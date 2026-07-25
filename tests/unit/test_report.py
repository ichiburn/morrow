"""Report rendering: PASS/BLOCK/ERROR headings, every ``r[i,p]`` (not just the
median), the invalid-pair breakdown, the null control beside the treatment, and
byte-reproducible JSON.

Assessments are produced by the real pipeline (``validate_experiment`` ->
``evaluate_policy`` -> ``enforce``) rather than hand-built, so the report is exercised
against the same shapes the decision layer actually emits.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Mapping, Sequence

import pytest

from morrow.adapters.report import (
    EvidenceMode,
    InvalidPair,
    NullControlOutcome,
    ReportMeta,
    render_json,
    render_markdown,
    verdict_label,
)
from morrow.domain.assessment import (
    Assessment,
    ExitResult,
    Mode,
    State,
    enforce,
    evaluate_policy,
    validate_experiment,
)
from morrow.domain.metrics import ComponentName, RawPairMeasurement, ValidatedExperiment
from morrow.domain.policy import default_policy

A = ComponentName.FILES_READ_DISTINCT
B = ComponentName.TEST_CYCLES
C = ComponentName.FINAL_CHURN
POLICY = default_policy()


def _raw(
    pair_id: int,
    baseline: Mapping[ComponentName, float],
    candidate: Mapping[ComponentName, float],
    *,
    baseline_success: bool = True,
    candidate_success: bool = True,
    regression: bool = False,
) -> RawPairMeasurement:
    return RawPairMeasurement(
        pair_id=pair_id,
        baseline_success=baseline_success,
        candidate_success=candidate_success,
        regression_detected=regression,
        baseline=baseline,
        candidate=candidate,
    )


def _uniform(value: float) -> dict[ComponentName, float]:
    return {A: value, B: value, C: value}


def _pipeline(
    raws: Sequence[RawPairMeasurement], mode: Mode = Mode.GATE
) -> tuple[ValidatedExperiment, Assessment, ExitResult]:
    experiment = validate_experiment(raws, POLICY)
    assert isinstance(experiment, ValidatedExperiment)
    assessment = evaluate_policy(experiment, POLICY)
    return experiment, assessment, enforce(mode, assessment)


def _meta(**overrides: object) -> ReportMeta:
    base: dict[str, object] = {
        "policy": POLICY,
        "evidence_mode": EvidenceMode.LIVE,
        "experiment_id": "exp-001",
        "scenario_id": "coupling",
        "provider": "claude_code",
        "model": "claude-x",
    }
    base.update(overrides)
    return ReportMeta(**base)  # type: ignore[arg-type]


# --- Fixtures for the three headline verdicts ------------------------------


def _block_case() -> tuple[ValidatedExperiment, Assessment, ExitResult]:
    """Candidate uniformly worse: r[i]=1.75 on each axis -> FFR_gate 1.75 > 1.50."""
    raws = [_raw(p, _uniform(3), _uniform(6)) for p in range(4)]
    return _pipeline(raws, Mode.GATE)


def _pass_case() -> tuple[ValidatedExperiment, Assessment, ExitResult]:
    raws = [_raw(p, _uniform(5), _uniform(5)) for p in range(4)]
    return _pipeline(raws, Mode.GATE)


def _inconclusive_case() -> tuple[ValidatedExperiment, Assessment, ExitResult]:
    """Baseline established, candidate succeeds once: only one successful pair, which
    is below minimum_ffr_pairs -> INCONCLUSIVE (exit 2, ERROR)."""
    raws = [
        _raw(0, _uniform(5), _uniform(6), candidate_success=True),
        _raw(1, _uniform(5), _uniform(6), candidate_success=False),
        _raw(2, _uniform(5), _uniform(6), candidate_success=False),
        _raw(3, _uniform(5), _uniform(6), candidate_success=False),
    ]
    return _pipeline(raws, Mode.GATE)


# --- Verdict labelling ------------------------------------------------------


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(0, "PASS"), (1, "BLOCK"), (2, "ERROR"), (7, "ERROR")],
)
def test_verdict_label_maps_exit_code(exit_code: int, expected: str) -> None:
    result = ExitResult(
        mode=Mode.GATE, state=State.OK, exit_code=exit_code, advisory=False, strict=False
    )
    assert verdict_label(result) == expected


def test_block_verdict_heading_and_json() -> None:
    experiment, assessment, exit_result = _block_case()
    assert assessment.state is State.FRICTION_REGRESSION
    assert exit_result.exit_code == 1

    md = render_markdown(experiment, assessment, exit_result, _meta())
    assert md.startswith("# MORROW — BLOCK")
    assert "**Primary reason:**" in md
    assert "`FRICTION_REGRESSION`" in md

    data = json.loads(render_json(experiment, assessment, exit_result, _meta()))
    assert data["verdict"]["label"] == "BLOCK"
    assert data["verdict"]["exit_code"] == 1
    assert data["ffr"]["exceeds_threshold"] is True
    assert data["ffr"]["gate"] == pytest.approx(1.75)


def test_pass_verdict_heading_and_json() -> None:
    experiment, assessment, exit_result = _pass_case()
    assert assessment.state is State.OK
    assert exit_result.exit_code == 0

    md = render_markdown(experiment, assessment, exit_result, _meta())
    assert md.startswith("# MORROW — PASS")

    data = json.loads(render_json(experiment, assessment, exit_result, _meta()))
    assert data["verdict"]["label"] == "PASS"
    assert data["ffr"]["exceeds_threshold"] is False


def test_error_verdict_inconclusive() -> None:
    experiment, assessment, exit_result = _inconclusive_case()
    assert assessment.state is State.INCONCLUSIVE
    assert exit_result.exit_code == 2

    md = render_markdown(experiment, assessment, exit_result, _meta())
    assert md.startswith("# MORROW — ERROR")
    # FFR is not computed for INCONCLUSIVE; the section must say so, not fabricate a value.
    assert "FFR was not computed" in md

    data = json.loads(render_json(experiment, assessment, exit_result, _meta()))
    assert data["verdict"]["label"] == "ERROR"
    assert data["ffr"]["computed"] is False
    assert data["ffr"]["gate"] is None


# --- Every r[i,p] is shown, not only the median -----------------------------


def _scatter_case() -> tuple[ValidatedExperiment, Assessment, ExitResult]:
    """A on the AB/BA pattern so median-of-ratios (2.84) differs sharply from the
    ratio of per-variant medians (1.0); B and C held equal at 5."""
    raws = [
        _raw(0, {A: 1, B: 5, C: 5}, {A: 10, B: 5, C: 5}),
        _raw(1, {A: 1, B: 5, C: 5}, {A: 10, B: 5, C: 5}),
        _raw(2, {A: 10, B: 5, C: 5}, {A: 1, B: 5, C: 5}),
        _raw(3, {A: 10, B: 5, C: 5}, {A: 1, B: 5, C: 5}),
    ]
    return _pipeline(raws, Mode.GATE)


def test_every_pair_ratio_present_not_only_median() -> None:
    experiment, assessment, exit_result = _scatter_case()

    # Fixture sanity: median of the pair ratios is not the ratio of the medians.
    high = (10 + 1) / (1 + 1)  # 5.5
    low = (1 + 1) / (10 + 1)  # 0.1818...
    expected_median = statistics.median([high, high, low, low])
    assert assessment.component_ratios[A] == pytest.approx(expected_median)
    assert assessment.component_ratios[A] != pytest.approx(1.0)

    md = render_markdown(experiment, assessment, exit_result, _meta())

    # All four per-pair ratios must appear (both the 5.5 and the 0.1818 values),
    # proving the table is per-pair and not a single median row.
    assert md.count(f"{high:.4f}") >= 2
    assert md.count(f"{low:.4f}") >= 2
    # One row per successful pair, id 0..3.
    per_pair_section = md.split("## Per-pair ratios")[1].split("## Trial breakdown")[0]
    data_rows = [ln for ln in per_pair_section.splitlines() if ln.startswith("| ") and " | " in ln]
    # header + separator + 4 data rows.
    assert len([ln for ln in data_rows if ln.split("|")[1].strip().isdigit()]) == 4

    data = json.loads(render_json(experiment, assessment, exit_result, _meta()))
    a_component = next(c for c in data["components"] if c["name"] == A.value)
    reported = sorted(pr["ratio"] for pr in a_component["pair_ratios"])
    assert reported == pytest.approx(sorted([high, high, low, low]))
    # And the JSON's per-pair values match what the decision layer computed.
    assert reported == pytest.approx(sorted(assessment.pair_ratios[A]))


# --- Invalid pairs are counted with their reasons ---------------------------


def test_invalid_pairs_counted_with_reasons() -> None:
    experiment, assessment, exit_result = _pass_case()  # 4 valid pairs
    invalid = (
        InvalidPair(pair_id=7, reason="worktree creation failed"),
        InvalidPair(pair_id=2, reason="API outage mid-run"),
    )
    meta = _meta(invalid_pairs=invalid)

    md = render_markdown(experiment, assessment, exit_result, meta)
    assert "| Pairs attempted (N) | 6 |" in md
    assert "| Valid (M) | 4 |" in md
    assert "| Invalid (K) | 2 |" in md
    assert "worktree creation failed" in md
    assert "API outage mid-run" in md
    # Reasons are ordered by pair id, deterministically.
    assert md.index("pair 2:") < md.index("pair 7:")

    data = json.loads(render_json(experiment, assessment, exit_result, meta))
    assert data["pairs"]["attempted"] == 6
    assert data["pairs"]["valid"] == 4
    assert [p["pair_id"] for p in data["pairs"]["invalid"]] == [2, 7]


# --- Null control shown on the same screen ----------------------------------


@pytest.mark.parametrize(
    ("null_ffr", "within"),
    [(1.05, True), (1.20, True), (1.30, False)],
)
def test_null_control_within_band(null_ffr: float, within: bool) -> None:
    experiment, assessment, exit_result = _block_case()
    meta = _meta(null_control=NullControlOutcome(ffr_gate=null_ffr))

    md = render_markdown(experiment, assessment, exit_result, meta)
    assert "## Null control" in md
    assert f"| Null FFR_gate | {null_ffr:.4f} |" in md
    assert f"| Within band | {'yes' if within else 'no'} |" in md

    data = json.loads(render_json(experiment, assessment, exit_result, meta))
    assert data["null_control"]["ffr_gate"] == pytest.approx(null_ffr)
    assert data["null_control"]["within_band"] is within


def test_null_control_absent() -> None:
    experiment, assessment, exit_result = _block_case()
    md = render_markdown(experiment, assessment, exit_result, _meta())
    assert "No null control was provided" in md
    data = json.loads(render_json(experiment, assessment, exit_result, _meta()))
    assert data["null_control"] is None


# --- Component medians vs r[i], and the small-sample DEGRADED path ----------


def test_degraded_component_dropped_and_marked() -> None:
    """A is small-sample on every pair (both sides below floor 3) so it is dropped;
    B and C carry the gate. The report must mark A dropped, keep B/C ratios, and the
    per-pair table must show A's cells as small-sample."""
    raws = [_raw(p, {A: 1, B: 5, C: 5}, {A: 1, B: 5, C: 5}) for p in range(4)]
    experiment, assessment, exit_result = _pipeline(raws, Mode.GATE)
    assert assessment.state is State.DEGRADED_DATA
    assert A in assessment.dropped_components

    md = render_markdown(experiment, assessment, exit_result, _meta())
    assert "dropped (small-sample)" in md
    # A's per-pair cells are small-sample, B/C are real ratios.
    per_pair = md.split("## Per-pair ratios")[1].split("## Trial breakdown")[0]
    assert "small-sample" in per_pair

    data = json.loads(render_json(experiment, assessment, exit_result, _meta()))
    a_component = next(c for c in data["components"] if c["name"] == A.value)
    assert a_component["dropped"] is True
    assert a_component["ratio"] is None
    assert all(cell["small_sample"] is True for cell in a_component["pair_ratios"])
    b_component = next(c for c in data["components"] if c["name"] == B.value)
    assert b_component["dropped"] is False
    assert b_component["ratio"] == pytest.approx(1.0)


def test_component_table_reports_both_medians_and_ratio() -> None:
    experiment, assessment, exit_result = _block_case()
    md = render_markdown(experiment, assessment, exit_result, _meta())
    # Baseline median 3, candidate median 6, r[i] 1.75 for each component.
    assert "| `files_read_distinct` | 3 | 6 | 1.7500 | no |" in md
    assert "not the ratio of the two medians" in md


# --- ADAPTATION_REGRESSION: no successful pairs -----------------------------


def test_adaptation_regression_no_successful_pairs() -> None:
    raws = [_raw(p, _uniform(5), _uniform(9), candidate_success=False) for p in range(4)]
    experiment, assessment, exit_result = _pipeline(raws, Mode.GATE)
    assert assessment.state is State.ADAPTATION_REGRESSION
    assert exit_result.exit_code == 1

    md = render_markdown(experiment, assessment, exit_result, _meta())
    assert md.startswith("# MORROW — BLOCK")
    assert "No successful pairs" in md

    data = json.loads(render_json(experiment, assessment, exit_result, _meta()))
    assert data["success"]["successful_pairs"] == 0
    assert data["ffr"]["computed"] is False


# --- Framing: K, the no-significance line, evidence mode, trace id ----------


def test_statistical_disclaimer_and_k_present() -> None:
    experiment, assessment, exit_result = _block_case()
    md = render_markdown(experiment, assessment, exit_result, _meta())
    assert "No statistical significance is claimed" in md
    assert "K = 4 pairs" in md

    data = json.loads(render_json(experiment, assessment, exit_result, _meta()))
    assert data["repetitions_k"] == 4
    assert data["statistical_significance_claimed"] is False


def test_evidence_mode_and_trace_id() -> None:
    experiment, assessment, exit_result = _block_case()
    meta = _meta(evidence_mode=EvidenceMode.REPLAY, trace_id="abc123def456")
    md = render_markdown(experiment, assessment, exit_result, meta)
    assert "Evidence `replay`" in md
    assert "SigNoz trace id: `abc123def456`" in md

    data = json.loads(render_json(experiment, assessment, exit_result, meta))
    assert data["evidence_mode"] == "replay"
    assert data["trace_id"] == "abc123def456"


def test_trace_id_absent_is_stated() -> None:
    experiment, assessment, exit_result = _block_case()
    md = render_markdown(experiment, assessment, exit_result, _meta())
    assert "SigNoz trace id: not available" in md


# --- JSON byte reproducibility ----------------------------------------------


def test_json_is_byte_identical_from_same_input() -> None:
    experiment, assessment, exit_result = _block_case()
    meta = _meta(
        null_control=NullControlOutcome(ffr_gate=1.05),
        invalid_pairs=(InvalidPair(pair_id=9, reason="crash"),),
        trace_id="deadbeef",
    )
    first = render_json(experiment, assessment, exit_result, meta)
    second = render_json(experiment, assessment, exit_result, meta)
    assert first == second
    assert first.encode("ascii") == second.encode("ascii")


def test_json_formatting_contract() -> None:
    experiment, assessment, exit_result = _block_case()
    raw = render_json(experiment, assessment, exit_result, _meta())
    # LF only, exactly one trailing newline.
    assert "\r" not in raw
    assert raw.endswith("\n")
    assert not raw.endswith("\n\n")
    # Pure ASCII (opaque ids and enums only).
    raw.encode("ascii")

    # Every object's keys are in lexicographic order (RFC-8785-equivalent ordering).
    def _require_sorted(pairs: list[tuple[str, object]]) -> dict[str, object]:
        keys = [key for key, _ in pairs]
        assert keys == sorted(keys), f"unsorted object keys: {keys}"
        return dict(pairs)

    json.loads(raw, object_pairs_hook=_require_sorted)


def test_json_floats_stay_numeric() -> None:
    experiment, assessment, exit_result = _block_case()
    data = json.loads(render_json(experiment, assessment, exit_result, _meta()))
    # Floats are JSON numbers, not stringified.
    assert isinstance(data["ffr"]["gate"], float)
    assert isinstance(data["ffr"]["threshold"], float)
    a_component = next(c for c in data["components"] if c["name"] == A.value)
    assert isinstance(a_component["ratio"], float)
    assert isinstance(a_component["pair_ratios"][0]["ratio"], float)
