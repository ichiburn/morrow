"""Friction math: clamping, pair-then-median ordering, one-sided aggregation,
small-sample dropping, and log-space threshold boundaries (measurement.md §3.1,
§3.9)."""

from __future__ import annotations

import math

import pytest

from morrow.domain.friction import (
    ComponentFriction,
    component_ratio,
    compute_component_frictions,
    exceeds_threshold,
    ffr_display,
    ffr_gate,
)
from morrow.domain.metrics import ComponentName, PairMeasurement

A = ComponentName.FILES_READ_DISTINCT
B = ComponentName.TEST_CYCLES
C = ComponentName.FINAL_CHURN

ALPHA = 1.0
CLAMP = 10.0


def _pair(pair_id: int, baseline: dict, candidate: dict) -> PairMeasurement:
    return PairMeasurement(
        pair_id=pair_id,
        baseline_success=True,
        candidate_success=True,
        baseline=baseline,
        candidate=candidate,
    )


class TestComponentRatio:
    def test_clamps_to_upper_bound_r(self) -> None:
        # (1000 + 1) / (0 + 1) = 1001, clamped to R = 10.
        assert component_ratio(0, 1000, alpha=ALPHA, clamp_ratio=CLAMP) == 10.0

    def test_clamps_to_lower_bound_one_over_r(self) -> None:
        # (0 + 1) / (1000 + 1) ≈ 0.001, clamped to 1/R = 0.1.
        assert component_ratio(1000, 0, alpha=ALPHA, clamp_ratio=CLAMP) == pytest.approx(0.1)

    def test_alpha_smooths_the_zero_baseline(self) -> None:
        # Without alpha this would divide by zero; with alpha it is (5+1)/(0+1)=6.
        assert component_ratio(0, 5, alpha=ALPHA, clamp_ratio=CLAMP) == pytest.approx(6.0)


class TestOneSidedAggregation:
    def test_gate_does_not_cancel_opposing_axes(self) -> None:
        # r = [10.0, 0.1]: the one-sided gate rounds 0.1 up to 1, so the 10x
        # regression is NOT offset by the 10x improvement.
        ratios = {A: 10.0, B: 0.1}
        weights = {A: 1.0, B: 1.0}
        gate = ffr_gate(ratios, weights)
        assert gate > 1.0
        assert gate == pytest.approx(math.sqrt(10.0))  # exp((ln10 + ln1)/2)

    def test_display_is_two_sided_and_cancels(self) -> None:
        # The human-facing number keeps both sides, so 10x and 1/10x net to 1.
        ratios = {A: 10.0, B: 0.1}
        weights = {A: 1.0, B: 1.0}
        assert ffr_display(ratios, weights) == pytest.approx(1.0)

    def test_gate_and_display_diverge_on_the_same_input(self) -> None:
        ratios = {A: 10.0, B: 0.1}
        weights = {A: 1.0, B: 1.0}
        assert ffr_gate(ratios, weights) != pytest.approx(ffr_display(ratios, weights))


class TestPairThenMedian:
    def test_pair_ratio_then_median_differs_from_variant_medians_then_ratio(self) -> None:
        # Baseline/candidate counts arranged so the two orders of operation give
        # different answers. This is the whole point of pairing (§3.1).
        pairs = [
            _pair(0, {A: 1}, {A: 10}),
            _pair(1, {A: 10}, {A: 1}),
            _pair(2, {A: 1}, {A: 10}),
            _pair(3, {A: 10}, {A: 1}),
        ]
        frictions, dropped = compute_component_frictions(
            pairs, {A}, alpha=ALPHA, clamp_ratio=CLAMP, small_sample_floor=3
        )
        assert dropped == frozenset()
        (friction,) = frictions

        # Correct order: ratio within each pair, then median of the four ratios.
        # pairs 0,2 -> (10+1)/(1+1) = 5.5 ; pairs 1,3 -> (1+1)/(10+1) = 2/11.
        expected = ((11 / 2) + (2 / 11)) / 2  # median of [2/11, 2/11, 5.5, 5.5]
        assert friction.ratio == pytest.approx(expected)
        assert friction.ratio == pytest.approx(2.840909, abs=1e-5)

        # Wrong order (median per variant, then ratio) would give exactly 1.0,
        # because both variants have per-variant median 5.5.
        baseline_median = 5.5
        candidate_median = 5.5
        variant_first = (candidate_median + ALPHA) / (baseline_median + ALPHA)
        assert variant_first == pytest.approx(1.0)
        assert friction.ratio != pytest.approx(variant_first)

    def test_every_pair_ratio_is_retained(self) -> None:
        # The report shows all r[i,p], not just the median (§3.1).
        pairs = [
            _pair(0, {A: 1}, {A: 10}),
            _pair(1, {A: 10}, {A: 1}),
            _pair(2, {A: 4}, {A: 8}),
            _pair(3, {A: 8}, {A: 4}),
        ]
        (friction,), _ = compute_component_frictions(
            pairs, {A}, alpha=ALPHA, clamp_ratio=CLAMP, small_sample_floor=3
        )
        assert len(friction.pair_ratios) == 4


class TestSmallSample:
    def test_pair_below_floor_on_both_sides_is_skipped(self) -> None:
        # One pair is small-sample (b=1, c=2 < floor 3) and contributes no ratio;
        # the other three do.
        pairs = [
            _pair(0, {A: 1}, {A: 2}),  # small-sample -> skipped
            _pair(1, {A: 5}, {A: 10}),
            _pair(2, {A: 5}, {A: 10}),
            _pair(3, {A: 5}, {A: 10}),
        ]
        (friction,), dropped = compute_component_frictions(
            pairs, {A}, alpha=ALPHA, clamp_ratio=CLAMP, small_sample_floor=3
        )
        assert dropped == frozenset()
        assert len(friction.pair_ratios) == 3

    def test_component_small_sample_everywhere_is_dropped(self) -> None:
        pairs = [
            _pair(0, {A: 1}, {A: 2}),
            _pair(1, {A: 2}, {A: 1}),
            _pair(2, {A: 0}, {A: 2}),
        ]
        frictions, dropped = compute_component_frictions(
            pairs, {A}, alpha=ALPHA, clamp_ratio=CLAMP, small_sample_floor=3
        )
        assert frictions == []
        assert dropped == frozenset({A})

    def test_floor_boundary_value_is_not_small_sample(self) -> None:
        # b = c = 3 is NOT below floor 3, so the pair counts.
        pairs = [_pair(0, {A: 3}, {A: 3})]
        (friction,), dropped = compute_component_frictions(
            pairs, {A}, alpha=ALPHA, clamp_ratio=CLAMP, small_sample_floor=3
        )
        assert dropped == frozenset()
        assert len(friction.pair_ratios) == 1


class TestThresholdBoundary:
    def test_exactly_at_threshold_is_pass(self) -> None:
        # ln(value) > ln(threshold) + eps is False when value == threshold.
        assert exceeds_threshold(1.5, 1.5, 1e-9) is False

    def test_just_above_threshold_exceeds(self) -> None:
        assert exceeds_threshold(1.5 + 1e-3, 1.5, 1e-9) is True

    def test_within_epsilon_above_still_passes(self) -> None:
        # A hair over the threshold, inside epsilon, is still a PASS.
        assert exceeds_threshold(math.exp(math.log(1.5) + 1e-11), 1.5, 1e-9) is False

    def test_no_cancel_gate_crosses_threshold(self) -> None:
        gate = ffr_gate({A: 10.0, B: 0.1}, {A: 1.0, B: 1.0})
        assert exceeds_threshold(gate, 1.5, 1e-9) is True


def test_component_friction_is_immutable() -> None:
    friction = ComponentFriction(name=A, pair_ratios=(1.0,), ratio=1.0)
    with pytest.raises((AttributeError, TypeError)):
        friction.ratio = 2.0  # type: ignore[misc]
