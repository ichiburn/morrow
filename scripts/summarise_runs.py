"""Recompute the measurement for runs that were already recorded.

Normalisation happens after the agent has finished, so a failure there does not lose the
run: the raw stream, the pre-run snapshot and the launcher log are all still on disk. This
recovers the numbers without paying for the agent time again.

    uv run python scripts/summarise_runs.py r0 r1
"""

from __future__ import annotations

import sys
from pathlib import Path

from morrow.adapters.claude.stream import ClaudeStreamNormalizer
from morrow.adapters.fs.snapshot import Snapshot, compute_churn, take_snapshot
from morrow.adapters.fs.workspace import count_launcher_invocations
from morrow.domain.events import EventKind

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / ".morrow" / "state"
WORK_ROOT = ROOT / ".morrow" / "work"

#: Normalised events carry a bounded run id (`^r[0-9]{1,3}$`) so an identifier cannot
#: become a free-text channel. Recording used some names outside that shape, so the
#: on-disk name is mapped to a conforming one here rather than loosening the schema.
CONFORMING = {"n0": "r90", "n1": "r91"}


def _rebuild_pre(run_id: str) -> Snapshot:
    """Rebuild the pre-run snapshot's record table from the stored content copy."""
    content_root = STATE_ROOT / "snapshots" / f"{run_id}.pre"
    snapshot = take_snapshot(content_root)
    snapshot.content_root = content_root
    return snapshot


def summarise(run_id: str) -> dict[str, int]:
    workspace = WORK_ROOT / run_id
    pre = _rebuild_pre(run_id)
    post = take_snapshot(workspace)
    churn = compute_churn(pre, post, workspace)

    normalizer = ClaudeStreamNormalizer(
        run_id=CONFORMING.get(run_id, run_id), workspace=workspace
    )
    lines = (STATE_ROOT / "streams" / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
    events, audit = normalizer.normalize(iter(lines))

    return {
        "files_read_distinct": len({e.path_ref for e in events if e.kind is EventKind.FILE_READ}),
        "test_cycles": count_launcher_invocations(STATE_ROOT / "launcher-log" / f"{run_id}.jsonl"),
        "final_churn": churn.total_lines,
        "events": len(events),
        "unknown_raw_kinds": audit.unknown_raw_kinds,
        "unpaired": audit.unpaired_tool_uses,
    }


def main() -> None:
    run_ids = sys.argv[1:] or ["r0", "r1"]
    header = f"{'run':6} {'reads':>7} {'tests':>7} {'churn':>8} {'events':>8} {'unknown':>8}"
    print(header)
    for run_id in run_ids:
        m = summarise(run_id)
        print(
            f"{run_id:6} {m['files_read_distinct']:>7} {m['test_cycles']:>7} "
            f"{m['final_churn']:>8} {m['events']:>8} {m['unknown_raw_kinds']:>8}"
        )


if __name__ == "__main__":
    main()
