"""Turn the recordings under ``.morrow/state`` into the cassettes that ship in the repo.

    uv run python scripts/build_cassettes.py

The recordings themselves are not publishable: the raw provider stream carries absolute
paths and whole shell command bodies. What comes out of here is — normalized events with
opaque references, two integer records per run, the evaluator policy that was in force,
and the report that policy produced. ``morrow verify`` then re-derives the verdict from
exactly those files, which is what makes the published numbers checkable by a reader who
does not trust the report.

Three cassettes are built, and the third one is the point:

* ``treatment-replace-cache``   — main vs the coupled candidate, K=3
* ``null-control-as-recorded``  — two clones of main, in the order they were recorded
* ``null-control-arms-swapped`` — the *same two runs* with the arm labels exchanged

The null control is supposed to be symmetric: both arms are the same tree, so which one is
called "baseline" is arbitrary. One-sided aggregation is not symmetric, though, and
swapping the labels moves the null's FFR. Publishing only the favourable order would be
choosing the answer. Both are published, and the README reports the range.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from _recording import ROOT, RecordedRun, load_run

from morrow.adapters.cassette.store import digest_of, encode_events, encode_json, write_cassette
from morrow.adapters.cassette.verify import report_meta, verify_path
from morrow.adapters.report.render import render_json, render_markdown
from morrow.domain.assessment import (
    Assessment,
    EvidenceError,
    Mode,
    check_null_control,
    enforce,
    evaluate_policy,
    invalidated_experiment,
    validate_experiment,
)
from morrow.domain.cassette import (
    REPORT_JSON_NAME,
    REPORT_MARKDOWN_NAME,
    ChurnRecord,
    EvidenceMode,
    ExperimentKind,
    LauncherRecord,
    Manifest,
    RunEntry,
    RunFiles,
    RunStatus,
    TerminalStatus,
)
from morrow.domain.events import EventKind, KnownModel, TerminalReason
from morrow.domain.metrics import ComponentName, RawPairMeasurement, Variant
from morrow.domain.policy import ExperimentPolicy, MetricsPolicy, Policy

CASSETTE_ROOT = ROOT / "cassettes"
PROVIDER = "claude-code"
SCENARIO = "replace-cache"


@dataclass(frozen=True)
class PairSpec:
    pair_id: int
    baseline: str
    candidate: str


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    kind: ExperimentKind
    pairs: tuple[PairSpec, ...]
    #: The runs in the order they were actually executed. Recording order is a property of
    #: the session, not of the arm labels, so swapping arms must not renumber it.
    order: tuple[str, ...]


TREATMENT = ExperimentSpec(
    experiment_id="treatment-replace-cache",
    kind=ExperimentKind.TREATMENT,
    pairs=(
        PairSpec(0, baseline="r0", candidate="r1"),
        PairSpec(1, baseline="r2", candidate="r3"),
        PairSpec(2, baseline="r4", candidate="r5"),
    ),
    order=("r0", "r1", "r2", "r3", "r4", "r5"),
)

NULL_AS_RECORDED = ExperimentSpec(
    experiment_id="null-control-as-recorded",
    kind=ExperimentKind.NULL_CONTROL,
    pairs=(
        PairSpec(0, baseline="n0", candidate="n1"),
        PairSpec(1, baseline="r20", candidate="r21"),
    ),
    order=("n0", "n1", "r20", "r21"),
)

NULL_ARMS_SWAPPED = ExperimentSpec(
    experiment_id="null-control-arms-swapped",
    kind=ExperimentKind.NULL_CONTROL,
    pairs=(
        PairSpec(0, baseline="n1", candidate="n0"),
        PairSpec(1, baseline="r21", candidate="r20"),
    ),
    order=("n0", "n1", "r20", "r21"),
)


def policy_for(pair_count: int) -> Policy:
    """The evaluator policy, with the sample-size requirements set to what was recorded.

    The decision thresholds — ``friction_threshold``, ``component_hard_max``,
    ``maximum_ffr`` — and every metric parameter are left at their published defaults for
    all three cassettes. Only the pair counts differ, because the treatment ran three pairs
    and the null two. Loosening a *threshold* to fit the data would invalidate the whole
    exercise; stating the sample size honestly is not the same act.
    """
    if pair_count < 2:
        raise SystemExit(
            f"an experiment needs at least two pairs to establish a baseline; got {pair_count}"
        )
    return Policy(
        experiment=ExperimentPolicy(
            runs_per_variant=pair_count,
            minimum_valid_pairs=pair_count,
            minimum_ffr_pairs=pair_count,
            minimum_baseline_successes=2,
        ),
        metrics=MetricsPolicy(
            weights={
                ComponentName.FILES_READ_DISTINCT: 1.0,
                ComponentName.TEST_CYCLES: 1.0,
                ComponentName.FINAL_CHURN: 1.0,
            }
        ),
    )


def _terminal_status(run: RecordedRun) -> TerminalStatus:
    """Read the terminal status off the completion event, not off an assumption."""
    completion = next(
        (event for event in run.events if event.kind is EventKind.COMPLETION), None
    )
    if completion is None:
        return TerminalStatus.CRASHED
    return (
        TerminalStatus.COMPLETED
        if completion.terminal_reason is TerminalReason.COMPLETED
        else TerminalStatus.CRASHED
    )


def _session_ref(run: RecordedRun) -> str:
    start = next(event for event in run.events if event.kind is EventKind.SESSION_START)
    return start.session_ref


def _assert_publishable(run: RecordedRun) -> None:
    """Refuse to publish a recording whose normalisation lost something.

    These defects exist only in the raw provider stream, and the raw stream is not
    published — so after this point nobody, including ``verify``, can ever detect them. A
    tool call whose result never arrived, a line that would not parse, a duplicate tool id:
    each one is work that may have happened and left no trace, which would be scored as a
    cheaper run rather than as a broken measurement.

    ``unknown_raw_kinds`` is deliberately not here. Those events *do* survive publication,
    as ``raw_kind: unknown``, so a reader can count them in the cassette and weigh them.
    Recording them honestly is the right answer; refusing to publish is not.
    """
    audit = run.audit
    blocking = {
        "unparsable_lines": audit.unparsable_lines,
        "orphaned_tool_results": audit.orphaned_tool_results,
        "duplicate_tool_ids": audit.duplicate_tool_ids,
        "unpaired_tool_uses": audit.unpaired_tool_uses,
        "direct_test_invocations": audit.direct_test_invocations,
        "unclassifiable_commands": audit.unclassifiable_commands,
    }
    found = {name: count for name, count in blocking.items() if count}
    if found:
        raise SystemExit(
            f"run {run.source_run}: normalisation left defects that cannot be checked "
            f"after publication: {found}"
        )
    if _terminal_status(run) is not TerminalStatus.COMPLETED:
        raise SystemExit(
            f"run {run.source_run}: terminal status is not 'completed'; it must not be "
            "adopted into a published cassette"
        )


def _run_files(run: RecordedRun) -> tuple[RunFiles, dict[str, bytes]]:
    """The three published files for one run, and the names they are stored under."""
    names = RunFiles(
        events=f"{run.run_id}.events.jsonl",
        churn=f"{run.run_id}.churn.json",
        tests=f"{run.run_id}.tests.json",
    )
    churn = ChurnRecord(
        added_lines=run.churn.added_lines,
        deleted_lines=run.churn.deleted_lines,
        files_added=run.churn.files_added,
        files_deleted=run.churn.files_deleted,
        files_modified=run.churn.files_modified,
        binary_bytes_changed=run.churn.binary_bytes_changed,
        binary_files_changed=run.churn.binary_files_changed,
    )
    tests = LauncherRecord(exit_codes=tuple(entry["exit_code"] for entry in run.launcher))
    payload = {
        names.events: encode_events(run.events),
        names.churn: encode_json(churn.model_dump(mode="json")),
        names.tests: encode_json(tests.model_dump(mode="json")),
    }
    return names, payload


def build(spec: ExperimentSpec, *, null_ffr_gate: float | None = None) -> Assessment:
    """Build one cassette and return the assessment that was recorded into it."""
    loaded = {source: load_run(source) for source in spec.order}
    for run in loaded.values():
        _assert_publishable(run)
    policy = policy_for(len(spec.pairs))

    entries: list[RunEntry] = []
    files: dict[str, bytes] = {}
    seen_per_variant: dict[Variant, int] = {Variant.BASELINE: 0, Variant.CANDIDATE: 0}
    measurements: list[RawPairMeasurement] = []

    for pair in spec.pairs:
        sides = {Variant.BASELINE: loaded[pair.baseline], Variant.CANDIDATE: loaded[pair.candidate]}
        for variant in (Variant.BASELINE, Variant.CANDIDATE):
            run = sides[variant]
            names, payload = _run_files(run)
            files.update(payload)
            entries.append(
                RunEntry(
                    run_id=run.run_id,
                    run_index=seen_per_variant[variant],
                    variant=variant,
                    pair_id=pair.pair_id,
                    order_position=spec.order.index(run.source_run),
                    attempt_index=0,
                    adopted=True,
                    terminal_status=_terminal_status(run),
                    status=RunStatus.OK if run.acceptance_passed else RunStatus.FAILED,
                    session_ref=_session_ref(run),
                    files=names,
                )
            )
            seen_per_variant[variant] += 1

        baseline, candidate = sides[Variant.BASELINE], sides[Variant.CANDIDATE]
        measurements.append(
            RawPairMeasurement(
                pair_id=pair.pair_id,
                baseline_success=baseline.acceptance_passed,
                candidate_success=candidate.acceptance_passed,
                baseline=_counts(baseline),
                candidate=_counts(candidate),
            )
        )

    experiment = validate_experiment(measurements, policy)
    if isinstance(experiment, EvidenceError):
        raise SystemExit(f"{spec.experiment_id}: evidence rejected — {experiment.detail}")

    # Exactly the order ``verify`` applies, so the recorded verdict is the one a verifier
    # will re-derive. If the two disagreed, the cassette would fail its own verification.
    # The same condition ``verify`` applies — "a null control that is present is checked",
    # without the extra ``kind`` clause the builder used to carry. The two must agree, or a
    # cassette can be recorded under one rule and judged under another.
    null_error = (
        check_null_control(null_ffr_gate, policy) if null_ffr_gate is not None else None
    )
    assessment = (
        invalidated_experiment(experiment, null_error)
        if null_error is not None
        else evaluate_policy(experiment, policy)
    )

    draft = Manifest(
        experiment_id=spec.experiment_id,
        scenario_id=SCENARIO,
        kind=spec.kind,
        provider=PROVIDER,
        model=KnownModel.CLAUDE_SONNET,
        recorded_mode=Mode.MEASURE,
        recorded_evidence_mode=EvidenceMode.LIVE,
        policy=policy,
        runs=tuple(sorted(entries, key=lambda e: (e.pair_id, e.variant.value))),
        digests={},
        null_control_ffr_gate=null_ffr_gate,
    )

    # The report is rendered through the same meta builder ``verify`` uses, so the two
    # sides cannot drift apart in how they read the manifest.
    exit_result = enforce(Mode.MEASURE, assessment, strict=draft.recorded_strict)
    meta = report_meta(draft)
    files[REPORT_JSON_NAME] = render_json(experiment, assessment, exit_result, meta).encode("utf-8")
    files[REPORT_MARKDOWN_NAME] = render_markdown(
        experiment, assessment, exit_result, meta
    ).encode("utf-8")

    manifest = draft.model_copy(
        update={"digests": {name: digest_of(payload) for name, payload in files.items()}}
    )
    destination = CASSETTE_ROOT / spec.experiment_id
    write_cassette(destination, manifest, files)

    # Verify what was just written, through the real verifier. The builder and the verifier
    # apply the same rules by construction, but only one of them is the one a reader runs —
    # and a cassette that fails its own verification is worse than no cassette at all. This
    # also catches the per-run invariants the builder does not itself check.
    check = verify_path(destination)
    if check.assessment is None or check.assessment.state is not assessment.state:
        raise SystemExit(
            f"{spec.experiment_id}: written cassette does not verify as recorded "
            f"(recorded {assessment.state.value}, verifier said {check.state.value}: "
            f"{check.detail})"
        )
    if check.report_matches is not True:
        raise SystemExit(f"{spec.experiment_id}: written report is not reproducible")

    ffr = "n/a" if assessment.ffr_gate is None else f"{assessment.ffr_gate:.4f}"
    print(
        f"{spec.experiment_id:30} {assessment.state.value:20} "
        f"FFR_gate {ffr:>8}  exit {exit_result.exit_code}  "
        f"verify={check.state.value} -> {destination.relative_to(ROOT)}"
    )
    return assessment


def _counts(run: RecordedRun) -> dict[ComponentName, float]:
    return {
        ComponentName.FILES_READ_DISTINCT: float(run.files_read_distinct),
        ComponentName.TEST_CYCLES: float(run.test_cycles),
        ComponentName.FINAL_CHURN: float(run.churn.total_lines),
    }


def main() -> None:
    CASSETTE_ROOT.mkdir(parents=True, exist_ok=True)
    as_recorded = build(NULL_AS_RECORDED)
    swapped = build(NULL_ARMS_SWAPPED)

    if as_recorded.ffr_gate is None or swapped.ffr_gate is None:
        raise SystemExit("a null control produced no FFR; refusing to build the treatment")

    # The treatment carries the *worse* of the two null orderings. The label assignment is
    # arbitrary, so reporting the flattering one beside the treatment would be a choice
    # made after seeing the data — exactly the thing the pre-registered rule exists to stop.
    worst_null = max(as_recorded.ffr_gate, swapped.ffr_gate)
    print(
        f"null FFR_gate: as-recorded {as_recorded.ffr_gate:.4f}, "
        f"arms-swapped {swapped.ffr_gate:.4f} -> carrying {worst_null:.4f}"
    )
    build(TREATMENT, null_ffr_gate=worst_null)


if __name__ == "__main__":
    sys.exit(main())
