"""Send one experiment's worth of real normalized events to SigNoz.

Used to prove the export path end to end before any recorded experiment exists. The
numbers here come from the committed capture fixture; the verdict fields are placeholders
and are labelled ``smoke`` in ``morrow.evidence_mode`` so nothing here can be mistaken for
a measured result.
"""

from __future__ import annotations

from pathlib import Path

from morrow.adapters.claude.stream import ClaudeStreamNormalizer
from morrow.adapters.otel.export import ExperimentTelemetry, RunTelemetry, export_experiment

FIXTURE = (
    Path(__file__).resolve().parents[1] / "tests/contract/fixtures/claude_stream_capture.jsonl"
)


def main() -> None:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()

    runs: list[RunTelemetry] = []
    for index, (variant, run_id) in enumerate([("baseline", "r0"), ("candidate", "r1")]):
        normalizer = ClaudeStreamNormalizer(run_id=run_id, workspace=Path("/workspace"))
        events, _audit = normalizer.normalize(iter(lines))
        reads = len({e.path_ref for e in events if e.kind.value == "file_read"})
        runs.append(
            RunTelemetry(
                run_id=run_id,
                variant=variant,
                pair_id=0,
                attempt_index=0,
                adopted=True,
                terminal_status="completed",
                wall_duration_ms=96_000 + index * 40_000,
                events=events,
                files_read_distinct=reads,
                test_cycles=0,
                final_churn=31 if variant == "baseline" else 214,
            )
        )

    experiment = ExperimentTelemetry(
        experiment_id="smoke-e2e-0001",
        scenario_id="coupling",
        evidence_mode="smoke",
        provider="claude",
        model="claude-opus",
        policy_sha256="0" * 64,
        verdict="SMOKE",
        primary_reason="NOT_A_MEASUREMENT",
        ffr_gate=None,
        runs=runs,
    )

    trace_id = export_experiment(experiment)
    span_count = 1 + 1 + sum(1 + len(run.events) for run in runs)
    print(f"trace_id: {trace_id}")
    print(f"spans exported: {span_count}")


if __name__ == "__main__":
    main()
