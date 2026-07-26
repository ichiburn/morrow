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

# Aliased: pytest tries to collect anything named Test* as a test class.
from morrow.domain.events import TestEvent as LauncherRunEvent
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


def _events(run_id: str, *, reads: int, tests: int) -> bytes:
    """A minimal well-formed stream: a session start, N distinct reads, M test runs through
    the launcher, and a completion.

    The test events have to be here rather than implied: the verifier cross-checks them
    against the launcher log, because the log alone is what `test_cycles` is read from.
    """
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
    for index in range(tests):
        events.append(
            LauncherRunEvent(
                seq=reads + 1 + index,
                run_id=run_id,
                raw_kind=RawKind.ASSISTANT_TOOL_USE,
                tool_ref=f"t{reads + index}",
                success=True,
                launcher_seq=index,
            )
        )
    events.append(
        CompletionEvent(
            seq=reads + tests + 1,
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
        names.events: _events(run_id, reads=reads, tests=tests),
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
    # The fixture runs two pairs, so its sample-size floors are lower than the published
    # ones; it is handed its own policy as the evaluator's. What is under test here is the
    # blocking behaviour, not the policy comparison — that has its own test below.
    outcome = verify_path(cassette, mode=Mode.GATE, evaluator_policy=_policy())
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
    assert verify_path(cassette, mode=Mode.GATE, evaluator_policy=_policy()).exit_code == 1


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
    outcome = verify_path(cassette, mode=Mode.GATE, evaluator_policy=_policy())
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

    # The fixture's own policy is handed in as the evaluator's, so the *only* difference
    # between the two is the thresholds this test raises. Without that, the fixture's
    # sample-size floors already differ from the published ones and the assertion would
    # pass even if the thresholds were dropped from the comparison entirely.
    baseline_policy = _policy()
    assert verify_path(cassette, mode=Mode.GATE, evaluator_policy=baseline_policy).state is (
        State.FRICTION_REGRESSION
    ), "the fixture must pass the policy check before the thresholds are altered"

    _patch_manifest(cassette, raise_thresholds)
    outcome = verify_path(cassette, mode=Mode.GATE, evaluator_policy=baseline_policy)
    assert outcome.state is State.GATE_PRECONDITION_UNMET
    assert outcome.exit_code == 2


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


def test_an_unconvertible_churn_count_is_refused_not_crashed(cassette: Path) -> None:
    """JSON integers have no width limit and Python's have no precision limit, so an
    unbounded count reaches `float()` and raises OverflowError — the same "crash instead of
    verdict" hole as a NaN, arriving through the integer side of the schema."""
    huge = encode_json(
        {
            "added_lines": 10**400,
            "deleted_lines": 0,
            "files_added": 1,
            "files_deleted": 0,
            "files_modified": 0,
            "binary_bytes_changed": 0,
            "binary_files_changed": 0,
        }
    )
    _rewrite(cassette, "r1.churn.json", huge, fix_digest=True)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert outcome.exit_code == 2


def test_a_discarded_attempt_cannot_smuggle_an_unchecked_file(cassette: Path) -> None:
    """The allowed file set is derived from every run entry, including retried attempts.

    A run nobody adopted would otherwise declare a file name, have it admitted by that
    derivation, and never be parsed — published, vouched for by a digest, and validated by
    nothing. Every run is checked; only the adopted ones contribute metrics.
    """
    payload = b"not an event stream at all\n"
    (cassette / "r9.events.jsonl").write_bytes(payload)

    def add_discarded_attempt(manifest: dict) -> None:
        template = dict(manifest["runs"][0])
        manifest["runs"].append(
            {
                **template,
                "run_id": "r9",
                "adopted": False,
                "attempt_index": 1,
                "files": {
                    "events": "r9.events.jsonl",
                    "churn": template["files"]["churn"],
                    "tests": template["files"]["tests"],
                },
            }
        )
        manifest["digests"]["r9.events.jsonl"] = digest_of(payload)

    _patch_manifest(cassette, add_discarded_attempt)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert outcome.exit_code == 2


def test_a_pair_cannot_be_both_invalidated_and_adopted(cassette: Path) -> None:
    """Invalidated pairs are counted in the report's "attempted" total, so claiming a pair
    on both sides inflates how much work the experiment appears to have done."""
    _patch_manifest(
        cassette,
        lambda m: m.__setitem__(
            "invalid_pairs", [{"pair_id": 0, "reason": "infrastructure_failure"}]
        ),
    )
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "both invalidated and adopted" in outcome.detail


def test_relabelling_a_treatment_does_not_skip_its_null_control(cassette: Path) -> None:
    """`kind` is a manifest assertion, so "a treatment must carry a null control" can be
    sidestepped by calling the treatment something else and dropping the number.

    `verify` catches it at step 5 — the null block vanishes from the report — but `gate`
    never reads the report, so it has to refuse the relabelling outright. §4.4 makes the
    null control an unconditional gate precondition.
    """

    def relabel(manifest: dict) -> None:
        manifest["kind"] = "null_control"
        manifest["null_control_ffr_gate"] = None

    _patch_manifest(cassette, relabel)
    gated = verify_path(cassette, mode=Mode.GATE, evaluator_policy=_policy())
    assert gated.state is State.GATE_PRECONDITION_UNMET
    assert gated.exit_code == 2
    # And verify still refuses, by a different route: the report no longer follows.
    assert verify_path(cassette).exit_code == 2


def test_a_truncated_stream_cannot_be_excused_by_the_manifest(cassette: Path) -> None:
    """`seq` only proves the events present are contiguous, so lopping off the tail and
    renumbering passes it — and a shorter trajectory is a cheaper one. The completion event
    is what proves the stream is whole, and an adopted run always needs one, whatever
    `terminal_status` claims."""
    lines = (cassette / "r1.events.jsonl").read_bytes().decode().splitlines()
    kept = [line for line in lines if '"completion"' not in line]
    _rewrite(cassette, "r1.events.jsonl", ("\n".join(kept) + "\n").encode(), fix_digest=True)
    _patch_manifest(
        cassette,
        lambda m: [
            run.__setitem__("terminal_status", "timeout")
            for run in m["runs"]
            if run["run_id"] == "r1"
        ],
    )
    outcome = verify_path(cassette)
    assert outcome.state in {State.EVIDENCE_INCOMPLETE, State.EVIDENCE_INVALID}
    assert outcome.exit_code == 2


def test_the_launcher_log_and_the_stream_have_to_agree(cassette: Path) -> None:
    """`test_cycles` is read from the launcher log alone, so shortening it lowers the
    component directly — while the test events sit in the stream contradicting it."""
    shortened = encode_json(LauncherRecord(exit_codes=(0,)).model_dump(mode="json"))
    _rewrite(cassette, "r1.tests.json", shortened, fix_digest=True)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "test event" in outcome.detail


def test_an_arm_cannot_be_run_until_a_cheap_result_appears(cassette: Path) -> None:
    """Every attempt is genuine and every invariant holds; the metric is still chosen after
    the fact. Capping retries is what keeps a retry policy from being best-of-N."""

    def add_many_attempts(manifest: dict) -> None:
        template = next(r for r in manifest["runs"] if r["run_id"] == "r1")
        manifest["runs"].append({**template, "attempt_index": 9, "adopted": False})

    _patch_manifest(cassette, add_many_attempts)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "retry cap" in outcome.detail


def test_the_completion_has_to_end_the_stream(cassette: Path) -> None:
    """Counting completions only proves the stream stops somewhere after one. Deleting
    everything past it and renumbering satisfies the count while still hiding work."""
    lines = (cassette / "r1.events.jsonl").read_bytes().decode().splitlines()
    completion = next(line for line in lines if '"completion"' in line)
    others = [line for line in lines if line != completion]
    reordered = [completion, *others]
    renumbered = []
    for seq, line in enumerate(reordered):
        record = json.loads(line)
        record["seq"] = seq
        renumbered.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
    _rewrite(
        cassette, "r1.events.jsonl", ("\n".join(renumbered) + "\n").encode(), fix_digest=True
    )
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INCOMPLETE
    assert "does not end with its completion" in outcome.detail


def test_the_manifest_cannot_call_a_failed_run_completed(cassette: Path) -> None:
    """`terminal_status` is the manifest's assertion; the completion event's
    `terminal_reason` is the provider's account. An adopted run needs both to say the run
    finished, or the assertion is doing work the evidence does not support."""
    lines = (cassette / "r1.events.jsonl").read_bytes().decode().splitlines()
    patched = []
    for line in lines:
        record = json.loads(line)
        if record.get("kind") == "completion":
            record["terminal_reason"] = "api_error"
        patched.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
    _rewrite(cassette, "r1.events.jsonl", ("\n".join(patched) + "\n").encode(), fix_digest=True)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "completion event says" in outcome.detail


def test_a_middle_attempt_cannot_be_quietly_dropped(cassette: Path) -> None:
    """Capping the highest attempt index alone would let an inconvenient attempt in the
    middle be deleted. How much work was actually done is part of what retaining an
    attempt is for."""

    def add_gapped_attempt(manifest: dict) -> None:
        template = next(r for r in manifest["runs"] if r["run_id"] == "r1")
        manifest["runs"].append({**template, "attempt_index": 2, "adopted": False})

    _patch_manifest(cassette, add_gapped_attempt)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "missing an attempt" in outcome.detail


def test_a_pair_cannot_be_invalidated_twice(cassette: Path) -> None:
    """Each invalid pair is counted once in the report's attempted total."""
    _patch_manifest(
        cassette,
        lambda m: m.__setitem__(
            "invalid_pairs",
            [
                {"pair_id": 7, "reason": "infrastructure_failure"},
                {"pair_id": 7, "reason": "retries_exhausted"},
            ],
        ),
    )
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "more than once" in outcome.detail


def test_two_test_events_cannot_share_a_launcher_index(cassette: Path) -> None:
    """The counts can match while the indices do not: two events both claiming launcher
    run 0 leave one real invocation unaccounted for."""
    lines = (cassette / "r1.events.jsonl").read_bytes().decode().splitlines()
    patched = []
    for line in lines:
        record = json.loads(line)
        if record.get("kind") == "test" and record["launcher_seq"] == 1:
            record["launcher_seq"] = 0
        patched.append(json.dumps(record, sort_keys=True, separators=(",", ":")))
    _rewrite(cassette, "r1.events.jsonl", ("\n".join(patched) + "\n").encode(), fix_digest=True)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "index the launcher log" in outcome.detail


def _extra_attempt(manifest: dict, **overrides: object) -> None:
    """Append a second attempt for r1, copying the first and applying overrides."""
    template = next(run for run in manifest["runs"] if run["run_id"] == "r1")
    manifest["runs"].append({**template, "attempt_index": 1, "adopted": False, **overrides})


def test_a_completed_attempt_cannot_be_discarded(cassette: Path) -> None:
    """Within the retry cap, running an arm twice and keeping the cheaper result is still
    choosing the metric after the fact. A discarded attempt has to be one that failed."""
    _patch_manifest(cassette, _extra_attempt)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "discarded rather than adopted" in outcome.detail


def test_a_discarded_run_cannot_be_relabelled_as_crashed(cassette: Path) -> None:
    """...and the rule above keys on `terminal_status`, which is a manifest assertion.

    Calling a run that plainly finished "crashed" would make it discardable and bring
    best-of-N back through the label. The stream's own account of how the run ended is the
    part that cannot be relabelled.
    """
    _patch_manifest(cassette, lambda m: _extra_attempt(m, terminal_status="crashed"))
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INVALID
    assert "completion event says the run finished" in outcome.detail


def test_a_pair_cannot_be_left_unaccounted_for(cassette: Path) -> None:
    """A pair whose numbers came out inconvenient could otherwise be left unadopted and
    unlisted: it drops out of the experiment without appearing in the attempted total or
    anywhere else, and the verdict is re-derived from what remains."""

    def orphan_the_second_pair(manifest: dict) -> None:
        for run in manifest["runs"]:
            if run["pair_id"] == 1:
                run["adopted"] = False
                # Also drop the completion, so the run is discardable and this test
                # reaches the accounting check rather than the completed-discard one.
                run["terminal_status"] = "timeout"

    _patch_manifest(cassette, orphan_the_second_pair)
    outcome = verify_path(cassette)
    assert outcome.state is State.EVIDENCE_INCOMPLETE
    assert "neither adopted nor declared invalid" in outcome.detail


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
