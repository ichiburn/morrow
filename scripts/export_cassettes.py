"""Send the published cassettes to SigNoz, so the verdict and the trajectory sit together.

    uv run python scripts/export_cassettes.py
    uv run python scripts/export_cassettes.py --endpoint otel.example:4317

Everything exported here is re-derived by the verifier first. The spans therefore carry the
same verdict a reader gets from ``morrow verify`` on the same directory — the dashboard is
a view of the decision, not a second opinion about it.

Wall-clock duration is deliberately absent. It is a property of the machine that did the
recording rather than of the work the task required, so it is not in the published evidence
and a replay has none to report. Putting a zero there would be inventing a measurement.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from _recording import ROOT

from morrow.adapters.cassette.checks import parse_run
from morrow.adapters.cassette.store import encode_json, read_cassette
from morrow.adapters.cassette.verify import Verification, verify_path
from morrow.adapters.otel.export import (
    DEFAULT_ENDPOINT,
    ExperimentTelemetry,
    RunTelemetry,
    export_experiment,
)
from morrow.domain.assessment import EvidenceError
from morrow.domain.cassette import Manifest
from morrow.domain.metrics import ComponentName

CASSETTE_ROOT = ROOT / "cassettes"
PUBLISHED = ("null-control-as-recorded", "null-control-arms-swapped", "treatment-replace-cache")


def _policy_digest(manifest: Manifest) -> str:
    """A stable identifier for the evaluator snapshot the verdict was decided under."""
    return hashlib.sha256(encode_json(manifest.policy.model_dump(mode="json"))).hexdigest()


def _runs(outcome: Verification, path: Path) -> list[RunTelemetry]:
    """One telemetry record per adopted run, with the counts the verdict was built from."""
    assert outcome.manifest is not None
    records: list[RunTelemetry] = []

    cassette = read_cassette(path)
    for entry in outcome.manifest.adopted_runs:
        parsed = parse_run(entry, cassette.files)
        if isinstance(parsed, EvidenceError):  # pragma: no cover — verify already passed
            raise SystemExit(f"run {entry.run_id} no longer parses: {parsed.detail}")
        counts = parsed.counts()
        records.append(
            RunTelemetry(
                run_id=entry.run_id,
                variant=entry.variant.value,
                pair_id=entry.pair_id,
                attempt_index=entry.attempt_index,
                adopted=entry.adopted,
                terminal_status=entry.terminal_status.value,
                wall_duration_ms=None,
                events=parsed.events,
                files_read_distinct=int(counts[ComponentName.FILES_READ_DISTINCT]),
                test_cycles=int(counts[ComponentName.TEST_CYCLES]),
                final_churn=int(counts[ComponentName.FINAL_CHURN]),
            )
        )
    return records


def export(name: str, *, endpoint: str) -> str:
    path = CASSETTE_ROOT / name
    outcome = verify_path(path)
    if outcome.manifest is None or outcome.assessment is None:
        raise SystemExit(
            f"{name}: cassette does not verify far enough to export ({outcome.detail})"
        )
    manifest = outcome.manifest

    experiment = ExperimentTelemetry(
        experiment_id=manifest.experiment_id,
        scenario_id=manifest.scenario_id,
        # These spans are a replay of recorded evidence, and say so. A reader filtering the
        # dashboard for live measurements must not pick these up by accident.
        evidence_mode="replay",
        provider=manifest.provider,
        model=manifest.model.value,
        policy_sha256=_policy_digest(manifest),
        verdict=outcome.assessment.state.value,
        primary_reason=outcome.detail,
        ffr_gate=outcome.assessment.ffr_gate,
        runs=_runs(outcome, path),
    )
    trace_id = export_experiment(experiment, endpoint=endpoint)
    ffr = "n/a" if experiment.ffr_gate is None else f"{experiment.ffr_gate:.4f}"
    spans = sum(1 + len(run.events) for run in experiment.runs)
    print(
        f"{name:30} {experiment.verdict:20} FFR {ffr:>7}  "
        f"{len(experiment.runs)} runs / ~{spans} spans  trace {trace_id}"
    )
    return trace_id


def main() -> None:
    endpoint = DEFAULT_ENDPOINT
    args = sys.argv[1:]
    if args[:1] == ["--endpoint"] and len(args) > 1:
        endpoint = args[1]
        args = args[2:]
    names = tuple(args) or PUBLISHED

    print(f"exporting to {endpoint}")
    for name in names:
        export(name, endpoint=endpoint)


if __name__ == "__main__":
    main()
