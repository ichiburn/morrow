"""Verification tests: what a cassette must survive, and what it must not.

The point of ``verify`` is that a reader who does not trust the report can recompute it.
That claim is only worth anything if the checks actually fire, so every test here breaks
one specific thing and asserts the state it produces — a tampered byte, a file nobody
listed, a report that no longer matches the evidence behind it.

The final test runs the real published cassettes. If a change to the metrics, the policy
defaults or the report layout would alter a verdict that is already committed, that test
fails and the cassettes have to be rebuilt deliberately rather than drifting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from morrow.adapters.cassette.store import (
    CassetteReadError,
    digest_of,
    encode_events,
    encode_json,
    read_cassette,
    write_cassette,
)
from morrow.adapters.cassette.verify import verify_path
from morrow.domain.assessment import Mode, State
from morrow.domain.cassette import (
    MANIFEST_NAME,
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
from morrow.domain.events import (
    CompletionEvent,
    FileReadEvent,
    KnownModel,
    RawKind,
    SessionStartEvent,
    StopReason,
    TerminalReason,
)
from morrow.domain.metrics import ComponentName, Variant
from morrow.domain.policy import ExperimentPolicy, MetricsPolicy, Policy

_WEIGHTS = {
    ComponentName.FILES_READ_DISTINCT: 1.0,
    ComponentName.TEST_CYCLES: 1.0,
    ComponentName.FINAL_CHURN: 1.0,
}


def _policy(pairs: int = 2) -> Policy:
    return Policy(
        experiment=ExperimentPolicy(
            runs_per_variant=pairs,
            minimum_valid_pairs=pairs,
            minimum_ffr_pairs=pairs,
            minimum_baseline_successes=2,
        ),
        metrics=MetricsPolicy(weights=_WEIGHTS),
    )


def _events(run_id: str, *, reads: int) -> bytes:
    """A minimal well-formed stream: a session start, N distinct reads, a completion."""
    events: list[object] = [
        SessionStartEvent(seq=0, run_id=run_id, raw_kind=RawKind.INIT, session_ref="s0",
                          model=KnownModel.CLAUDE_SONNET)
    ]
    for index in range(reads):
        events.append(
            FileReadEvent(
                seq=index + 1,
                run_id=run_id,
                raw_kind=RawKind.ASSISTANT_TOOL_USE,
                tool_ref=f"t{index}",
                success=True,
                path_ref=f"p{index}",
            )
        )
    events.append(
        CompletionEvent(
            seq=reads + 1,
            run_id=run_id,
            raw_kind=RawKind.RESULT,
            num_turns=3,
            output_tokens=100,
            api_duration_ms=1000,
            cost_micro_usd=1000,
            stop_reason=StopReason.END_TURN,
            terminal_reason=TerminalReason.COMPLETED,
            permission_denial_count=0,
        )
    )
    return encode_events(events)  # type: ignore[arg-type]


def _run(
    run_id: str,
    *,
    variant: Variant,
    pair_id: int,
    run_index: int,
    order: int,
    reads: int,
    tests: int,
    churn: int,
) -> tuple[RunEntry, dict[str, bytes]]:
    names = RunFiles(
        events=f"{run_id}.events.jsonl",
        churn=f"{run_id}.churn.json",
        tests=f"{run_id}.tests.json",
    )
    files = {
        names.events: _events(run_id, reads=reads),
        names.churn: encode_json(
            ChurnRecord(
                added_lines=churn,
                deleted_lines=0,
                files_added=1,
                files_deleted=0,
                files_modified=0,
            ).model_dump(mode="json")
        ),
        # A passing run: every invocation exited zero, and the last one is what decides.
        names.tests: encode_json(
            LauncherRecord(exit_codes=(0,) * tests).model_dump(mode="json")
        ),
    }
    entry = RunEntry(
        run_id=run_id,
        run_index=run_index,
        variant=variant,
        pair_id=pair_id,
        order_position=order,
        attempt_index=0,
        adopted=True,
        terminal_status=TerminalStatus.COMPLETED,
        status=RunStatus.OK,
        session_ref="s0",
        files=names,
    )
    return entry, files


def _build(
    root: Path,
    *,
    baseline: tuple[int, int, int] = (10, 4, 10),
    candidate: tuple[int, int, int] = (12, 5, 40),
) -> Path:
    """A two-pair cassette. Each arm is ``(reads, test cycles, churn)``.

    The defaults make the candidate side expensive enough to trip the gate. Passing equal
    arms with test counts below the small-sample floor produces a DEGRADED_DATA verdict
    instead, which is what ``--strict`` exists to act on.
    """
    from morrow.adapters.cassette.verify import report_meta
    from morrow.adapters.report.render import render_json, render_markdown
    from morrow.domain.assessment import (
        EvidenceError,
        enforce,
        evaluate_policy,
        validate_experiment,
    )
    from morrow.domain.metrics import RawPairMeasurement

    policy = _policy()
    entries: list[RunEntry] = []
    files: dict[str, bytes] = {}
    measurements: list[RawPairMeasurement] = []

    spec = [
        ("r0", Variant.BASELINE, 0, 0, 0, *baseline),
        ("r1", Variant.CANDIDATE, 0, 0, 1, *candidate),
        ("r2", Variant.BASELINE, 1, 1, 2, *baseline),
        ("r3", Variant.CANDIDATE, 1, 1, 3, *candidate),
    ]
    counts: dict[int, dict[Variant, dict[ComponentName, float]]] = {}
    for run_id, variant, pair_id, run_index, order, reads, tests, churn in spec:
        entry, payload = _run(
            run_id,
            variant=variant,
            pair_id=pair_id,
            run_index=run_index,
            order=order,
            reads=reads,
            tests=tests,
            churn=churn,
        )
        entries.append(entry)
        files.update(payload)
        counts.setdefault(pair_id, {})[variant] = {
            ComponentName.FILES_READ_DISTINCT: float(reads),
            ComponentName.TEST_CYCLES: float(tests),
            ComponentName.FINAL_CHURN: float(churn),
        }

    for pair_id in sorted(counts):
        measurements.append(
            RawPairMeasurement(
                pair_id=pair_id,
                baseline_success=True,
                candidate_success=True,
                baseline=counts[pair_id][Variant.BASELINE],
                candidate=counts[pair_id][Variant.CANDIDATE],
            )
        )

    experiment = validate_experiment(measurements, policy)
    # The fixture's own evidence must be valid, or every test built on it is testing the
    # wrong failure.
    assert not isinstance(experiment, EvidenceError), experiment.detail
    assessment = evaluate_policy(experiment, policy)
    exit_result = enforce(Mode.MEASURE, assessment)

    draft = Manifest(
        experiment_id="synthetic",
        scenario_id="synthetic",
        kind=ExperimentKind.TREATMENT,
        provider="claude-code",
        model=KnownModel.CLAUDE_SONNET,
        recorded_mode=Mode.MEASURE,
        recorded_evidence_mode=EvidenceMode.LIVE,
        policy=policy,
        runs=tuple(entries),
        digests={},
        # A treatment is only interpretable beside a null control, so one is required.
        # This fixture's null sits inside the band; the interesting case — a null that
        # does not — is covered by the published cassettes.
        null_control_ffr_gate=1.0,
    )
    meta = report_meta(draft)
    files[REPORT_JSON_NAME] = render_json(
        experiment, assessment, exit_result, meta  # type: ignore[arg-type]
    ).encode("utf-8")
    files[REPORT_MARKDOWN_NAME] = render_markdown(
        experiment, assessment, exit_result, meta  # type: ignore[arg-type]
    ).encode("utf-8")

    manifest = draft.model_copy(
        update={"digests": {name: digest_of(payload) for name, payload in files.items()}}
    )
    write_cassette(root, manifest, files)
    return root


@pytest.fixture
def cassette(tmp_path: Path) -> Path:
    return _build(tmp_path / "synthetic")


def _rewrite(root: Path, name: str, payload: bytes, *, fix_digest: bool) -> None:
    """Replace a file, optionally updating the manifest so the digest still matches.

    ``fix_digest=False`` simulates corruption; ``True`` simulates someone who edited the
    evidence *and* covered their tracks in the manifest — which the digests cannot catch
    and the recomputation must.
    """
    (root / name).write_bytes(payload)
    if fix_digest:
        manifest = json.loads((root / MANIFEST_NAME).read_text())
        manifest["digests"][name] = digest_of(payload)
        (root / MANIFEST_NAME).write_bytes(encode_json(manifest))


# --- the happy path ---------------------------------------------------------------


def test_untouched_cassette_reproduces(cassette: Path) -> None:
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_REPRODUCED
    assert outcome.exit_code == 0
    assert outcome.report_matches is True


def test_gate_blocks_on_the_recomputed_friction_finding(cassette: Path) -> None:
    outcome = verify_path(cassette, mode=Mode.GATE)
    assert outcome.state is State.FRICTION_REGRESSION
    assert outcome.exit_code == 1


def test_the_recorded_report_calls_the_same_finding_advisory(cassette: Path) -> None:
    """The same evidence and the same verdict, with a different consequence per mode.

    The cassette was recorded under ``measure``, where a friction finding is advisory, so
    the published report says PASS while ``gate`` on the same bytes exits 1. That split is
    the reason mode is a parameter rather than a property of the finding.
    """
    recorded = (cassette / REPORT_MARKDOWN_NAME).read_text(encoding="utf-8")
    assert "# MORROW — PASS" in recorded
    assert "| Advisory | yes |" in recorded
    assert "`FRICTION_REGRESSION`" in recorded
    assert verify_path(cassette, mode=Mode.GATE).exit_code == 1


# --- step 1: digests --------------------------------------------------------------


def test_tampered_evidence_is_corrupt(cassette: Path) -> None:
    _rewrite(cassette, "r1.churn.json", b'{"added_lines": 0}\n', fix_digest=False)
    outcome = verify_path(cassette)
    assert outcome.state is State.CASSETTE_CORRUPTED
    assert outcome.exit_code == 2


def test_a_file_nobody_listed_is_incomplete_evidence(cassette: Path) -> None:
    (cassette / "extra.json").write_bytes(b"{}\n")
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INCOMPLETE


def test_a_listed_file_that_is_missing_is_incomplete_evidence(cassette: Path) -> None:
    (cassette / "r1.tests.json").unlink()
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INCOMPLETE


def test_a_corrupt_manifest_is_not_a_verdict(cassette: Path) -> None:
    (cassette / MANIFEST_NAME).write_bytes(b"{not json")
    outcome = verify_path(cassette)
    assert outcome.state is State.CASSETTE_CORRUPTED


def test_a_symlink_cannot_stand_in_for_evidence(cassette: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_bytes(b"{}\n")
    (cassette / "r1.tests.json").unlink()
    (cassette / "r1.tests.json").symlink_to(target)
    outcome = verify_path(cassette)
    assert outcome.state is State.INFRASTRUCTURE_ERROR
    with pytest.raises(CassetteReadError):
        read_cassette(cassette)


# --- step 2: the evidence is well formed ------------------------------------------


def test_a_gap_in_seq_is_invalid_evidence(cassette: Path) -> None:
    lines = (cassette / "r1.events.jsonl").read_bytes().decode().splitlines()
    del lines[2]  # leaves a hole rather than renumbering
    _rewrite(cassette, "r1.events.jsonl", ("\n".join(lines) + "\n").encode(), fix_digest=True)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID


def test_an_event_from_another_run_is_invalid_evidence(cassette: Path) -> None:
    lines = (cassette / "r1.events.jsonl").read_bytes().decode().splitlines()
    record = json.loads(lines[1])
    record["run_id"] = "r99"
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    _rewrite(cassette, "r1.events.jsonl", ("\n".join(lines) + "\n").encode(), fix_digest=True)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID


def test_an_unknown_event_field_is_rejected(cassette: Path) -> None:
    """The event schema is closed: a provider that starts emitting a new field must not
    have it silently accepted into published evidence."""
    lines = (cassette / "r1.events.jsonl").read_bytes().decode().splitlines()
    record = json.loads(lines[1])
    record["surprise"] = "anything at all"
    lines[1] = json.dumps(record, sort_keys=True, separators=(",", ":"))
    _rewrite(cassette, "r1.events.jsonl", ("\n".join(lines) + "\n").encode(), fix_digest=True)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID


def test_a_session_reference_that_disagrees_with_the_manifest_is_invalid(cassette: Path) -> None:
    manifest = json.loads((cassette / MANIFEST_NAME).read_text())
    for run in manifest["runs"]:
        if run["run_id"] == "r1":
            run["session_ref"] = "s7"
    (cassette / MANIFEST_NAME).write_bytes(encode_json(manifest))
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID


def test_a_completed_run_without_a_completion_event_is_incomplete(cassette: Path) -> None:
    lines = (cassette / "r1.events.jsonl").read_bytes().decode().splitlines()
    kept = [line for line in lines if '"completion"' not in line]
    renumbered = []
    for seq, line in enumerate(kept):
        record = json.loads(line)
        record["seq"] = seq
        renumbered.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
    _rewrite(
        cassette, "r1.events.jsonl", ("\n".join(renumbered) + "\n").encode(), fix_digest=True
    )
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INCOMPLETE


def test_two_adopted_runs_on_one_arm_is_invalid(cassette: Path) -> None:
    manifest = json.loads((cassette / MANIFEST_NAME).read_text())
    duplicate = dict(manifest["runs"][0])
    duplicate["attempt_index"] = 1
    manifest["runs"].append(duplicate)
    (cassette / MANIFEST_NAME).write_bytes(encode_json(manifest))
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID


# --- step 5: the report is the one that was published -----------------------------


def test_an_edited_report_is_stale_even_with_a_matching_digest(cassette: Path) -> None:
    """The digest only proves the bytes are the ones the manifest expected. Whether those
    bytes *follow from the evidence* is a separate question, and this is the check that
    asks it."""
    doctored = (cassette / REPORT_MARKDOWN_NAME).read_bytes().replace(
        b"`FRICTION_REGRESSION`", b"`OK`                 "
    )
    _rewrite(cassette, REPORT_MARKDOWN_NAME, doctored, fix_digest=True)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_STALE
    assert outcome.exit_code == 2
    assert outcome.report_matches is False


def test_evidence_edited_to_flatter_numbers_is_caught_by_recomputation(cassette: Path) -> None:
    """Rewriting the churn *and* its digest defeats step 1 entirely. Step 5 still fails,
    because the report on disk no longer follows from the evidence on disk."""
    smaller = encode_json(
        ChurnRecord(
            added_lines=10, deleted_lines=0, files_added=1, files_deleted=0, files_modified=0
        ).model_dump(mode="json")
    )
    _rewrite(cassette, "r1.churn.json", smaller, fix_digest=True)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_STALE


def test_gate_ignores_the_recorded_report_entirely(cassette: Path) -> None:
    """§4.6: a recorded artifact is never an input to a blocking decision. A report edited
    to say PASS must not stop the gate from blocking."""
    doctored = (cassette / REPORT_MARKDOWN_NAME).read_bytes().replace(
        b"`FRICTION_REGRESSION`", b"`OK`                 "
    )
    _rewrite(cassette, REPORT_MARKDOWN_NAME, doctored, fix_digest=True)
    outcome = verify_path(cassette, mode=Mode.GATE)
    assert outcome.state is State.FRICTION_REGRESSION
    assert outcome.exit_code == 1


# --- a hostile cassette: what an adversarial review found, pinned ------------------
#
# Everything below corresponds to a way a cassette author could have got a favourable
# outcome out of `verify` or `gate` before review. Each test states the attack and asserts
# the state that now stops it.


def _patch_manifest(root: Path, mutate: object) -> None:
    """Apply a mutation to the manifest JSON and write it back.

    Manifest edits do not need a digest fix — the manifest is not in its own digest table.
    """
    manifest = json.loads((root / MANIFEST_NAME).read_text())
    assert callable(mutate)
    mutate(manifest)
    (root / MANIFEST_NAME).write_bytes(encode_json(manifest))


@pytest.mark.parametrize("hostile", [0.0, -1.0, float("nan"), float("inf")])
def test_a_non_finite_null_control_is_refused_not_crashed(cassette: Path, hostile: float) -> None:
    """A threshold comparison takes the log of this number.

    Zero and negatives raise ValueError, NaN raises decimal.InvalidOperation, and either
    leaves the CLI exiting 1 on a traceback — a code the state table does not contain. The
    bound belongs on the schema, so the cassette is rejected before any of that.
    """
    text = json.dumps({"null_control_ffr_gate": hostile}, allow_nan=True)
    manifest = json.loads((cassette / MANIFEST_NAME).read_text())
    manifest["null_control_ffr_gate"] = json.loads(text)["null_control_ffr_gate"]
    (cassette / MANIFEST_NAME).write_bytes(
        (json.dumps(manifest, sort_keys=True, indent=2, allow_nan=True) + "\n").encode()
    )
    outcome = verify_path(cassette)
    assert outcome.state is State.CASSETTE_CORRUPTED
    assert outcome.exit_code == 2


def test_an_enormous_epsilon_cannot_move_the_threshold_out_of_reach(cassette: Path) -> None:
    """`exceeds_threshold` adds epsilon to the right-hand side, so a large enough one makes
    every comparison answer "within" — a fail-open switch that appears nowhere in the
    rendered report."""
    _patch_manifest(cassette, lambda m: m["policy"]["numeric"].__setitem__("epsilon", 1e9))
    outcome = verify_path(cassette, mode=Mode.GATE)
    assert outcome.state is State.CASSETTE_CORRUPTED
    assert outcome.exit_code == 2


def test_gate_refuses_a_policy_the_candidate_chose(cassette: Path) -> None:
    """The cassette arrives from the pull request under review, and it carries the policy.

    Letting that policy decide a blocking outcome would let the author of a regression pick
    the bar they are measured against: raise `friction_threshold` to 9 and the same evidence
    passes. `gate` compares the deciding fields against the evaluator's own and refuses.
    """

    def raise_thresholds(manifest: dict) -> None:
        manifest["policy"]["decision"]["friction_threshold"] = 9.0
        manifest["policy"]["decision"]["component_hard_max"] = 9.0
        manifest["policy"]["metrics"]["clamp_ratio"] = 10.0

    _patch_manifest(cassette, raise_thresholds)
    # The report no longer follows from the altered policy, so verify stops at step 5;
    # what matters here is that gate refuses before it ever computes a verdict.
    assert verify_path(cassette, mode=Mode.GATE).state is State.GATE_PRECONDITION_UNMET
    assert verify_path(cassette, mode=Mode.GATE).exit_code == 2


def test_a_treatment_without_a_null_control_is_incomplete(cassette: Path) -> None:
    """Omitting the null is not a way to skip the null check. The number the treatment
    would be judged against was never collected, so there is nothing to interpret."""
    _patch_manifest(cassette, lambda m: m.__setitem__("null_control_ffr_gate", None))
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INCOMPLETE
    assert outcome.exit_code == 2


def test_an_extra_file_cannot_be_admitted_by_listing_its_digest(cassette: Path) -> None:
    """The allowed file set is derived from `runs[].files` plus the two reports.

    Deriving it from the digest table instead would make the rule circular: anything at all
    becomes permitted by vouching for it, and a cassette could ship a notes file full of
    real paths while still verifying.
    """
    payload = b"/home/someone/secret/path.py: rm -rf --no-preserve-root /\n"
    (cassette / "notes.txt").write_bytes(payload)
    _patch_manifest(cassette, lambda m: m["digests"].__setitem__("notes.txt", digest_of(payload)))
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INCOMPLETE


def test_one_run_cannot_be_counted_as_two_repetitions(cassette: Path) -> None:
    """Pair 1 is repointed at pair 0's evidence. Every digest still matches and every
    per-run invariant still holds; only a uniqueness check catches that the experiment
    measured one run and claimed two."""

    def duplicate_pair_zero(manifest: dict) -> None:
        by_id = {run["run_id"]: run for run in manifest["runs"]}
        for source, target in (("r0", "r2"), ("r1", "r3")):
            by_id[target]["files"] = dict(by_id[source]["files"])
            by_id[target]["run_id"] = source
        # Withdraw the now-unreferenced evidence so the file set stays self-consistent.
        # Without this the cassette fails one step earlier on structure, and the
        # uniqueness rule — the thing under test — never runs.
        for name in [n for n in manifest["digests"] if n.startswith(("r2.", "r3."))]:
            del manifest["digests"][name]

    _patch_manifest(cassette, duplicate_pair_zero)
    for stale in list(cassette.glob("r2.*")) + list(cassette.glob("r3.*")):
        stale.unlink()

    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "more than one" in outcome.detail


def test_a_run_cannot_claim_success_the_launcher_log_denies(cassette: Path) -> None:
    """Success is the one fact the verdict turns on — a pair counts only when both arms
    succeeded. Recording exit codes rather than a bare count is what makes it re-derivable
    instead of an assertion by whoever built the cassette."""
    failed = encode_json(LauncherRecord(exit_codes=(0, 1)).model_dump(mode="json"))
    _rewrite(cassette, "r1.tests.json", failed, fix_digest=True)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "launcher log" in outcome.detail


def test_strict_promotes_a_reproduced_degraded_verdict(tmp_path: Path) -> None:
    """Degraded data survives reproduction: the report matches, the verdict is simply built
    on fewer components than planned. Folding that into EVIDENCE_REPRODUCED would make
    `--strict` silently do nothing, which is worse than not offering the flag."""
    equal_arms = (10, 1, 10)  # test cycles below the small-sample floor on both sides
    degraded = _build(tmp_path / "degraded", baseline=equal_arms, candidate=equal_arms)

    relaxed = verify_path(degraded)
    assert relaxed.assessment is not None
    assert relaxed.assessment.state is State.DEGRADED_DATA
    assert relaxed.state is State.EVIDENCE_REPRODUCED
    assert relaxed.exit_code == 0

    strict = verify_path(degraded, strict=True)
    assert strict.state is State.DEGRADED_DATA
    assert strict.exit_code == 1


def test_an_oversized_file_is_refused_before_it_is_read(cassette: Path) -> None:
    """A cassette is fetched from a pull request and read wholly into memory to be hashed.
    Without a ceiling, a large enough file kills the verifier before a single digest is
    checked — which turns hostile evidence into a dead process rather than into exit 2."""
    from morrow.adapters.cassette.store import MAX_FILE_BYTES

    (cassette / "r1.events.jsonl").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    outcome = verify_path(cassette)
    assert outcome.state is State.INFRASTRUCTURE_ERROR
    assert outcome.exit_code == 2


# The cassettes committed under ``cassettes/`` are covered separately, in
# ``test_published_cassettes.py`` — that file is a regression test on published artifacts,
# this one is a test of the verification logic.
