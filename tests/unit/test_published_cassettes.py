"""The cassettes committed under ``cassettes/`` still re-derive their recorded verdicts.

This is a regression test on published artifacts, not on logic. The numbers in the README
come from these three cassettes, so a change to the metrics, the policy defaults or the
report layout that would move a published verdict has to fail here first — and then be
answered by rebuilding the cassettes deliberately, with the new numbers written up.

CI runs the same three cassettes through the CLI and asserts the same exit codes. The
duplication is intentional: this file fails fast during development, and the CLI step
proves the exit code a user would actually see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from morrow.adapters.cassette.verify import verify_path
from morrow.domain.assessment import Mode, State

PUBLISHED = Path(__file__).resolve().parents[2] / "cassettes"


@pytest.mark.parametrize(
    ("name", "expected_state", "expected_exit"),
    [
        ("null-control-as-recorded", State.EVIDENCE_REPRODUCED, 0),
        ("null-control-arms-swapped", State.EVIDENCE_REPRODUCED, 0),
        # The treatment's null control sits outside its published band, so the
        # pre-registered rule invalidates the experiment. Reproducing that faithfully is
        # still exit 2 — "the instrument could not separate signal from noise" is a result,
        # and it is not allowed to read as a pass.
        ("treatment-replace-cache", State.INVALID_EXPERIMENT, 2),
    ],
)
def test_published_cassette_still_verifies(
    name: str, expected_state: State, expected_exit: int
) -> None:
    outcome = verify_path(PUBLISHED / name)
    assert outcome.state is expected_state, outcome.detail
    assert outcome.exit_code == expected_exit
    # Reached step 5 and matched: the report committed next to the evidence is the one
    # that evidence produces.
    assert outcome.report_matches is True


def test_the_null_controls_disagree_by_arm_order() -> None:
    """The finding the README leads with, pinned as a test.

    The null control is symmetric by construction — both arms are clones of the same tree.
    One-sided aggregation is not symmetric, so relabelling which clone is "baseline" moves
    the null's FFR from 1.0000 to 1.7403, across the 1.20 tolerance band. Both orderings
    are committed precisely so that neither can be the one picked after seeing the data.
    """
    as_recorded = verify_path(PUBLISHED / "null-control-as-recorded")
    swapped = verify_path(PUBLISHED / "null-control-arms-swapped")
    assert as_recorded.assessment is not None
    assert swapped.assessment is not None

    assert as_recorded.assessment.ffr_gate == pytest.approx(1.0, abs=1e-9)
    assert swapped.assessment.ffr_gate == pytest.approx(1.7403, abs=5e-5)
    assert as_recorded.assessment.state is State.OK
    assert swapped.assessment.state is State.FRICTION_REGRESSION


def test_churn_separates_under_either_arm_ordering() -> None:
    """The strongest claim in the README, checked against the evidence rather than quoted.

    Every treatment pair's ``final_churn`` ratio must sit above every ratio the null
    produces in either ordering. If a future recording narrows that gap, this fails and the
    claim has to be rewritten rather than left standing.
    """
    treatment = verify_path(PUBLISHED / "treatment-replace-cache")
    assert treatment.experiment is not None

    from morrow.domain.friction import component_ratio
    from morrow.domain.metrics import ComponentName

    def churn_ratios(name: str) -> list[float]:
        outcome = verify_path(PUBLISHED / name)
        assert outcome.experiment is not None
        assert outcome.manifest is not None
        policy = outcome.manifest.policy
        return [
            component_ratio(
                pair.baseline[ComponentName.FINAL_CHURN],
                pair.candidate[ComponentName.FINAL_CHURN],
                alpha=policy.metrics.alpha,
                clamp_ratio=policy.metrics.clamp_ratio,
            )
            for pair in outcome.experiment.successful_pairs
        ]

    treatment_ratios = churn_ratios("treatment-replace-cache")
    null_ratios = churn_ratios("null-control-as-recorded") + churn_ratios(
        "null-control-arms-swapped"
    )

    assert len(treatment_ratios) == 3
    assert len(null_ratios) == 4
    assert min(treatment_ratios) > max(null_ratios)


def test_gate_will_not_decide_on_a_two_pair_experiment() -> None:
    """The null controls ran two pairs, and the published floor is three.

    `gate` compares the deciding fields of a cassette's policy — including the sample-size
    floors — against the evaluator's, so a cassette cannot lower the bar it is measured
    against. A two-pair experiment is therefore not something the gate decides on, however
    interesting its numbers are.
    """
    outcome = verify_path(PUBLISHED / "null-control-arms-swapped", mode=Mode.GATE)
    assert outcome.state is State.GATE_PRECONDITION_UNMET
    assert outcome.exit_code == 2


def test_the_swapped_null_reaches_a_friction_verdict_on_zero_difference() -> None:
    """Two identical trees, and the re-derived verdict is a friction regression.

    This is what "the aggregate does not separate" rests on: at this sample size the noise
    floor reaches past the published threshold, so the same rule that would flag a real
    regression also flags a comparison of a tree against itself.

    The gate will not act on it — see the test above — but the verdict is a property of the
    evidence, not of whether anything chose to enforce it.
    """
    outcome = verify_path(PUBLISHED / "null-control-arms-swapped")
    assert outcome.assessment is not None
    assert outcome.assessment.state is State.FRICTION_REGRESSION
    assert outcome.assessment.ffr_gate is not None
    assert outcome.assessment.ffr_gate > outcome.manifest.policy.decision.friction_threshold  # type: ignore[union-attr]
