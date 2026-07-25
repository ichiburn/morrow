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

SPAN_BREAKDOWN = """
SELECT name, count() AS spans
FROM signoz_traces.distributed_signoz_index_v3
WHERE serviceName = '{service}' AND timestamp > now() - INTERVAL {minutes} MINUTE
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
ORDER BY variant
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
    args = parser.parse_args()

    breakdown = _query(SPAN_BREAKDOWN.format(service=SERVICE_NAME, minutes=args.minutes))
    print("--- spans by name ---")
    print(breakdown or "(none)")

    runs = _query(RUN_SUMMARY.format(service=SERVICE_NAME, minutes=args.minutes))
    print("\n--- run spans (variant, run_id, files_read, test_cycles, churn) ---")
    print(runs or "(none)")


if __name__ == "__main__":
    main()
