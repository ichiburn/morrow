"""Verdict states, findings, and the three pure decision functions.

The pipeline (evidence.md §4.1) is deliberately split so evidence errors are not
smuggled through ``FrictionMetrics``:

    validate_experiment(raw, policy)      -> ValidatedExperiment | EvidenceError
    evaluate_policy(experiment, policy)    -> Assessment
    enforce(mode, result, strict=...)      -> ExitResult

* ``validate_experiment`` is the fail-closed front door: NaN / Inf / negative /
  non-integral counts become ``EVIDENCE_INVALID``, a missing required component
  becomes ``EVIDENCE_INCOMPLETE``, and too few infra-valid pairs becomes
  ``INFRASTRUCTURE_ERROR``. All three are exit-2 in every mode.
* ``evaluate_policy`` applies the normative evaluation order (§4.5). The order
  is load-bearing: a candidate that failed every pair is ``ADAPTATION_REGRESSION``,
  decided *before* the FFR-pair check that would otherwise call it
  ``INCONCLUSIVE``.
* ``enforce`` is the single state×mode → exit-code table (§4.2). Evidence,
  infrastructure, trust-boundary and not-comparable states are exit 2 in every
  mode; only friction findings are advisory (0) under ``measure`` and blocking
  (1) under ``gate``. There is no path by which any of them reaches 0 — that is
  the structural absence of fail-open.

All three functions are pure: no I/O, clock, or randomness.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field

from morrow.domain.friction import (
    compute_component_frictions,
    exceeds_threshold,
    ffr_display,
    ffr_gate,
)
from morrow.domain.metrics import (
    ComponentName,
    PairMeasurement,
    RawPairMeasurement,
    ValidatedExperiment,
)
from morrow.domain.policy import Policy


class State(StrEnum):
    """Every verdict in the state machine (evidence.md §4.2).

    ``INVALID_RUN`` is intentionally absent: it is a run-level internal state
    (§3.4.1), not a mode verdict, and surfaces here as ``INFRASTRUCTURE_ERROR``.
    """

    # evidence / infrastructure / trust boundary / not comparable — exit 2 always
    EVIDENCE_INVALID = "EVIDENCE_INVALID"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    CASSETTE_CORRUPTED = "CASSETTE_CORRUPTED"
    UNTRUSTED_TARGET = "UNTRUSTED_TARGET"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    INVALID_EXPERIMENT = "INVALID_EXPERIMENT"
    INCONCLUSIVE = "INCONCLUSIVE"
    GATE_PRECONDITION_UNMET = "GATE_PRECONDITION_UNMET"
    # friction findings — advisory under measure, blocking under gate
    REGRESSION = "REGRESSION"
    ADAPTATION_REGRESSION = "ADAPTATION_REGRESSION"
    FRICTION_REGRESSION = "FRICTION_REGRESSION"
    SINGLE_AXIS_REGRESSION = "SINGLE_AXIS_REGRESSION"
    # passing states
    DEGRADED_DATA = "DEGRADED_DATA"
    OK = "OK"
    # verify-only states
    EVIDENCE_REPRODUCED = "EVIDENCE_REPRODUCED"
    EVIDENCE_STALE = "EVIDENCE_STALE"


class Mode(StrEnum):
    MEASURE = "measure"
    VERIFY = "verify"
    GATE = "gate"


class Severity(IntEnum):
    """Ordering used to pick the primary verdict when findings co-occur (§4.3).
    Higher is worse."""

    OK = 0
    DEGRADED = 1
    FRICTION = 2
    INCONCLUSIVE = 3
    ERROR = 4


SEVERITY: dict[State, Severity] = {
    State.OK: Severity.OK,
    State.EVIDENCE_REPRODUCED: Severity.OK,
    State.DEGRADED_DATA: Severity.DEGRADED,
    State.REGRESSION: Severity.FRICTION,
    State.ADAPTATION_REGRESSION: Severity.FRICTION,
    State.FRICTION_REGRESSION: Severity.FRICTION,
    State.SINGLE_AXIS_REGRESSION: Severity.FRICTION,
    State.INCONCLUSIVE: Severity.INCONCLUSIVE,
    State.EVIDENCE_INVALID: Severity.ERROR,
    State.EVIDENCE_INCOMPLETE: Severity.ERROR,
    State.CASSETTE_CORRUPTED: Severity.ERROR,
    State.UNTRUSTED_TARGET: Severity.ERROR,
    State.INFRASTRUCTURE_ERROR: Severity.ERROR,
    State.INVALID_EXPERIMENT: Severity.ERROR,
    State.GATE_PRECONDITION_UNMET: Severity.ERROR,
    State.EVIDENCE_STALE: Severity.ERROR,
}

# The four friction findings, in the display order used for ``primary_reason``.
# This order is independent of how the exit code is decided (§4.3): all four map
# to the same code within a mode, so it only picks which one is reported first.
FRICTION_STATES: frozenset[State] = frozenset(
    {
        State.REGRESSION,
        State.ADAPTATION_REGRESSION,
        State.FRICTION_REGRESSION,
        State.SINGLE_AXIS_REGRESSION,
    }
)
_FRICTION_DISPLAY_ORDER: tuple[State, ...] = (
    State.ADAPTATION_REGRESSION,
    State.REGRESSION,
    State.FRICTION_REGRESSION,
    State.SINGLE_AXIS_REGRESSION,
)


class Finding(BaseModel):
    """One detected condition. Severity is derived from the state, never stored
    twice."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: State
    detail: str = ""

    @property
    def severity(self) -> Severity:
        return SEVERITY[self.state]


class EvidenceError(BaseModel):
    """A short-circuit result from ``validate_experiment`` (or, upstream, from
    trust-boundary / cassette checks). Carries the state ``enforce`` maps to an
    exit code, plus a human detail for the report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: State
    detail: str = ""


class Assessment(BaseModel):
    """The verdict of ``evaluate_policy`` together with the numbers behind it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: State
    findings: tuple[Finding, ...] = ()
    ffr_gate: float | None = None
    ffr_display: float | None = None
    component_ratios: dict[ComponentName, float] = Field(default_factory=dict)
    pair_ratios: dict[ComponentName, tuple[float, ...]] = Field(default_factory=dict)
    dropped_components: frozenset[ComponentName] = frozenset()
    degraded: bool = False
    valid_pair_count: int = 0
    successful_pair_count: int = 0
    baseline_success_count: int = 0
    candidate_success_count: int = 0

    @property
    def severity(self) -> Severity:
        return SEVERITY[self.state]


class ExitResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: Mode
    state: State
    exit_code: int
    advisory: bool
    strict: bool


# State × mode → exit code (evidence.md §4.2), as (measure, verify, gate).
# A cell the spec marks "—" (a state that mode never emits) is set to 2: an
# impossible combination fails closed, never to 0. This is what makes the table
# safe to apply blindly.
_FAIL_CLOSED = 2
_EXIT_TABLE: dict[State, tuple[int, int, int]] = {
    State.EVIDENCE_INVALID: (2, 2, 2),
    State.EVIDENCE_INCOMPLETE: (2, 2, 2),
    State.CASSETTE_CORRUPTED: (2, 2, 2),
    State.UNTRUSTED_TARGET: (2, 2, 2),
    State.INFRASTRUCTURE_ERROR: (2, 2, 2),
    State.INVALID_EXPERIMENT: (2, 2, 2),
    State.INCONCLUSIVE: (2, 2, 2),
    State.GATE_PRECONDITION_UNMET: (2, 2, 2),  # measure/verify never emit it
    State.REGRESSION: (0, 2, 1),  # advisory / — / block
    State.ADAPTATION_REGRESSION: (0, 2, 1),
    State.FRICTION_REGRESSION: (0, 2, 1),
    State.SINGLE_AXIS_REGRESSION: (0, 2, 1),
    State.DEGRADED_DATA: (0, 0, 0),  # --strict promotes to 1 below
    State.OK: (0, 0, 0),
    State.EVIDENCE_REPRODUCED: (2, 0, 2),  # verify only
    State.EVIDENCE_STALE: (2, 2, 2),  # verify only; still fail-closed elsewhere
}
_MODE_INDEX: dict[Mode, int] = {Mode.MEASURE: 0, Mode.VERIFY: 1, Mode.GATE: 2}


def enforce(mode: Mode, result: EvidenceError | Assessment, *, strict: bool = False) -> ExitResult:
    """Map ``(mode, result.state)`` to an exit code via the §4.2 table.

    ``--strict`` promotes ``DEGRADED_DATA`` from 0 to 1 in whichever mode would
    otherwise pass it; nothing else changes. An unknown state (there is none in
    ``State``, but defensively) fails closed to 2.
    """
    state = result.state
    codes = _EXIT_TABLE.get(state)
    code = _FAIL_CLOSED if codes is None else codes[_MODE_INDEX[mode]]
    if strict and state is State.DEGRADED_DATA and code == 0:
        code = 1
    advisory = state in FRICTION_STATES and code == 0
    return ExitResult(mode=mode, state=state, exit_code=code, advisory=advisory, strict=strict)


def validate_experiment(
    raw_pairs: Sequence[RawPairMeasurement], policy: Policy
) -> ValidatedExperiment | EvidenceError:
    """Fail-closed front door: raw measurements -> validated experiment or error.

    Order of rejection:
      1. missing required component on either side -> ``EVIDENCE_INCOMPLETE``
      2. NaN / Inf / negative / non-integral count -> ``EVIDENCE_INVALID``
      3. fewer infra-valid pairs than ``minimum_valid_pairs`` -> ``INFRASTRUCTURE_ERROR``

    The required component set is exactly ``policy.metrics.weights`` — the
    component set is fixed in policy (§3.3).
    """
    required = frozenset(policy.metrics.weights)
    validated: list[PairMeasurement] = []
    for raw in raw_pairs:
        for side in (raw.baseline, raw.candidate):
            if not required.issubset(side.keys()):
                missing = sorted(required - set(side.keys()))
                return EvidenceError(
                    state=State.EVIDENCE_INCOMPLETE,
                    detail=f"pair {raw.pair_id}: missing component(s) {missing}",
                )
            for name, value in side.items():
                # isfinite is checked first so math.floor is never handed a NaN.
                if not math.isfinite(value) or value < 0 or value != math.floor(value):
                    return EvidenceError(
                        state=State.EVIDENCE_INVALID,
                        detail=f"pair {raw.pair_id}: component {name} has invalid count {value!r}",
                    )
        validated.append(
            PairMeasurement(
                pair_id=raw.pair_id,
                baseline_success=raw.baseline_success,
                candidate_success=raw.candidate_success,
                regression_detected=raw.regression_detected,
                baseline={name: int(value) for name, value in raw.baseline.items()},
                candidate={name: int(value) for name, value in raw.candidate.items()},
            )
        )

    if len(validated) < policy.experiment.minimum_valid_pairs:
        return EvidenceError(
            state=State.INFRASTRUCTURE_ERROR,
            detail=(
                f"only {len(validated)} valid pair(s); "
                f"need {policy.experiment.minimum_valid_pairs}"
            ),
        )
    return ValidatedExperiment(pairs=tuple(validated))


def check_null_control(null_ffr_gate: float, policy: Policy) -> EvidenceError | None:
    """Reject the day's experiments if the null control did not stay within the
    published tolerance band (§3.8): a null above ``maximum_ffr`` means the
    instrument could not separate signal from noise, so the treatment result is
    ``INVALID_EXPERIMENT`` — the threshold is *not* loosened to pass."""
    if exceeds_threshold(null_ffr_gate, policy.null_control.maximum_ffr, policy.numeric.epsilon):
        return EvidenceError(
            state=State.INVALID_EXPERIMENT,
            detail=(
                f"null control FFR_gate {null_ffr_gate} exceeds "
                f"maximum_ffr {policy.null_control.maximum_ffr}"
            ),
        )
    return None


def _primary_state(findings: Sequence[Finding]) -> State:
    """Pick the reported verdict: highest severity, ties among friction findings
    broken by the fixed display order (§4.3)."""
    if not findings:
        return State.OK
    worst = max(f.severity for f in findings)
    at_worst = {f.state for f in findings if f.severity == worst}
    if worst is Severity.FRICTION:
        for candidate in _FRICTION_DISPLAY_ORDER:
            if candidate in at_worst:
                return candidate
    # DEGRADED (or any single-state tier) — return the one present.
    return next(iter(at_worst))


def _terminal(
    state: State, experiment: ValidatedExperiment, detail: str, **extra: object
) -> Assessment:
    """Build an Assessment for a step-2..5 short-circuit, carrying the counts the
    report needs. ``extra`` is limited to ``degraded`` / ``dropped_components``."""
    successful = experiment.successful_pairs
    degraded = bool(extra.get("degraded", False))
    dropped = extra.get("dropped_components", frozenset())
    assert isinstance(dropped, frozenset)
    return Assessment(
        state=state,
        findings=(Finding(state=state, detail=detail),),
        dropped_components=dropped,
        degraded=degraded,
        valid_pair_count=len(experiment.pairs),
        successful_pair_count=len(successful),
        baseline_success_count=experiment.baseline_success_count,
        candidate_success_count=experiment.candidate_success_count,
    )


def evaluate_policy(experiment: ValidatedExperiment, policy: Policy) -> Assessment:
    """Apply the normative evaluation order (§4.5). The first matching rule wins.

    Steps 1 (infra/evidence/trust) are handled upstream in
    ``validate_experiment``; this function starts at step 2.
    """
    baseline_successes = experiment.baseline_success_count
    candidate_successes = experiment.candidate_success_count
    successful = experiment.successful_pairs

    # Step 2 — baseline not established.
    if baseline_successes < policy.experiment.minimum_baseline_successes:
        return _terminal(
            State.INCONCLUSIVE,
            experiment,
            f"baseline not established: {baseline_successes} success(es) < "
            f"{policy.experiment.minimum_baseline_successes}",
        )

    # Step 3 — candidate failed every pair. Evaluated BEFORE the FFR-pair check
    # so it cannot be masked as INCONCLUSIVE (§4.5).
    if candidate_successes == 0:
        return _terminal(
            State.ADAPTATION_REGRESSION,
            experiment,
            "candidate failed every valid pair while baseline was established",
        )

    # Step 4 — too few successful pairs to report an FFR.
    if len(successful) < policy.experiment.minimum_ffr_pairs:
        return _terminal(
            State.INCONCLUSIVE,
            experiment,
            f"only {len(successful)} successful pair(s) < "
            f"{policy.experiment.minimum_ffr_pairs} for FFR",
        )

    # Step 5 — FFR and per-component checks over the successful pairs.
    frictions, dropped = compute_component_frictions(
        successful,
        frozenset(policy.metrics.weights),
        alpha=policy.metrics.alpha,
        clamp_ratio=policy.metrics.clamp_ratio,
        small_sample_floor=policy.metrics.small_sample_floor,
    )
    if not frictions:
        # Every component was small-sample everywhere: nothing comparable.
        return _terminal(
            State.INCONCLUSIVE,
            experiment,
            "all components dropped as small-sample",
            degraded=True,
            dropped_components=dropped,
        )

    ratios = {f.name: f.ratio for f in frictions}
    pair_ratios = {f.name: f.pair_ratios for f in frictions}
    # Renormalize the weights over the surviving components (§3.9). The surviving
    # weight sum is positive because ``frictions`` is non-empty and every weight
    # is > 0 by policy validation.
    weights = {f.name: policy.metrics.weights[f.name] for f in frictions}
    gate = ffr_gate(ratios, weights)
    display = ffr_display(ratios, weights)
    epsilon = policy.numeric.epsilon

    findings: list[Finding] = []
    if any(pair.regression_detected for pair in experiment.pairs):
        findings.append(
            Finding(state=State.REGRESSION, detail="a regression test failed at post")
        )
    if exceeds_threshold(gate, policy.decision.friction_threshold, epsilon):
        findings.append(
            Finding(
                state=State.FRICTION_REGRESSION,
                detail=f"FFR_gate {gate} > threshold {policy.decision.friction_threshold}",
            )
        )
    over_axis = sorted(
        name
        for name, ratio in ratios.items()
        if exceeds_threshold(ratio, policy.decision.component_hard_max, epsilon)
    )
    if over_axis:
        hard_max = policy.decision.component_hard_max
        findings.append(
            Finding(
                state=State.SINGLE_AXIS_REGRESSION,
                detail=f"components over hard_max {hard_max}: {over_axis}",
            )
        )

    degraded = bool(dropped)
    if degraded:
        findings.append(
            Finding(state=State.DEGRADED_DATA, detail=f"dropped small-sample: {sorted(dropped)}")
        )

    verdict = _primary_state(findings)
    return Assessment(
        state=verdict,
        findings=tuple(findings),
        ffr_gate=gate,
        ffr_display=display,
        component_ratios=ratios,
        pair_ratios=pair_ratios,
        dropped_components=dropped,
        degraded=degraded,
        valid_pair_count=len(experiment.pairs),
        successful_pair_count=len(successful),
        baseline_success_count=baseline_successes,
        candidate_success_count=candidate_successes,
    )
