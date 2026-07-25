"""Policy schema: closed to unknown keys, and every cross-field invariant from
measurement.md §3.9 rejected at construction (fail-closed)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from morrow.domain.metrics import ComponentName
from morrow.domain.policy import (
    DecisionPolicy,
    ExperimentPolicy,
    MetricsPolicy,
    NumericPolicy,
    Policy,
    default_policy,
)

_WEIGHTS = {
    ComponentName.FILES_READ_DISTINCT: 1.0,
    ComponentName.TEST_CYCLES: 1.0,
    ComponentName.FINAL_CHURN: 1.0,
}


def _policy(**overrides: object) -> Policy:
    """Build a valid policy, overriding one nested section at a time."""
    fields: dict[str, object] = {"metrics": MetricsPolicy(weights=_WEIGHTS)}
    fields.update(overrides)
    return Policy(**fields)  # type: ignore[arg-type]


class TestDefaults:
    def test_default_policy_matches_the_documented_values(self) -> None:
        policy = default_policy()
        assert policy.experiment.runs_per_variant == 4
        assert policy.experiment.minimum_valid_pairs == 3
        assert policy.experiment.minimum_baseline_successes == 2
        assert policy.metrics.alpha == 1.0
        assert policy.metrics.clamp_ratio == 10.0
        assert policy.metrics.small_sample_floor == 3
        assert policy.decision.friction_threshold == 1.5
        assert policy.decision.component_hard_max == 3.0
        assert policy.null_control.maximum_ffr == 1.2
        assert policy.numeric.epsilon == pytest.approx(1e-9)
        assert set(policy.metrics.weights) == set(_WEIGHTS)


class TestClosedSchema:
    def test_unknown_top_level_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Policy(metrics=MetricsPolicy(weights=_WEIGHTS), bogus=1)  # type: ignore[call-arg]

    def test_unknown_nested_key_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ExperimentPolicy(runs_per_variant=4, unexpected=True)  # type: ignore[call-arg]

    def test_frozen_instance_cannot_be_mutated(self) -> None:
        policy = default_policy()
        with pytest.raises(ValidationError):
            policy.metrics = MetricsPolicy(weights=_WEIGHTS)  # type: ignore[misc]


class TestCrossFieldInvariants:
    def test_component_hard_max_above_clamp_ratio_rejected(self) -> None:
        # component_hard_max 3 > clamp_ratio 2 breaks the chain.
        with pytest.raises(ValidationError):
            _policy(
                metrics=MetricsPolicy(weights=_WEIGHTS, clamp_ratio=2.0),
                decision=DecisionPolicy(friction_threshold=1.5, component_hard_max=3.0),
            )

    def test_friction_threshold_above_component_hard_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _policy(decision=DecisionPolicy(friction_threshold=3.0, component_hard_max=2.0))

    def test_friction_threshold_at_or_below_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _policy(decision=DecisionPolicy(friction_threshold=1.0, component_hard_max=3.0))

    def test_threshold_chain_boundary_is_accepted(self) -> None:
        # 1 < 2 <= 2 <= 2 is the tight boundary and must load.
        policy = _policy(
            metrics=MetricsPolicy(weights=_WEIGHTS, clamp_ratio=2.0),
            decision=DecisionPolicy(friction_threshold=2.0, component_hard_max=2.0),
        )
        assert policy.decision.component_hard_max == 2.0

    def test_minimum_valid_pairs_above_runs_per_variant_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _policy(experiment=ExperimentPolicy(runs_per_variant=4, minimum_valid_pairs=5))

    def test_minimum_ffr_pairs_above_runs_per_variant_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _policy(experiment=ExperimentPolicy(runs_per_variant=4, minimum_ffr_pairs=5))

    def test_minimum_baseline_successes_above_runs_per_variant_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _policy(
                experiment=ExperimentPolicy(runs_per_variant=4, minimum_baseline_successes=5)
            )


class TestFieldConstraints:
    def test_alpha_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            MetricsPolicy(weights=_WEIGHTS, alpha=0.0)

    def test_clamp_ratio_below_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MetricsPolicy(weights=_WEIGHTS, clamp_ratio=0.9)

    def test_non_positive_weight_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MetricsPolicy(weights={ComponentName.FINAL_CHURN: 0.0})

    def test_empty_weights_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MetricsPolicy(weights={})

    def test_epsilon_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            NumericPolicy(epsilon=0.0)
