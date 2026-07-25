"""Value objects for MORROW's measurement model.

Pure data only: every type here is a frozen Pydantic model or an enum, so an
instance is an immutable snapshot of one experiment's measurements. There is no
I/O, no clock, no randomness. The two decision functions that *consume* these
shapes (``validate_experiment`` / ``evaluate_policy``) live in
:mod:`morrow.domain.assessment`; the friction math lives in
:mod:`morrow.domain.friction`.

Counts are non-negative integers (``files_read_distinct`` / ``test_cycles`` /
``final_churn``). Floating point never enters a *validated* measurement — the
normalized event model keeps amounts as integers (evidence.md §6.1). The raw,
still-untrusted form (:class:`RawPairMeasurement`) carries floats precisely so
that the fail-closed numeric check in ``validate_experiment`` has something to
reject: NaN / Inf / negative / non-integral surface as ``EVIDENCE_INVALID``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, NonNegativeInt


class ComponentName(StrEnum):
    """The three mutually exclusive friction components (measurement.md §3.3)."""

    FILES_READ_DISTINCT = "files_read_distinct"
    TEST_CYCLES = "test_cycles"
    FINAL_CHURN = "final_churn"


class Variant(StrEnum):
    """The two arms compared within every pair. Names are concealed from the
    agent (operations.md §7) but are the canonical labels on the evaluator side."""

    BASELINE = "baseline"
    CANDIDATE = "candidate"


class RawPairMeasurement(BaseModel):
    """One pair's measurements *before* numeric validation.

    Counts are floats here on purpose: this is the untrusted shape handed to
    ``validate_experiment``, which rejects NaN / Inf / negative / non-integral
    values (``EVIDENCE_INVALID``) and a missing required component
    (``EVIDENCE_INCOMPLETE``) before anything reaches the decision.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair_id: int
    baseline_success: bool
    candidate_success: bool
    # A regression test failed at post in this pair (either variant). Distinct
    # from success: an acceptance failure lowers success without being a
    # REGRESSION (measurement.md §3.6).
    regression_detected: bool = False
    baseline: Mapping[ComponentName, float]
    candidate: Mapping[ComponentName, float]


class PairMeasurement(BaseModel):
    """One validated pair. Counts are non-negative integers by construction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair_id: NonNegativeInt
    baseline_success: bool
    candidate_success: bool
    regression_detected: bool = False
    baseline: Mapping[ComponentName, NonNegativeInt]
    candidate: Mapping[ComponentName, NonNegativeInt]

    @property
    def both_succeeded(self) -> bool:
        """A pair is *successful* only when both arms succeeded (§3.7)."""
        return self.baseline_success and self.candidate_success

    def counts(self, variant: Variant) -> Mapping[ComponentName, int]:
        return self.baseline if variant is Variant.BASELINE else self.candidate


class ValidatedExperiment(BaseModel):
    """The set of infra-valid pairs handed to ``evaluate_policy``.

    Only pairs that passed infra-level validation appear here; invalidated pairs
    were retried and dropped upstream (§3.2). ``evaluate_policy`` still checks
    ``len(pairs)`` against ``minimum_valid_pairs`` because that threshold is a
    policy value, not an evidence one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pairs: tuple[PairMeasurement, ...]

    @property
    def successful_pairs(self) -> tuple[PairMeasurement, ...]:
        return tuple(p for p in self.pairs if p.both_succeeded)

    @property
    def baseline_success_count(self) -> int:
        return sum(1 for p in self.pairs if p.baseline_success)

    @property
    def candidate_success_count(self) -> int:
        return sum(1 for p in self.pairs if p.candidate_success)
