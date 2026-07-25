"""Friction computation — the pure math behind FFR (measurement.md §3.1, §3.9).

Every function here is total and side-effect free. The load-bearing choices,
each of which a prior revision got wrong:

* **Pair the ratio, then take the median.** ``r[i,p]`` is a ratio *inside* one
  pair; ``r[i]`` is the median of those per-pair ratios — never the ratio of
  per-variant medians, which would launder time drift into the treatment
  (§3.1). :func:`compute_component_frictions` does it in that order.
* **One-sided vs two-sided aggregation are different functions.**
  :func:`ffr_gate` rounds each ``r[i]`` up to ``max(1, r[i])`` so an improvement
  on one axis can never cancel a regression on another (§3.1). :func:`ffr_display`
  keeps both sides for the human-facing number. ``r = [10.0, 0.1]`` therefore
  gives ``FFR_gate = sqrt(10) > 1`` while ``FFR_display = 1``.
* **Threshold comparisons happen in log space with Decimal + epsilon**, so
  "exactly at the threshold" is a reproducible PASS across machines (§3.9).
* **Small-sample pairs are dropped, not zeroed.** A component whose every
  surviving pair is small-sample is returned in the dropped set; the caller
  renormalizes the weights over what is left.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from morrow.domain.metrics import ComponentName, PairMeasurement


@dataclass(frozen=True)
class ComponentFriction:
    """The friction of one surviving component over the successful pairs.

    ``pair_ratios`` retains every ``r[i,p]`` that was actually computed (the
    report shows them all — §3.1); ``ratio`` is their median ``r[i]``.
    """

    name: ComponentName
    pair_ratios: tuple[float, ...]
    ratio: float


def component_ratio(
    baseline: float, candidate: float, *, alpha: float, clamp_ratio: float
) -> float:
    """``r[i,p] = clamp((c + alpha) / (b + alpha), 1/R, R)`` (§3.1)."""
    ratio = (candidate + alpha) / (baseline + alpha)
    return min(max(ratio, 1.0 / clamp_ratio), clamp_ratio)


def is_small_sample(baseline: int, candidate: int, *, floor: int) -> bool:
    """A pair is small-sample for a component when *both* sides fall below the
    floor (§3.9); its ratio is then not computed for that component."""
    return baseline < floor and candidate < floor


def median_ratio(ratios: Sequence[float]) -> float:
    """Median of the per-pair ratios. Even K averages the two middle values,
    which is why K is kept even (§3.1)."""
    return statistics.median(ratios)


def compute_component_frictions(
    successful_pairs: Sequence[PairMeasurement],
    components: Collection[ComponentName],
    *,
    alpha: float,
    clamp_ratio: float,
    small_sample_floor: int,
) -> tuple[list[ComponentFriction], frozenset[ComponentName]]:
    """Return the surviving component frictions and the set that was dropped.

    Iterates components in sorted order for determinism. For each component it
    collects ``r[i,p]`` over the successful pairs, skipping any pair that is
    small-sample for that component. A component with no computable ratio is
    dropped (small-sample everywhere). The caller decides ``DEGRADED_DATA`` vs
    ``INCONCLUSIVE`` from whether the surviving list is non-empty.
    """
    surviving: list[ComponentFriction] = []
    dropped: set[ComponentName] = set()
    for name in sorted(components):
        ratios: list[float] = []
        for pair in successful_pairs:
            baseline = pair.baseline[name]
            candidate = pair.candidate[name]
            if is_small_sample(baseline, candidate, floor=small_sample_floor):
                continue
            ratios.append(
                component_ratio(baseline, candidate, alpha=alpha, clamp_ratio=clamp_ratio)
            )
        if not ratios:
            dropped.add(name)
            continue
        surviving.append(
            ComponentFriction(name=name, pair_ratios=tuple(ratios), ratio=median_ratio(ratios))
        )
    return surviving, frozenset(dropped)


def _log_weighted_mean(
    ratios: Mapping[ComponentName, float],
    weights: Mapping[ComponentName, float],
    *,
    one_sided: bool,
) -> float:
    """``Σ wᵢ · ln(val(rᵢ)) / Σ wᵢ`` where ``val`` is ``max(1, rᵢ)`` one-sided.

    The caller guarantees a non-empty ``ratios`` drawn from surviving components,
    so ``Σ wᵢ > 0``; the guard makes that impossible-by-construction case
    fail-closed rather than divide by zero.
    """
    numerator = 0.0
    denominator = 0.0
    for name, ratio in ratios.items():
        weight = weights[name]
        value = max(1.0, ratio) if one_sided else ratio
        numerator += weight * math.log(value)
        denominator += weight
    if denominator <= 0:
        raise ValueError("weight sum over surviving components must be positive")
    return numerator / denominator


def ffr_gate(
    ratios: Mapping[ComponentName, float], weights: Mapping[ComponentName, float]
) -> float:
    """One-sided FFR used for the gate: improvements are rounded to 1 so they
    cannot offset a regression (§3.1)."""
    return math.exp(_log_weighted_mean(ratios, weights, one_sided=True))


def ffr_display(
    ratios: Mapping[ComponentName, float], weights: Mapping[ComponentName, float]
) -> float:
    """Two-sided FFR for the report only. Never feeds the gate decision."""
    return math.exp(_log_weighted_mean(ratios, weights, one_sided=False))


def exceeds_threshold(value: float, threshold: float, epsilon: float) -> bool:
    """``ln(value) > ln(threshold) + epsilon`` in Decimal (§3.9).

    Equality is a PASS: at exactly the threshold the strict ``>`` is False.
    ``repr`` yields the shortest round-tripping decimal string for each float,
    so the Decimal comparison reflects the actual IEEE-754 values plus epsilon.
    Both arguments are strictly positive by construction (an FFR is ``exp(...)``;
    a ratio is clamped into ``[1/R, R]`` with ``R >= 1``; a threshold is > 1).
    """
    left = Decimal(repr(math.log(value)))
    right = Decimal(repr(math.log(threshold))) + Decimal(repr(epsilon))
    return left > right
