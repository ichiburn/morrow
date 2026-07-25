"""Recompute the measurement for runs that were already recorded.

Normalisation happens after the agent has finished, so a failure there does not lose the
run: the raw stream, the pre-run snapshot and the launcher log are all still on disk. This
recovers the numbers without paying for the agent time again.

    uv run python scripts/summarise_runs.py r0 r1

These are the raw per-run counts. They are *not* the numbers the gate decides on: the
policy drops small-sample pairs per component before taking a median, so a ratio computed
by eye from this table can differ from the one in the report. Build a cassette and run
``morrow verify`` for the decided values.
"""

from __future__ import annotations

import sys

from _recording import load_run


def main() -> None:
    run_ids = sys.argv[1:] or ["r0", "r1"]
    print(
        f"{'run':6} {'reads':>7} {'tests':>7} {'churn':>8} "
        f"{'events':>8} {'unknown':>8} {'pass':>6}"
    )
    for source_run in run_ids:
        run = load_run(source_run)
        print(
            f"{source_run:6} {run.files_read_distinct:>7} {run.test_cycles:>7} "
            f"{run.churn.total_lines:>8} {len(run.events):>8} "
            f"{run.audit.unknown_raw_kinds:>8} {run.acceptance_passed!s:>6}"
        )


if __name__ == "__main__":
    main()
