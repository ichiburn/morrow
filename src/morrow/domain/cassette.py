"""The cassette schema — the published form of one experiment's evidence.

A cassette is what makes C1 and C4 checkable rather than asserted: it holds every
normalized event, the churn and launcher counts behind each run, the evaluator policy
that was in force, and the report that was published. ``morrow verify`` re-derives the
verdict from exactly these files and compares it to what was recorded, so a reader who
does not trust the report can recompute it.

Two properties are load-bearing.

*Everything here is publishable.* The manifest carries opaque references and enums, never
a path, a session identifier from the provider, or a command body. What the evaluator
knew and did not publish stays on the evaluator side (:mod:`morrow.adapters.refs`).

*Everything here is closed.* Every model forbids unknown keys and every free-ish string is
pattern-bounded, so a cassette either parses whole or is rejected. File names are bounded
to a single path segment: a manifest cannot name ``../etc/passwd`` or a nested directory,
which is what keeps reading a cassette from becoming a traversal (evidence.md §5.1).

This module is pure data. Reading and writing cassettes is the adapter's job.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

from morrow.domain.assessment import Mode
from morrow.domain.events import KnownModel, RunId, SessionRef
from morrow.domain.metrics import Variant
from morrow.domain.policy import FinitePositiveFloat, Policy

#: The schema version of the cassette format. A cassette written by a different version is
#: rejected rather than best-effort parsed: silently reading an older layout is how a
#: verifier ends up comparing two different things and calling them equal.
CASSETTE_SCHEMA_VERSION: Literal[1] = 1

#: A single path segment under the cassette root. No separator, no leading dot, so a
#: manifest entry cannot escape the directory or name a hidden file.
FileName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]
#: Lowercase hex SHA-256.
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
#: Identifiers that appear in the published report. Bounded so neither can carry prose.
ExperimentId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
ScenarioId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
ProviderId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")]

#: The two report surfaces a cassette records, and the names they are stored under.
REPORT_JSON_NAME = "report.json"
REPORT_MARKDOWN_NAME = "report.md"
MANIFEST_NAME = "manifest.json"


class TerminalStatus(StrEnum):
    """How the agent process ended, as decided by the evaluator — not by the provider.

    Only a ``COMPLETED`` run is required to carry a ``COMPLETION`` event (evidence.md §5.1);
    a run that was killed at the wall clock legitimately has none.
    """

    COMPLETED = "completed"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    CRASHED = "crashed"


class RunStatus(StrEnum):
    """Whether the run's acceptance check passed. Distinct from ``TerminalStatus``: an
    agent can finish cleanly and still leave the tests red."""

    OK = "ok"
    FAILED = "failed"


class ExperimentKind(StrEnum):
    """A treatment experiment compares two different trees; a null control compares two
    clones of the same tree and therefore measures only run-to-run variance (§3.8)."""

    TREATMENT = "treatment"
    NULL_CONTROL = "null_control"


class EvidenceMode(StrEnum):
    """Where the evidence a report was rendered from came from.

    Recorded in the manifest because the report is regenerated during ``verify`` and
    compared byte-for-byte: rendering it under a different mode label would fail the
    comparison on a word, not on a number.
    """

    LIVE = "live"
    REPLAY = "replay"


class InvalidPairReason(StrEnum):
    """Why a pair was invalidated at the infrastructure level (§3.2).

    An enum rather than a sentence, because this reaches the published report and a
    free-text reason is a channel out of the trust boundary.
    """

    LAUNCHER_TAMPERED = "launcher_tampered"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    NORMALIZATION_FAILED = "normalization_failed"
    ACCEPTANCE_FAILED_AT_PRE = "acceptance_failed_at_pre"
    RETRIES_EXHAUSTED = "retries_exhausted"


class InvalidPairRecord(BaseModel):
    """A pair excluded from the experiment, retained with its reason so the report can
    say "N attempted, M valid, K invalid" honestly rather than only showing survivors."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pair_id: NonNegativeInt
    reason: InvalidPairReason


class ChurnRecord(BaseModel):
    """The churn counts for one run, as measured against the pre-run tree.

    ``final_churn`` is ``added_lines + deleted_lines``; binary changes are counted but
    kept out of the component, because a byte count and a line count are not the same
    quantity and adding them would make the ratio meaningless.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    added_lines: NonNegativeInt
    deleted_lines: NonNegativeInt
    files_added: NonNegativeInt
    files_deleted: NonNegativeInt
    files_modified: NonNegativeInt
    binary_bytes_changed: NonNegativeInt = 0
    binary_files_changed: NonNegativeInt = 0

    @property
    def total_lines(self) -> int:
        return self.added_lines + self.deleted_lines


class LauncherRecord(BaseModel):
    """The launcher's own log for one run: one exit code per invocation, in order.

    This is the primary source for ``test_cycles`` (evidence.md §6.4) — what the launcher
    itself appended, not something inferred from the event stream.

    Exit codes are kept rather than a bare count so that **whether the run passed is
    re-derivable from the evidence**. A manifest that merely asserted ``status: ok`` would
    be an unchecked claim by whoever built the cassette, and a verifier has no business
    taking the candidate's word for the one fact the verdict turns on.

    The agent bypassing the launcher is deliberately *not* recorded here. That count is
    derivable from the events (a ``command`` event whose purpose is ``direct_test``), and
    storing it twice would let the two copies disagree, at which point a verifier has to
    decide which one to believe.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Bounded: a launcher log is a record of test runs, not an unbounded array.
    exit_codes: Annotated[tuple[NonNegativeInt, ...], Field(max_length=1024)]

    @property
    def invocations(self) -> int:
        return len(self.exit_codes)

    @property
    def acceptance_passed(self) -> bool:
        """Whether the last invocation exited zero.

        A run that never invoked the launcher did not pass. Absent evidence is absence;
        reading "no failures recorded" as "the tests passed" is the fail-open this project
        exists to avoid.
        """
        return bool(self.exit_codes) and self.exit_codes[-1] == 0


class RunFiles(BaseModel):
    """The three files one run contributes to the cassette."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    events: FileName
    churn: FileName
    tests: FileName


class RunEntry(BaseModel):
    """One recorded attempt. Retries append entries rather than overwriting, so every
    attempt survives; exactly one per ``(pair_id, variant)`` carries ``adopted``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: RunId
    run_index: NonNegativeInt
    variant: Variant
    pair_id: NonNegativeInt
    order_position: NonNegativeInt
    attempt_index: NonNegativeInt
    adopted: bool
    terminal_status: TerminalStatus
    status: RunStatus
    session_ref: SessionRef
    #: A regression test failed at post for this run (measurement.md §3.6). Distinct from
    #: ``status``: an acceptance failure is not automatically a regression.
    regression_detected: bool = False
    files: RunFiles


class Manifest(BaseModel):
    """The cassette's index: what was run, under which policy, and what was published.

    ``policy`` is embedded rather than referenced so a cassette is self-contained — the
    thresholds a verifier applies are the ones that were in force when the evidence was
    recorded, and a later edit to the repository's policy file cannot retroactively change
    a recorded verdict. ``recorded_mode`` is stored for the same reason: the report is
    regenerated in the mode that produced it, otherwise a byte comparison would fail on
    the mode label alone.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = CASSETTE_SCHEMA_VERSION
    experiment_id: ExperimentId
    scenario_id: ScenarioId
    kind: ExperimentKind
    provider: ProviderId
    model: KnownModel
    recorded_mode: Mode
    recorded_evidence_mode: EvidenceMode
    #: Whether ``--strict`` was in force when the recorded report was rendered. Part of
    #: the report's own header, so regenerating without it would fail the byte comparison.
    recorded_strict: bool = False
    policy: Policy
    runs: tuple[RunEntry, ...]
    #: file name -> SHA-256 of its bytes. Covers every file in the cassette except the
    #: manifest itself, including the two report surfaces.
    digests: Mapping[FileName, Digest]
    invalid_pairs: tuple[InvalidPairRecord, ...] = ()
    #: The null control's ``FFR_gate``, carried by a treatment cassette so the two numbers
    #: appear on the same screen (§3.8). It is a reference point, never an input to the
    #: threshold — the threshold is fixed in ``policy`` before any treatment data exists.
    #:
    #: Bounded, because this is the one number in the manifest that a cassette author
    #: chooses freely and that reaches ``math.log`` unguarded. An FFR is ``exp(...)`` and
    #: therefore positive by construction; a manifest claiming 0, a negative, or the bare
    #: ``NaN`` token that ``json.loads`` accepts would crash the comparison rather than
    #: fail it, and a traceback is not one of the exit codes in the state table.
    null_control_ffr_gate: FinitePositiveFloat | None = None
    #: The SigNoz trace the runs were exported under. Bounded to W3C trace-id shape.
    trace_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")] | None = None

    @property
    def adopted_runs(self) -> tuple[RunEntry, ...]:
        return tuple(run for run in self.runs if run.adopted)
