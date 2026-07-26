"""The individual checks a cassette has to pass, and the metrics read off it once it has.

Each function here answers one question and returns an ``EvidenceError`` naming the state
it failed with, or ``None``. Keeping them separate from the procedure that calls them
(:mod:`morrow.adapters.cassette.verify`) means the order of the steps is readable in one
place and the content of each step is testable on its own.

What these checks cannot see, stated plainly: an orphaned ``tool_result`` and an
unrecognised provider event kind exist only in the raw stream, and the raw stream is not
published. Those are caught at record time; a cassette cannot be re-checked for them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from morrow.adapters.cassette.store import digest_of
from morrow.domain.assessment import EvidenceError, State
from morrow.domain.cassette import (
    REPORT_JSON_NAME,
    REPORT_MARKDOWN_NAME,
    ChurnRecord,
    LauncherRecord,
    Manifest,
    RunEntry,
    RunStatus,
    TerminalStatus,
)
from morrow.domain.events import AgentEvent, CommandPurpose, EventKind
from morrow.domain.metrics import ComponentName, RawPairMeasurement, Variant
from morrow.domain.policy import Policy

_EVENT_ADAPTER: TypeAdapter[AgentEvent] = TypeAdapter(AgentEvent)


@dataclass(frozen=True)
class RunEvidence:
    """One adopted run's three files, parsed and bound to its manifest entry."""

    entry: RunEntry
    events: tuple[AgentEvent, ...]
    churn: ChurnRecord
    tests: LauncherRecord

    def counts(self) -> dict[ComponentName, float]:
        """The three components for this run.

        ``files_read_distinct`` counts *distinct* path references, so re-reading the same
        file to re-orient does not inflate the number of files the agent had to understand.
        """
        files_read = len(
            {event.path_ref for event in self.events if event.kind is EventKind.FILE_READ}
        )
        return {
            ComponentName.FILES_READ_DISTINCT: float(files_read),
            ComponentName.TEST_CYCLES: float(self.tests.invocations),
            ComponentName.FINAL_CHURN: float(self.churn.total_lines),
        }


# --- step 1: the bytes are what the manifest says they are ------------------------------


def parse_manifest(payload: bytes) -> Manifest | str:
    """Parse the manifest, or return why it could not be parsed.

    A manifest that does not parse is ``CASSETTE_CORRUPTED`` rather than
    ``EVIDENCE_INVALID``: without it there is no statement of what the evidence was
    supposed to be, so nothing downstream can even be checked.
    """
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return f"manifest is not valid JSON: {error}"
    try:
        return Manifest.model_validate(decoded)
    except ValidationError as error:
        return f"manifest does not match the cassette schema: {error.error_count()} error(s)"


def expected_files(manifest: Manifest) -> set[str]:
    """Exactly the files a cassette is allowed to contain.

    Derived from ``runs[].files`` plus the two report surfaces, never from the digest
    table. Taking it from the digests would make the rule circular: anything at all
    becomes permitted by listing its digest, and a cassette could then ship a ``notes.txt``
    full of real paths and shell bodies while still verifying. The point of a closed
    artifact is that its contents follow from its structure.

    Every attempt contributes, not only adopted ones — a retried run's evidence is retained
    on purpose (§5.1).
    """
    names = {REPORT_JSON_NAME, REPORT_MARKDOWN_NAME}
    for entry in manifest.runs:
        names.update({entry.files.events, entry.files.churn, entry.files.tests})
    return names


def check_digests(manifest: Manifest, files: Mapping[str, bytes]) -> EvidenceError | None:
    """Step 1 and part of §5.1: the file set is exactly right, and every digest matches.

    Three sets have to agree: what the manifest's structure implies, what its digest table
    vouches for, and what is on disk. A file present in any one of them but not the others
    is incomplete evidence — the manifest is supposed to enumerate everything, and a file
    nobody vouched for is exactly what an unlisted one is.
    """
    allowed = expected_files(manifest)
    listed = set(manifest.digests)
    present = set(files)

    if listed != allowed:
        undeclared = sorted(listed - allowed)
        unvouched = sorted(allowed - listed)
        return EvidenceError(
            state=State.EVIDENCE_INCOMPLETE,
            detail=(
                f"digest table does not match the manifest's own structure "
                f"(not declared by any run or report: {undeclared}, missing a digest: "
                f"{unvouched})"
            ),
        )
    if listed != present:
        missing = sorted(listed - present)
        unlisted = sorted(present - listed)
        return EvidenceError(
            state=State.EVIDENCE_INCOMPLETE,
            detail=(
                f"manifest does not describe the cassette "
                f"(missing {missing}, unlisted {unlisted})"
            ),
        )
    for name in sorted(listed):
        if digest_of(files[name]) != manifest.digests[name]:
            return EvidenceError(
                state=State.CASSETTE_CORRUPTED, detail=f"digest mismatch for {name}"
            )
    return None


# --- step 2: the evidence is well formed ------------------------------------------------


def parse_run(entry: RunEntry, files: Mapping[str, bytes]) -> RunEvidence | EvidenceError:
    """Parse one run's three files under their closed schemas."""
    for name in (entry.files.events, entry.files.churn, entry.files.tests):
        if name not in files:
            return EvidenceError(
                state=State.EVIDENCE_INCOMPLETE,
                detail=f"run {entry.run_id}: manifest lists {name}, which is absent",
            )

    events: list[AgentEvent] = []
    raw_events = files[entry.files.events].decode("utf-8", errors="replace")
    for index, line in enumerate(raw_events.splitlines()):
        if not line.strip():
            continue
        try:
            events.append(_EVENT_ADAPTER.validate_json(line))
        except ValidationError as error:
            return EvidenceError(
                state=State.EVIDENCE_INVALID,
                detail=(
                    f"run {entry.run_id}: event on line {index + 1} does not match the "
                    f"closed event schema ({error.error_count()} error(s))"
                ),
            )

    try:
        churn = ChurnRecord.model_validate_json(files[entry.files.churn])
        tests = LauncherRecord.model_validate_json(files[entry.files.tests])
    except ValidationError as error:
        return EvidenceError(
            state=State.EVIDENCE_INVALID,
            detail=(
                f"run {entry.run_id}: churn or tests record is malformed "
                f"({error.error_count()} error(s))"
            ),
        )

    return RunEvidence(entry=entry, events=tuple(events), churn=churn, tests=tests)


def check_run_invariants(run: RunEvidence, policy: Policy) -> EvidenceError | None:
    """The per-run checks of §5. The unit of validation is one run, never one variant."""
    entry, events = run.entry, run.events

    if any(event.run_id != entry.run_id for event in events):
        return EvidenceError(
            state=State.EVIDENCE_INVALID,
            detail=f"run {entry.run_id}: an event carries a different run id",
        )

    if [event.seq for event in events] != list(range(len(events))):
        return EvidenceError(
            state=State.EVIDENCE_INVALID,
            detail=(
                f"run {entry.run_id}: seq is not 0..{len(events) - 1} "
                "without gaps or duplicates"
            ),
        )

    tool_refs = [event.tool_ref for event in events if event.tool_ref is not None]
    if len(tool_refs) != len(set(tool_refs)):
        return EvidenceError(
            state=State.EVIDENCE_INVALID,
            detail=f"run {entry.run_id}: a tool reference is used more than once",
        )

    starts = [event for event in events if event.kind is EventKind.SESSION_START]
    if len(starts) != 1:
        return EvidenceError(
            state=State.EVIDENCE_INCOMPLETE,
            detail=(
                f"run {entry.run_id}: expected exactly one session_start, "
                f"found {len(starts)}"
            ),
        )
    if starts[0].session_ref != entry.session_ref:
        return EvidenceError(
            state=State.EVIDENCE_INVALID,
            detail=f"run {entry.run_id}: session reference does not match the manifest",
        )

    completions = [event for event in events if event.kind is EventKind.COMPLETION]
    # Only a run the evaluator marked completed is required to carry a completion event;
    # one killed at the wall clock legitimately has none (§5.1).
    if entry.terminal_status is TerminalStatus.COMPLETED and len(completions) != 1:
        return EvidenceError(
            state=State.EVIDENCE_INCOMPLETE,
            detail=(
                f"run {entry.run_id}: terminal status is completed but the stream carries "
                f"{len(completions)} completion event(s)"
            ),
        )

    unpaired = sum(
        1 for event in events if event.tool_ref is not None and event.success is None
    )
    if unpaired > policy.evidence.max_unpaired_tool_uses:
        return EvidenceError(
            state=State.EVIDENCE_INCOMPLETE,
            detail=(
                f"run {entry.run_id}: {unpaired} tool call(s) never had an outcome "
                f"confirmed, over the cap of {policy.evidence.max_unpaired_tool_uses}"
            ),
        )

    # The manifest's success flag has to follow from the launcher log, not stand beside it.
    # This is the single fact the whole verdict turns on — a pair counts only when both
    # arms succeeded — so a cassette must not be able to simply assert it.
    expected_status = RunStatus.OK if run.tests.acceptance_passed else RunStatus.FAILED
    if entry.status is not expected_status:
        return EvidenceError(
            state=State.EVIDENCE_INVALID,
            detail=(
                f"run {entry.run_id}: manifest says {entry.status.value}, but the launcher "
                f"log says {expected_status.value}"
            ),
        )

    commands = [event for event in events if event.kind is EventKind.COMMAND]
    unclassifiable = sum(1 for c in commands if c.purpose is CommandPurpose.UNCLASSIFIABLE)
    if unclassifiable:
        return EvidenceError(
            state=State.EVIDENCE_INCOMPLETE,
            detail=(
                f"run {entry.run_id}: {unclassifiable} command(s) could not be classified"
            ),
        )
    direct = sum(1 for c in commands if c.purpose is CommandPurpose.DIRECT_TEST)
    if direct > policy.metrics.max_direct_test_invocations:
        return EvidenceError(
            state=State.EVIDENCE_INCOMPLETE,
            detail=(
                f"run {entry.run_id}: {direct} test run(s) bypassed the launcher, over "
                f"the cap of {policy.metrics.max_direct_test_invocations}"
            ),
        )
    return None


def check_pair_structure(manifest: Manifest) -> EvidenceError | None:
    """The experiment-level checks of §5: attempts are unique, adoption is exactly one."""
    seen: set[tuple[int, Variant, int]] = set()
    for entry in manifest.runs:
        key = (entry.pair_id, entry.variant, entry.attempt_index)
        if key in seen:
            return EvidenceError(
                state=State.EVIDENCE_INVALID,
                detail=f"duplicate attempt for pair {entry.pair_id} {entry.variant.value}",
            )
        seen.add(key)

    # One recording must not back two arms. Without this, a manifest could point pair 0 and
    # pair 1 at the same three files, pass every digest and every per-run invariant, and
    # reach `minimum_valid_pairs` on a single run duplicated — a verdict re-derived from
    # fabricated repetition. Uniqueness is checked on both the run id and the files, since
    # either alone can be made to look distinct.
    seen_run_ids: set[str] = set()
    seen_files: set[str] = set()
    for entry in manifest.adopted_runs:
        if entry.run_id in seen_run_ids:
            return EvidenceError(
                state=State.EVIDENCE_INVALID,
                detail=f"run {entry.run_id} is adopted for more than one arm",
            )
        seen_run_ids.add(entry.run_id)
        for name in (entry.files.events, entry.files.churn, entry.files.tests):
            if name in seen_files:
                return EvidenceError(
                    state=State.EVIDENCE_INVALID,
                    detail=f"{name} backs more than one adopted run",
                )
            seen_files.add(name)

    # Invalidated pairs are counted in the report's "attempted" total, so a duplicate — or
    # a pair claimed as both invalidated and adopted — inflates how much work the
    # experiment appears to have done without any evidence behind the inflation.
    invalidated: set[int] = set()
    for record in manifest.invalid_pairs:
        if record.pair_id in invalidated:
            return EvidenceError(
                state=State.EVIDENCE_INVALID,
                detail=f"pair {record.pair_id} is listed as invalid more than once",
            )
        invalidated.add(record.pair_id)

    by_pair: dict[int, dict[Variant, int]] = {}
    for entry in manifest.adopted_runs:
        if entry.pair_id in invalidated:
            return EvidenceError(
                state=State.EVIDENCE_INVALID,
                detail=f"pair {entry.pair_id} is both invalidated and adopted",
            )
        by_pair.setdefault(entry.pair_id, {}).setdefault(entry.variant, 0)
        by_pair[entry.pair_id][entry.variant] += 1
    if not by_pair:
        return EvidenceError(
            state=State.EVIDENCE_INCOMPLETE, detail="no adopted run in the manifest"
        )
    for pair_id in sorted(by_pair):
        adopted = by_pair[pair_id]
        for variant in (Variant.BASELINE, Variant.CANDIDATE):
            count = adopted.get(variant, 0)
            if count != 1:
                return EvidenceError(
                    state=State.EVIDENCE_INVALID,
                    detail=(
                        f"pair {pair_id} has {count} adopted {variant.value} run(s); "
                        "exactly one is required"
                    ),
                )
    return None


# --- step 3: the metrics come from the evidence -----------------------------------------


def build_pairs(runs: Sequence[RunEvidence]) -> list[RawPairMeasurement]:
    """Group adopted runs into pairs and read the three components off each side."""
    by_pair: dict[int, dict[Variant, RunEvidence]] = {}
    for run in runs:
        by_pair.setdefault(run.entry.pair_id, {})[run.entry.variant] = run

    pairs: list[RawPairMeasurement] = []
    for pair_id in sorted(by_pair):
        sides = by_pair[pair_id]
        baseline = sides[Variant.BASELINE]
        candidate = sides[Variant.CANDIDATE]
        pairs.append(
            RawPairMeasurement(
                pair_id=pair_id,
                baseline_success=baseline.entry.status is RunStatus.OK,
                candidate_success=candidate.entry.status is RunStatus.OK,
                regression_detected=(
                    baseline.entry.regression_detected
                    or candidate.entry.regression_detected
                ),
                baseline=baseline.counts(),
                candidate=candidate.counts(),
            )
        )
    return pairs
