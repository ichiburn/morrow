"""Decision layer: the state×mode exit table, the structural absence of
fail-open, fail-closed input validation, and the normative evaluation order
(evidence.md §4.2, §4.5)."""

from __future__ import annotations

import math

import pytest

from morrow.domain.assessment import (
    Assessment,
    EvidenceError,
    Mode,
    State,
    check_null_control,
    enforce,
    evaluate_policy,
    validate_experiment,
)
from morrow.domain.metrics import (
    ComponentName,
    PairMeasurement,
    RawPairMeasurement,
    ValidatedExperiment,
)
from morrow.domain.policy import default_policy

A = ComponentName.FILES_READ_DISTINCT
B = ComponentName.TEST_CYCLES
C = ComponentName.FINAL_CHURN
POLICY = default_policy()

# Independent transcription of the exit table (evidence.md §4.2), as
# (measure, verify, gate). "—" cells in the doc are 2 here: a mode that never
# emits the state must still fail closed, never to 0.
EXPECTED_EXIT: dict[State, tuple[int, int, int]] = {
    State.EVIDENCE_INVALID: (2, 2, 2),
    State.EVIDENCE_INCOMPLETE: (2, 2, 2),
    State.CASSETTE_CORRUPTED: (2, 2, 2),
    State.UNTRUSTED_TARGET: (2, 2, 2),
    State.INFRASTRUCTURE_ERROR: (2, 2, 2),
    State.INVALID_EXPERIMENT: (2, 2, 2),
    State.INCONCLUSIVE: (2, 2, 2),
    State.GATE_PRECONDITION_UNMET: (2, 2, 2),
    State.REGRESSION: (0, 2, 1),
    State.ADAPTATION_REGRESSION: (0, 2, 1),
    State.FRICTION_REGRESSION: (0, 2, 1),
    State.SINGLE_AXIS_REGRESSION: (0, 2, 1),
    State.DEGRADED_DATA: (0, 0, 0),
    State.OK: (0, 0, 0),
    State.EVIDENCE_REPRODUCED: (2, 0, 2),
    State.EVIDENCE_STALE: (2, 2, 2),
}
_VERDICT_STATES = frozenset(
    {
        State.OK,
        State.DEGRADED_DATA,
        State.INCONCLUSIVE,
        State.REGRESSION,
        State.ADAPTATION_REGRESSION,
        State.FRICTION_REGRESSION,
        State.SINGLE_AXIS_REGRESSION,
    }
)
_ALWAYS_EXIT_2 = frozenset(
    {
        State.EVIDENCE_INVALID,
        State.EVIDENCE_INCOMPLETE,
        State.CASSETTE_CORRUPTED,
        State.UNTRUSTED_TARGET,
        State.INFRASTRUCTURE_ERROR,
        State.INVALID_EXPERIMENT,
        State.INCONCLUSIVE,
    }
)
_MODE_ORDER = (Mode.MEASURE, Mode.VERIFY, Mode.GATE)


def _result(state: State) -> EvidenceError | Assessment:
    """Wrap a state in whichever container the pipeline would produce for it."""
    if state in _VERDICT_STATES:
        return Assessment(state=state)
    return EvidenceError(state=state)


def _counts(a: int, b: int, c: int) -> dict[ComponentName, int]:
    return {A: a, B: b, C: c}


def _pair(
    pair_id: int,
    *,
    baseline_success: bool,
    candidate_success: bool,
    baseline: dict[ComponentName, int],
    candidate: dict[ComponentName, int],
    regression: bool = False,
) -> PairMeasurement:
    return PairMeasurement(
        pair_id=pair_id,
        baseline_success=baseline_success,
        candidate_success=candidate_success,
        regression_detected=regression,
        baseline=baseline,
        candidate=candidate,
    )


def _experiment(pairs: list[PairMeasurement]) -> ValidatedExperiment:
    return ValidatedExperiment(pairs=tuple(pairs))


# --------------------------------------------------------------------------- #
# enforce: the full state × mode table
# --------------------------------------------------------------------------- #
class TestEnforceTable:
    def test_every_state_maps_every_mode_to_the_documented_exit_code(self) -> None:
        # Covers all 16 states × 3 modes against an independent transcription.
        assert set(EXPECTED_EXIT) == set(State), "table is missing a state"
        for state, expected in EXPECTED_EXIT.items():
            result = _result(state)
            for mode, want in zip(_MODE_ORDER, expected, strict=True):
                got = enforce(mode, result).exit_code
                assert got == want, f"{state} under {mode}: expected {want}, got {got}"

    def test_no_state_ever_reaches_a_nonstandard_exit_code(self) -> None:
        for state in State:
            for mode in _MODE_ORDER:
                assert enforce(mode, _result(state)).exit_code in (0, 1, 2)

    def test_strict_promotes_degraded_data_to_one_in_every_mode(self) -> None:
        result = Assessment(state=State.DEGRADED_DATA)
        for mode in _MODE_ORDER:
            assert enforce(mode, result, strict=True).exit_code == 1
            assert enforce(mode, result, strict=False).exit_code == 0

    def test_strict_does_not_change_ok_or_findings(self) -> None:
        assert enforce(Mode.GATE, Assessment(state=State.OK), strict=True).exit_code == 0
        friction = Assessment(state=State.FRICTION_REGRESSION)
        assert enforce(Mode.GATE, friction, strict=True).exit_code == 1

    def test_friction_finding_is_advisory_under_measure_and_blocking_under_gate(self) -> None:
        friction = Assessment(state=State.FRICTION_REGRESSION)
        measured = enforce(Mode.MEASURE, friction)
        gated = enforce(Mode.GATE, friction)
        assert measured.exit_code == 0 and measured.advisory is True
        assert gated.exit_code == 1 and gated.advisory is False


# --------------------------------------------------------------------------- #
# Absence of fail-open
# --------------------------------------------------------------------------- #
class TestNoFailOpen:
    def test_evidence_infra_trust_incomparable_errors_exit_two_in_every_mode(self) -> None:
        for state in _ALWAYS_EXIT_2:
            for mode in _MODE_ORDER:
                assert enforce(mode, _result(state)).exit_code == 2, f"{state}/{mode} not 2"

    def test_no_error_state_is_ever_advisory(self) -> None:
        for state in _ALWAYS_EXIT_2:
            for mode in _MODE_ORDER:
                assert enforce(mode, _result(state)).advisory is False


# --------------------------------------------------------------------------- #
# validate_experiment: fail-closed input validation
# --------------------------------------------------------------------------- #
class TestValidateExperiment:
    def _raw(
        self,
        pair_id: int,
        baseline: dict[ComponentName, float],
        candidate: dict[ComponentName, float] | None = None,
    ) -> RawPairMeasurement:
        return RawPairMeasurement(
            pair_id=pair_id,
            baseline_success=True,
            candidate_success=True,
            baseline=baseline,
            candidate=candidate if candidate is not None else dict(baseline),
        )

    def _valid_raw(self, pair_id: int) -> RawPairMeasurement:
        return self._raw(pair_id, {A: 5.0, B: 5.0, C: 5.0})

    def test_nan_count_is_evidence_invalid(self) -> None:
        raw = [self._raw(0, {A: float("nan"), B: 1.0, C: 1.0})]
        result = validate_experiment(raw, POLICY)
        assert isinstance(result, EvidenceError)
        assert result.state is State.EVIDENCE_INVALID

    def test_inf_count_is_evidence_invalid(self) -> None:
        raw = [self._raw(0, {A: float("inf"), B: 1.0, C: 1.0})]
        result = validate_experiment(raw, POLICY)
        assert isinstance(result, EvidenceError)
        assert result.state is State.EVIDENCE_INVALID

    def test_negative_count_is_evidence_invalid(self) -> None:
        raw = [self._raw(0, {A: -1.0, B: 1.0, C: 1.0})]
        result = validate_experiment(raw, POLICY)
        assert isinstance(result, EvidenceError)
        assert result.state is State.EVIDENCE_INVALID

    def test_non_integral_count_is_evidence_invalid(self) -> None:
        raw = [self._raw(0, {A: 3.5, B: 1.0, C: 1.0})]
        result = validate_experiment(raw, POLICY)
        assert isinstance(result, EvidenceError)
        assert result.state is State.EVIDENCE_INVALID

    def test_missing_component_is_evidence_incomplete(self) -> None:
        # C absent on the baseline side.
        raw = [self._raw(0, {A: 1.0, B: 1.0}, {A: 1.0, B: 1.0, C: 1.0})]
        result = validate_experiment(raw, POLICY)
        assert isinstance(result, EvidenceError)
        assert result.state is State.EVIDENCE_INCOMPLETE

    def test_too_few_valid_pairs_is_infrastructure_error(self) -> None:
        # 2 valid pairs, but minimum_valid_pairs is 3.
        raw = [self._valid_raw(0), self._valid_raw(1)]
        result = validate_experiment(raw, POLICY)
        assert isinstance(result, EvidenceError)
        assert result.state is State.INFRASTRUCTURE_ERROR

    def test_valid_input_produces_integer_backed_experiment(self) -> None:
        raw = [self._valid_raw(i) for i in range(3)]
        result = validate_experiment(raw, POLICY)
        assert isinstance(result, ValidatedExperiment)
        assert len(result.pairs) == 3
        assert all(isinstance(v, int) for v in result.pairs[0].baseline.values())

    def test_invalid_count_beats_the_valid_pair_count_check(self) -> None:
        # A single NaN pair short-circuits to EVIDENCE_INVALID even though the
        # count of pairs is below the minimum (order: content before quantity).
        raw = [self._raw(0, {A: float("nan"), B: 1.0, C: 1.0})]
        result = validate_experiment(raw, POLICY)
        assert isinstance(result, EvidenceError)
        assert result.state is State.EVIDENCE_INVALID


# --------------------------------------------------------------------------- #
# evaluate_policy: the normative order and each verdict
# --------------------------------------------------------------------------- #
class TestEvaluatePolicyOrder:
    def test_candidate_fails_every_pair_is_adaptation_regression_not_inconclusive(self) -> None:
        # baseline succeeds everywhere, candidate nowhere. Step 3 must win over
        # the FFR-pair check that would otherwise call this INCONCLUSIVE.
        pairs = [
            _pair(
                i,
                baseline_success=True,
                candidate_success=False,
                baseline=_counts(10, 10, 10),
                candidate=_counts(10, 10, 10),
            )
            for i in range(4)
        ]
        assessment = evaluate_policy(_experiment(pairs), POLICY)
        assert assessment.state is State.ADAPTATION_REGRESSION
        assert assessment.candidate_success_count == 0
        assert assessment.baseline_success_count == 4

    def test_baseline_not_established_is_inconclusive(self) -> None:
        pairs = [
            _pair(
                i,
                baseline_success=False,
                candidate_success=True,
                baseline=_counts(10, 10, 10),
                candidate=_counts(10, 10, 10),
            )
            for i in range(4)
        ]
        assessment = evaluate_policy(_experiment(pairs), POLICY)
        assert assessment.state is State.INCONCLUSIVE
        assert assessment.baseline_success_count < POLICY.experiment.minimum_baseline_successes

    def test_too_few_successful_pairs_is_inconclusive_when_baseline_holds(self) -> None:
        # baseline established (3), candidate has 1 success -> not adaptation, but
        # only 1 successful pair < minimum_ffr_pairs (3).
        pairs = [
            _pair(
                0,
                baseline_success=True,
                candidate_success=True,
                baseline=_counts(10, 10, 10),
                candidate=_counts(10, 10, 10),
            ),
            _pair(
                1,
                baseline_success=True,
                candidate_success=False,
                baseline=_counts(10, 10, 10),
                candidate=_counts(10, 10, 10),
            ),
            _pair(
                2,
                baseline_success=True,
                candidate_success=False,
                baseline=_counts(10, 10, 10),
                candidate=_counts(10, 10, 10),
            ),
        ]
        assessment = evaluate_policy(_experiment(pairs), POLICY)
        assert assessment.state is State.INCONCLUSIVE
        assert assessment.candidate_success_count == 1  # distinct from adaptation


def _successful(
    pair_id: int, base: tuple[int, int, int], regression: bool = False
) -> PairMeasurement:
    counts = _counts(*base)
    return _pair(
        pair_id,
        baseline_success=True,
        candidate_success=True,
        baseline=counts,
        candidate=counts,
        regression=regression,
    )


class TestEvaluatePolicyVerdicts:
    def test_ffr_gate_exactly_at_threshold_is_ok(self) -> None:
        # Each component: (5+1)/(3+1) = 1.5 per pair -> r[i] = 1.5 -> FFR_gate = 1.5.
        # 1.5 == friction_threshold, so PASS (log space + epsilon).
        pairs = [
            _pair(
                i,
                baseline_success=True,
                candidate_success=True,
                baseline=_counts(3, 3, 3),
                candidate=_counts(5, 5, 5),
            )
            for i in range(3)
        ]
        assessment = evaluate_policy(_experiment(pairs), POLICY)
        assert assessment.state is State.OK
        assert assessment.ffr_gate == pytest.approx(1.5)

    def test_uniform_elevation_is_friction_regression_without_single_axis(self) -> None:
        # (21+1)/(10+1) = 2.0 per component -> FFR_gate = 2.0 > 1.5, but 2.0 < 3.0
        # so no single-axis finding.
        pairs = [
            _pair(
                i,
                baseline_success=True,
                candidate_success=True,
                baseline=_counts(10, 10, 10),
                candidate=_counts(21, 21, 21),
            )
            for i in range(3)
        ]
        assessment = evaluate_policy(_experiment(pairs), POLICY)
        assert assessment.state is State.FRICTION_REGRESSION
        assert assessment.ffr_gate == pytest.approx(2.0)
        assert {f.state for f in assessment.findings} == {State.FRICTION_REGRESSION}

    def test_single_axis_regression_without_crossing_the_ffr_threshold(self) -> None:
        # Only churn is elevated: (302+1)/(99+1) = 3.03 > hard_max 3.0. The other
        # two ratios are 1.0, so FFR_gate = 3.03**(1/3) ≈ 1.447 < 1.5 (no friction).
        pairs = [
            _pair(
                i,
                baseline_success=True,
                candidate_success=True,
                baseline=_counts(100, 100, 99),
                candidate=_counts(100, 100, 302),
            )
            for i in range(3)
        ]
        assessment = evaluate_policy(_experiment(pairs), POLICY)
        assert assessment.state is State.SINGLE_AXIS_REGRESSION
        assert assessment.ffr_gate is not None
        assert assessment.ffr_gate < POLICY.decision.friction_threshold
        assert {f.state for f in assessment.findings} == {State.SINGLE_AXIS_REGRESSION}
        assert assessment.component_ratios[C] == pytest.approx(3.03)

    def test_regression_finding_outranks_friction_for_primary_reason(self) -> None:
        # Elevated (FFR_regression) AND a post regression test failure. Both are
        # collected; REGRESSION is reported first by the display order (§4.3).
        pairs = [
            _pair(
                i,
                baseline_success=True,
                candidate_success=True,
                baseline=_counts(10, 10, 10),
                candidate=_counts(21, 21, 21),
                regression=(i == 0),
            )
            for i in range(3)
        ]
        assessment = evaluate_policy(_experiment(pairs), POLICY)
        assert assessment.state is State.REGRESSION
        finding_states = {f.state for f in assessment.findings}
        assert State.REGRESSION in finding_states
        assert State.FRICTION_REGRESSION in finding_states

    def test_one_component_small_sample_degrades_and_renormalizes(self) -> None:
        # A is small-sample everywhere (dropped); B and C are ratio 1.0. No
        # friction, so the verdict is DEGRADED_DATA over the surviving weights.
        pairs = [
            _pair(
                i,
                baseline_success=True,
                candidate_success=True,
                baseline=_counts(1, 10, 10),
                candidate=_counts(2, 10, 10),
            )
            for i in range(3)
        ]
        assessment = evaluate_policy(_experiment(pairs), POLICY)
        assert assessment.state is State.DEGRADED_DATA
        assert assessment.degraded is True
        assert assessment.dropped_components == frozenset({A})
        assert A not in assessment.component_ratios

    def test_all_components_small_sample_is_inconclusive(self) -> None:
        pairs = [
            _pair(
                i,
                baseline_success=True,
                candidate_success=True,
                baseline=_counts(1, 1, 1),
                candidate=_counts(2, 2, 2),
            )
            for i in range(3)
        ]
        assessment = evaluate_policy(_experiment(pairs), POLICY)
        assert assessment.state is State.INCONCLUSIVE
        assert assessment.dropped_components == frozenset({A, B, C})

    def test_low_friction_all_components_is_ok(self) -> None:
        pairs = [_successful(i, (10, 10, 10)) for i in range(4)]
        assessment = evaluate_policy(_experiment(pairs), POLICY)
        assert assessment.state is State.OK
        assert assessment.ffr_gate == pytest.approx(1.0)
        assert assessment.findings == ()


# --------------------------------------------------------------------------- #
# Null control (measurement.md §3.8)
# --------------------------------------------------------------------------- #
class TestNullControl:
    def test_null_within_band_is_accepted(self) -> None:
        assert check_null_control(1.20, POLICY) is None

    def test_null_above_band_is_invalid_experiment(self) -> None:
        error = check_null_control(1.20 + 1e-3, POLICY)
        assert isinstance(error, EvidenceError)
        assert error.state is State.INVALID_EXPERIMENT

    def test_null_at_band_boundary_is_accepted(self) -> None:
        # Exactly at maximum_ffr passes, mirroring the threshold boundary rule.
        assert check_null_control(math.exp(math.log(1.20)), POLICY) is None


# --------------------------------------------------------------------------- #
# End-to-end through both pure functions
# --------------------------------------------------------------------------- #
def test_validate_then_evaluate_then_enforce_blocks_on_gate() -> None:
    raw = [
        RawPairMeasurement(
            pair_id=i,
            baseline_success=True,
            candidate_success=True,
            baseline={A: 10.0, B: 10.0, C: 10.0},
            candidate={A: 21.0, B: 21.0, C: 21.0},
        )
        for i in range(3)
    ]
    experiment = validate_experiment(raw, POLICY)
    assert isinstance(experiment, ValidatedExperiment)
    assessment = evaluate_policy(experiment, POLICY)
    assert assessment.state is State.FRICTION_REGRESSION
    assert enforce(Mode.GATE, assessment).exit_code == 1
    assert enforce(Mode.MEASURE, assessment).exit_code == 0
