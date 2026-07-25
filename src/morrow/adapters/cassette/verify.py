"""Re-derive a verdict from a cassette, in the fixed order of evidence.md §5.2.

    1. verify the digests listed in the manifest        -> CASSETTE_CORRUPTED
    2. validate the schemas and the per-run invariants  -> EVIDENCE_INVALID / _INCOMPLETE
    3. recompute the metrics from the evidence
    4. recompute the verdict
    5. regenerate the report and compare it byte-for-byte with the recorded one
                                                        -> EVIDENCE_STALE

This is the whole of claim C1. Nothing here reads the recorded verdict before deciding:
the metrics come from the events, the churn records and the launcher counts, and the
verdict comes from the same three domain functions the gate uses. The recorded report is
touched only at step 5, and only to be compared against — never to be believed.

``gate`` shares steps 1-4 and **stops before step 5**, because a recorded artifact must
not be an input to a blocking decision (§4.6). Its verdict is the recomputed one.

The individual checks live in :mod:`morrow.adapters.cassette.checks`; what this module
owns is the order they run in and what each outcome means.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from morrow.adapters.cassette.checks import (
    RunEvidence,
    build_pairs,
    check_digests,
    check_pair_structure,
    check_run_invariants,
    parse_manifest,
    parse_run,
)
from morrow.adapters.cassette.store import (
    CassetteBytes,
    CassetteReadError,
    read_cassette,
)
from morrow.adapters.report.render import (
    InvalidPair,
    NullControlOutcome,
    ReportMeta,
    render_json,
    render_markdown,
)
from morrow.domain.assessment import (
    Assessment,
    EvidenceError,
    ExitResult,
    Mode,
    Severity,
    State,
    check_null_control,
    enforce,
    evaluate_policy,
    invalidated_experiment,
    validate_experiment,
)
from morrow.domain.cassette import (
    REPORT_JSON_NAME,
    REPORT_MARKDOWN_NAME,
    ExperimentKind,
    Manifest,
)
from morrow.domain.metrics import ValidatedExperiment

#: The two modes that decide from recorded evidence. ``measure`` is absent by construction:
#: it runs the experiment, so handing it a cassette is a category error rather than a
#: configuration choice, and the type says so instead of a runtime check discovering it.
VerificationMode = Literal[Mode.VERIFY, Mode.GATE]


@dataclass(frozen=True)
class Verification:
    """The outcome of a verification pass.

    ``report_matches`` is ``None`` when step 5 was not reached — either the run stopped at
    an earlier error, or the mode was ``gate``, which deliberately never looks.
    """

    exit_result: ExitResult
    detail: str
    manifest: Manifest | None = None
    assessment: Assessment | None = None
    experiment: ValidatedExperiment | None = None
    report_markdown: str | None = None
    report_json: str | None = None
    report_matches: bool | None = None

    @property
    def state(self) -> State:
        return self.exit_result.state

    @property
    def exit_code(self) -> int:
        return self.exit_result.exit_code


def _fail(
    mode: VerificationMode,
    error: EvidenceError,
    *,
    strict: bool = False,
    manifest: Manifest | None = None,
) -> Verification:
    """Wrap an evidence error in the exit code its state maps to under ``mode``."""
    return Verification(
        enforce(mode, error, strict=strict), error.detail, manifest=manifest
    )


def _detail_of(assessment: Assessment) -> str:
    for finding in assessment.findings:
        if finding.state is assessment.state and finding.detail:
            return finding.detail
    return assessment.state.value


def report_meta(manifest: Manifest) -> ReportMeta:
    """Rebuild the report's accompanying facts from the manifest alone.

    Everything the renderer needs beyond the domain objects is recorded, so the report can
    be regenerated without the evaluator's private state. That is what makes the byte
    comparison in step 5 meaningful rather than circular.
    """
    return ReportMeta(
        policy=manifest.policy,
        evidence_mode=manifest.recorded_evidence_mode,
        experiment_id=manifest.experiment_id,
        scenario_id=manifest.scenario_id,
        provider=manifest.provider,
        model=manifest.model.value,
        invalid_pairs=tuple(
            InvalidPair(pair_id=p.pair_id, reason=p.reason.value)
            for p in manifest.invalid_pairs
        ),
        null_control=(
            None
            if manifest.null_control_ffr_gate is None
            else NullControlOutcome(ffr_gate=manifest.null_control_ffr_gate)
        ),
        trace_id=manifest.trace_id,
    )


def verify_bytes(
    cassette: CassetteBytes,
    *,
    mode: VerificationMode = Mode.VERIFY,
    strict: bool = False,
) -> Verification:
    """Run the §5.2 procedure over an already-read cassette.

    ``mode`` selects what the exit code means, not what is checked: ``verify`` compares the
    regenerated report against the recorded one and reports reproduction; ``gate`` stops
    after the recomputed verdict and lets a friction finding block.
    """
    parsed = parse_manifest(cassette.manifest_bytes)
    if isinstance(parsed, str):
        return _fail(
            mode, EvidenceError(state=State.CASSETTE_CORRUPTED, detail=parsed), strict=strict
        )
    manifest = parsed
    policy = manifest.policy

    for error in (
        check_digests(manifest, cassette.files),
        check_pair_structure(manifest),
    ):
        if error is not None:
            return _fail(mode, error, strict=strict, manifest=manifest)

    runs: list[RunEvidence] = []
    for entry in manifest.adopted_runs:
        parsed_run = parse_run(entry, cassette.files)
        if isinstance(parsed_run, EvidenceError):
            return _fail(mode, parsed_run, strict=strict, manifest=manifest)
        invariant_error = check_run_invariants(parsed_run, policy)
        if invariant_error is not None:
            return _fail(mode, invariant_error, strict=strict, manifest=manifest)
        runs.append(parsed_run)

    experiment = validate_experiment(build_pairs(runs), policy)
    if isinstance(experiment, EvidenceError):
        return _fail(mode, experiment, strict=strict, manifest=manifest)

    # A treatment experiment is only interpretable beside its null control. A null outside
    # the band invalidates the day's experiment rather than changing its verdict (§3.8), so
    # the rejection replaces the assessment instead of short-circuiting past the report:
    # "the experiment was invalidated" is a result a reader is entitled to see rendered.
    null_error = (
        check_null_control(manifest.null_control_ffr_gate, policy)
        if manifest.kind is ExperimentKind.TREATMENT
        and manifest.null_control_ffr_gate is not None
        else None
    )
    assessment = (
        invalidated_experiment(experiment, null_error)
        if null_error is not None
        else evaluate_policy(experiment, policy)
    )
    meta = report_meta(manifest)

    if mode is Mode.GATE:
        # §4.6: the recorded report is not an input to a blocking decision.
        gate_exit = enforce(Mode.GATE, assessment, strict=strict)
        return Verification(
            gate_exit,
            _detail_of(assessment),
            manifest=manifest,
            assessment=assessment,
            experiment=experiment,
            report_markdown=render_markdown(experiment, assessment, gate_exit, meta),
            report_json=render_json(experiment, assessment, gate_exit, meta),
        )

    # Step 5 — regenerate under the *recorded* mode and strictness, because those appear in
    # the report's own header; rendering under today's mode would fail the comparison on a
    # label rather than on a number.
    recorded_exit = enforce(manifest.recorded_mode, assessment, strict=manifest.recorded_strict)
    regenerated_json = render_json(experiment, assessment, recorded_exit, meta)
    regenerated_markdown = render_markdown(experiment, assessment, recorded_exit, meta)

    matches_json = regenerated_json.encode("utf-8") == cassette.files[REPORT_JSON_NAME]
    matches_markdown = (
        regenerated_markdown.encode("utf-8") == cassette.files[REPORT_MARKDOWN_NAME]
    )

    if not (matches_json and matches_markdown):
        differing = [
            name
            for name, ok in (
                (REPORT_JSON_NAME, matches_json),
                (REPORT_MARKDOWN_NAME, matches_markdown),
            )
            if not ok
        ]
        return Verification(
            enforce(
                Mode.VERIFY, EvidenceError(state=State.EVIDENCE_STALE, detail=""), strict=strict
            ),
            f"regenerated report differs from the recorded one: {differing}",
            manifest=manifest,
            assessment=assessment,
            experiment=experiment,
            report_markdown=regenerated_markdown,
            report_json=regenerated_json,
            report_matches=False,
        )

    # The report was reproduced. That settles C1, but it does not make the *experiment*
    # sound: a verdict that is itself an evidence, infrastructure or not-comparable state
    # stays exit 2 in every mode (§4.2). Reproducing an invalidated experiment faithfully
    # is still an invalidated experiment.
    if assessment.severity >= Severity.INCONCLUSIVE:
        return Verification(
            enforce(Mode.VERIFY, assessment, strict=strict),
            (
                f"report reproduced byte for byte, and the re-derived verdict is "
                f"{assessment.state.value}: {_detail_of(assessment)}"
            ),
            manifest=manifest,
            assessment=assessment,
            experiment=experiment,
            report_markdown=regenerated_markdown,
            report_json=regenerated_json,
            report_matches=True,
        )

    return Verification(
        enforce(
            Mode.VERIFY,
            EvidenceError(state=State.EVIDENCE_REPRODUCED, detail=""),
            strict=strict,
        ),
        (
            f"verdict {assessment.state.value} re-derived from the evidence; "
            "both report surfaces match byte for byte"
        ),
        manifest=manifest,
        assessment=assessment,
        experiment=experiment,
        report_markdown=regenerated_markdown,
        report_json=regenerated_json,
        report_matches=True,
    )


def verify_path(
    path: Path, *, mode: VerificationMode = Mode.VERIFY, strict: bool = False
) -> Verification:
    """Read a cassette from disk and verify it.

    A cassette that cannot be read at all is ``INFRASTRUCTURE_ERROR``, kept distinct from
    one whose bytes are present but wrong: "the evidence is missing" and "the evidence
    disagrees with itself" call for different investigations.
    """
    try:
        cassette = read_cassette(path)
    except CassetteReadError as error:
        return _fail(
            mode,
            EvidenceError(state=State.INFRASTRUCTURE_ERROR, detail=str(error)),
            strict=strict,
        )
    return verify_bytes(cassette, mode=mode, strict=strict)
