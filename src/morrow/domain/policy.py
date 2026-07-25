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

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    field_validator,
    model_validator,
)

from morrow.domain.metrics import ComponentName

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

    alpha: PositiveFloat = 1.0
    clamp_ratio: float = 10.0  # R; validated >= 1 below
    small_sample_floor: PositiveInt = 3
    weights: Mapping[ComponentName, PositiveFloat]
    churn_exclude: tuple[str, ...] = _DEFAULT_CHURN_EXCLUDE
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

    friction_threshold: float = 1.50
    component_hard_max: float = 3.00


class NullControlPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    maximum_ffr: float = 1.20


class NumericPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    epsilon: PositiveFloat = 1e-9


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
