"""The policy schema and its cross-field validation (measurement.md §3.9).

The schema is *closed*: every model forbids unknown keys, so a typo in a YAML
key fails loudly at startup rather than being silently ignored. The numeric
invariants that couple fields (``1 < friction_threshold <= component_hard_max
<= clamp_ratio`` and the pair-count bounds) are checked in a single
``model_validator`` so a policy either loads whole or not at all — fail-closed.

This module does no I/O. Turning ``policies/default.yaml`` bytes into a mapping
is the application layer's job; :func:`default_policy` only pins the in-code
default values so the domain and its tests share one source of truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from morrow.domain.metrics import ComponentName

#: A ratio or threshold. Strictly positive **and finite**, because every one of these is
#: eventually handed to ``math.log`` in a threshold comparison: zero and negatives raise
#: ``ValueError``, and NaN makes the Decimal comparison raise ``InvalidOperation``. Either
#: escapes as a traceback rather than as a verdict, which would turn a fail-closed gate
#: into an exit code nobody in the state table produces. ``float`` alone does not exclude
#: them — pydantic accepts NaN and ``json.loads`` parses the bare ``NaN`` token — so the
#: bound has to be stated.
FinitePositiveFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]

#: A path pattern for the churn walker. Path characters only — see ``churn_exclude``.
ExcludePattern = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.*/-]{1,64}$")]

_DEFAULT_CHURN_EXCLUDE: tuple[str, ...] = (
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".git/",
    "*.pyc",
)


class ExperimentPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runs_per_variant: PositiveInt = 4  # = pair count K
    minimum_valid_pairs: PositiveInt = 3
    minimum_ffr_pairs: PositiveInt = 3
    minimum_baseline_successes: PositiveInt = 2
    max_pair_retries: NonNegativeInt = 2


class MetricsPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alpha: FinitePositiveFloat = 1.0
    clamp_ratio: FinitePositiveFloat = 10.0  # R; validated >= 1 below
    small_sample_floor: PositiveInt = 3
    weights: Mapping[ComponentName, FinitePositiveFloat]
    #: Glob-ish path patterns. Bounded to path characters because the policy is embedded in
    #: every published cassette: an unconstrained string list here would be a channel for
    #: arbitrary prose to leave the trust boundary inside something labelled "config".
    churn_exclude: tuple[ExcludePattern, ...] = _DEFAULT_CHURN_EXCLUDE
    max_direct_test_invocations: NonNegativeInt = 0

    @field_validator("clamp_ratio")
    @classmethod
    def _clamp_at_least_one(cls, value: float) -> float:
        if value < 1:
            raise ValueError("clamp_ratio must be >= 1")
        return value

    @model_validator(mode="after")
    def _weights_present_and_positive(self) -> MetricsPolicy:
        if not self.weights:
            raise ValueError("weights must declare at least one component")
        # PositiveFloat already forbids <= 0 per weight; the sum follows.
        return self


class DecisionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    friction_threshold: FinitePositiveFloat = 1.50
    component_hard_max: FinitePositiveFloat = 3.00


class NullControlPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The band the null control must stay inside. Bounded like the other thresholds: it
    #: reaches ``exceeds_threshold`` on a path the treatment verdict depends on, and a
    #: zero or NaN here would crash the comparison instead of deciding it.
    maximum_ffr: FinitePositiveFloat = 1.20


class EvidencePolicy(BaseModel):
    """Caps on the data-quality defects a run may carry and still be counted.

    A tool call whose result never arrived leaves the event stream with an unconfirmed
    outcome. Zero is the default because such a call is a hole in the trajectory, and a
    hole is not the same as an absence — accepting them silently would let a run that lost
    half its evidence be scored as a cheap one (evidence.md §5).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_unpaired_tool_uses: NonNegativeInt = 0


class NumericPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Tolerance for the log-space threshold comparison, capped as well as floored.
    #: ``exceeds_threshold`` adds it to the right-hand side, so a large epsilon does not
    #: merely blur the boundary — it moves the threshold out of reach and every comparison
    #: answers "within". That is a fail-open switch disguised as a numeric detail, and it
    #: does not appear anywhere in the rendered report.
    epsilon: Annotated[float, Field(gt=0, le=1e-3, allow_inf_nan=False)] = 1e-9


class AcceptancePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command_timeout_seconds: PositiveInt = 300
    output_limit_bytes: PositiveInt = 1048576


class Policy(BaseModel):
    """The closed, cross-validated policy. Load whole or reject."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment: ExperimentPolicy = Field(default_factory=ExperimentPolicy)
    metrics: MetricsPolicy
    decision: DecisionPolicy = Field(default_factory=DecisionPolicy)
    null_control: NullControlPolicy = Field(default_factory=NullControlPolicy)
    numeric: NumericPolicy = Field(default_factory=NumericPolicy)
    acceptance: AcceptancePolicy = Field(default_factory=AcceptancePolicy)
    evidence: EvidencePolicy = Field(default_factory=EvidencePolicy)

    @model_validator(mode="after")
    def _cross_field(self) -> Policy:
        d, m, e = self.decision, self.metrics, self.experiment
        # The one chained invariant that ties the gate to the clamp: a threshold
        # above the clamp could never be crossed; a hard-max above the clamp is
        # equally unreachable (measurement.md §3.9).
        if not (1 < d.friction_threshold <= d.component_hard_max <= m.clamp_ratio):
            raise ValueError(
                "require 1 < friction_threshold <= component_hard_max <= clamp_ratio "
                f"(got friction_threshold={d.friction_threshold}, "
                f"component_hard_max={d.component_hard_max}, clamp_ratio={m.clamp_ratio})"
            )
        if e.minimum_valid_pairs > e.runs_per_variant:
            raise ValueError("minimum_valid_pairs must be <= runs_per_variant")
        if e.minimum_ffr_pairs > e.runs_per_variant:
            raise ValueError("minimum_ffr_pairs must be <= runs_per_variant")
        if e.minimum_baseline_successes > e.runs_per_variant:
            raise ValueError("minimum_baseline_successes must be <= runs_per_variant")
        return self


def evaluator_fingerprint(policy: Policy) -> tuple[object, ...]:
    """The deciding part of a policy, in a form two policies can be compared on.

    A policy holds two different kinds of thing. Thresholds and metric parameters are the
    *evaluator's*: they are published before any data is collected, and the whole
    pre-registration argument rests on them being fixed. Sample sizes — how many pairs this
    particular recording managed — are the *experiment's*, and legitimately differ between
    a treatment with three pairs and a null with two.

    Only the first kind belongs here. A cassette arrives from a pull request carrying its
    own policy, and if that policy decided the verdict unchallenged, the author of a
    regression could set ``friction_threshold`` to 9 and pass. ``gate`` compares this
    fingerprint against the evaluator's own and refuses to decide when they differ.

    ``verify`` deliberately does *not*: reproducing a recorded decision means reproducing
    it under the policy that produced it. Whether that policy was the canonical one is a
    separate question, and it is ``gate``'s.
    """
    return (
        policy.decision.friction_threshold,
        policy.decision.component_hard_max,
        policy.null_control.maximum_ffr,
        policy.metrics.alpha,
        policy.metrics.clamp_ratio,
        policy.metrics.small_sample_floor,
        tuple(sorted((name.value, weight) for name, weight in policy.metrics.weights.items())),
        policy.metrics.max_direct_test_invocations,
        policy.metrics.churn_exclude,
        policy.numeric.epsilon,
        policy.evidence.max_unpaired_tool_uses,
    )


def default_policy() -> Policy:
    """The in-code equivalent of ``policies/default.yaml`` (measurement.md §3.9).

    Kept here so the domain and its tests never disagree on the defaults; the
    YAML file is the operator-facing copy the application layer loads.
    """
    return Policy(
        metrics=MetricsPolicy(
            weights={
                ComponentName.FILES_READ_DISTINCT: 1.0,
                ComponentName.TEST_CYCLES: 1.0,
                ComponentName.FINAL_CHURN: 1.0,
            }
        )
    )
