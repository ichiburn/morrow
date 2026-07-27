"""Read back what SigNoz actually stored for a MORROW experiment.

Exporting a span without an error is not evidence that it landed. This queries the
telemetry store directly, which is the only thing that supports the claim that the two
trajectories are visible side by side.

It goes to ClickHouse rather than through the SigNoz HTTP API deliberately: no session,
no token, nothing to keep in a file, and the answer is the stored rows rather than a
rendering of them.

    uv run python scripts/signoz_query.py
    uv run python scripts/signoz_query.py --minutes 30
"""

from __future__ import annotations

import argparse
import subprocess
import sys

CLICKHOUSE_CONTAINER = "signoz-telemetrystore-clickhouse-0-0"
SERVICE_NAME = "morrow"

#: The traces to report on: the most recent N experiments, by their root spans.
#:
#: Filtering by time window alone counts every export that happened to land inside it, so
#: re-running the exporter doubles the numbers and the readback stops describing any
#: particular export. Anchoring on the newest experiment roots makes the answer "what the
#: last run of the exporter stored", which is the question being asked.
LATEST_TRACES = """
SELECT DISTINCT traceID
FROM signoz_traces.distributed_signoz_index_v3
WHERE serviceName = '{service}'
  AND name = 'morrow.experiment'
  AND timestamp > now() - INTERVAL {minutes} MINUTE
ORDER BY timestamp DESC
LIMIT {traces}
"""

SPAN_BREAKDOWN = """
SELECT name, count() AS spans
FROM signoz_traces.distributed_signoz_index_v3
WHERE serviceName = '{service}' AND timestamp > now() - INTERVAL {minutes} MINUTE
  AND traceID IN ({traces})
GROUP BY name
ORDER BY spans DESC
"""

RUN_SUMMARY = """
SELECT
    attributes_string['morrow.variant']       AS variant,
    attributes_string['morrow.run.id']        AS run_id,
    attributes_number['morrow.metric.files_read_distinct'] AS files_read,
    attributes_number['morrow.metric.test_cycles']         AS test_cycles,
    attributes_number['morrow.metric.final_churn']         AS churn
FROM signoz_traces.distributed_signoz_index_v3
WHERE serviceName = '{service}'
  AND name = 'morrow.run'
  AND timestamp > now() - INTERVAL {minutes} MINUTE
  AND traceID IN ({traces})
ORDER BY variant, run_id
"""


def _query(sql: str) -> str:
    result = subprocess.run(
        ["docker", "exec", CLICKHOUSE_CONTAINER, "clickhouse-client", "--query", sql],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr.strip()[:500], file=sys.stderr)
        raise SystemExit(f"query failed (exit {result.returncode})")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument(
        "--traces",
        type=int,
        default=3,
        help="How many of the most recent experiment traces to report on (default 3).",
    )
    args = parser.parse_args()

    latest = _query(
        LATEST_TRACES.format(service=SERVICE_NAME, minutes=args.minutes, traces=args.traces)
    )
    if not latest:
        raise SystemExit(f"no morrow experiment traces in the last {args.minutes} minute(s)")
    trace_ids = ", ".join(f"'{line.strip()}'" for line in latest.splitlines() if line.strip())
    print(f"--- {len(latest.splitlines())} most recent experiment trace(s) ---")

    breakdown = _query(
        SPAN_BREAKDOWN.format(service=SERVICE_NAME, minutes=args.minutes, traces=trace_ids)
    )
    print("\n--- spans by name ---")
    print(breakdown or "(none)")

    runs = _query(
        RUN_SUMMARY.format(service=SERVICE_NAME, minutes=args.minutes, traces=trace_ids)
    )
    print("\n--- run spans (variant, run_id, files_read, test_cycles, churn) ---")
    print(runs or "(none)")


if __name__ == "__main__":
    main()
