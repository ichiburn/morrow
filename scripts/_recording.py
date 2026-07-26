"""Read back a run that was already recorded under ``.morrow/state``.

Normalisation happens after the agent has finished, so the raw stream, the pre-run
snapshot and the launcher log are all still on disk when the numbers are wanted. Both
``summarise_runs.py`` (look at the numbers) and ``build_cassettes.py`` (publish them) go
through here, so the layout of a recording is described in exactly one place.

Nothing in this module is part of the shipped package: it reads the evaluator's private
state, which includes real paths and raw provider output. What it hands back — normalized
events, counts — is the publishable part.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.claude.stream import ClaudeStreamNormalizer
from morrow.adapters.fs.snapshot import Churn, Snapshot, compute_churn, take_snapshot
from morrow.domain.events import AgentEvent, EventKind, NormalizationAudit

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / ".morrow" / "state"
WORK_ROOT = ROOT / ".morrow" / "work"

#: Normalised events carry a bounded run id (``^r[0-9]{1,3}$``) so an identifier cannot
#: become a free-text channel. Recording used some names outside that shape, so the
#: on-disk name is mapped to a conforming one here rather than loosening the schema.
CONFORMING = {"n0": "r90", "n1": "r91"}


def conforming_id(source_run: str) -> str:
    return CONFORMING.get(source_run, source_run)


def rebuild_pre(source_run: str) -> Snapshot:
    """Rebuild the pre-run snapshot's record table from the stored content copy."""
    content_root = STATE_ROOT / "snapshots" / f"{source_run}.pre"
    snapshot = take_snapshot(content_root)
    snapshot.content_root = content_root
    return snapshot


def launcher_entries(source_run: str) -> list[dict[str, int]]:
    """The launcher's own log lines, in order. Empty when it was never invoked."""
    path = STATE_ROOT / "launcher-log" / f"{source_run}.jsonl"
    if not path.is_file():
        return []
    entries: list[dict[str, int]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


@dataclass(frozen=True)
class RecordedRun:
    """One run, re-derived from what was kept on disk."""

    source_run: str
    run_id: str
    events: tuple[AgentEvent, ...]
    audit: NormalizationAudit
    churn: Churn
    launcher: tuple[dict[str, int], ...]

    @property
    def files_read_distinct(self) -> int:
        return len(
            {event.path_ref for event in self.events if event.kind is EventKind.FILE_READ}
        )

    @property
    def test_cycles(self) -> int:
        return len(self.launcher)

    @property
    def acceptance_passed(self) -> bool:
        """Whether the last launcher invocation exited zero.

        A run that never invoked the launcher is *not* treated as passing. The acceptance
        result is evidence, and absent evidence is absence — reading "no failures recorded"
        as "the tests passed" is the fail-open this project exists to avoid.
        """
        return bool(self.launcher) and self.launcher[-1]["exit_code"] == 0


def load_run(source_run: str) -> RecordedRun:
    """Re-derive one recorded run: normalized events, churn, and the launcher log."""
    workspace = WORK_ROOT / source_run
    run_id = conforming_id(source_run)

    pre = rebuild_pre(source_run)
    post = take_snapshot(workspace)
    churn = compute_churn(pre, post, workspace)

    normalizer = ClaudeStreamNormalizer(run_id=run_id, workspace=workspace)
    stream = STATE_ROOT / "streams" / f"{source_run}.jsonl"
    lines = stream.read_text(encoding="utf-8").splitlines()
    events, audit = normalizer.normalize(iter(lines))

    return RecordedRun(
        source_run=source_run,
        run_id=run_id,
        events=tuple(events),
        audit=audit,
        churn=churn,
        launcher=tuple(launcher_entries(source_run)),
    )
