"""Render a finished measurement into the two published report surfaces.

Two functions, one shape of truth. :func:`render_markdown` produces text meant to
be pasted straight into ``GITHUB_STEP_SUMMARY``; :func:`render_json` produces the
same facts machine-readably. Both read the already-decided
:class:`~morrow.domain.assessment.Assessment` and its
:class:`~morrow.domain.assessment.ExitResult`, never recomputing the verdict — the
report is a view of the decision, not a second decider (evidence.md §4.6).

Design choices worth stating, because a report that overclaims is worse than none:

* **The verdict label comes from the exit code, not the state name.** ``0 -> PASS``,
  ``1 -> BLOCK``, ``2 -> ERROR``; an unrecognised code fails closed to ``ERROR`` so
  a report never paints an unknown outcome green.
* **Every ``r[i,p]`` is shown, never only the median.** A four-pair sample at K=4 can
  scatter widely, and how much it scatters is information the reader must judge
  (measurement.md §3.1, risks §16). The per-pair table is recomputed from the
  successful pairs with the same domain functions the gate used, so the displayed
  ``exceeds``/``within`` calls match the actual decision rather than a rounded copy
  of it.
* **``r[i]`` is the median of the per-pair ratios, not the ratio of the medians.**
  The component table shows both medians *and* ``r[i]`` and says in a footnote that
  the two are not related by division — collapsing them is exactly the bug §3.1
  calls out.
* **No statistical significance is claimed.** The intro carries the C6 sentence
  verbatim in spirit: at K=4 a sign test cannot reach significance, so all that is
  reported is an observation measured against a concurrently collected null control
  under a rule fixed beforehand (design.md §0.1, measurement.md §3.8).
* **The JSON is byte-reproducible.** Keys are sorted, numbers stay numeric (never
  stringified), non-finite values are rejected (``allow_nan=False``), the separator
  is LF, and there is exactly one trailing newline. The same input yields the same
  bytes on any machine.

The accompanying facts a report needs but the domain types do not carry — the
evidence mode, the SigNoz trace id, the attempted/invalid pair breakdown, the null
control's headline number — arrive in :class:`ReportMeta`. It also carries the
:class:`~morrow.domain.policy.Policy` so the thresholds and metric parameters have a
single source of truth rather than being copied into this layer.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from morrow.domain.assessment import Assessment, ExitResult, State
from morrow.domain.friction import component_ratio, exceeds_threshold, is_small_sample
from morrow.domain.metrics import ComponentName, PairMeasurement, ValidatedExperiment
from morrow.domain.policy import Policy


class EvidenceMode(StrEnum):
    """Where the evidence came from: a live agent run, or a replayed cassette."""

    LIVE = "live"
    REPLAY = "replay"


@dataclass(frozen=True)
class InvalidPair:
    """A pair invalidated at the infra level (§3.2), retained with its reason.

    Invalid pairs are excluded from the :class:`ValidatedExperiment`, so their count
    and reason cannot be recovered from the domain types alone — they are reported
    from here so "N attempted, M valid, K invalid (reason)" stays honest (§12)."""

    pair_id: int
    reason: str


@dataclass(frozen=True)
class NullControlOutcome:
    """The null control's headline result, presented beside the treatment (§3.8).

    Only ``ffr_gate`` is carried; the tolerance band is ``maximum_ffr`` on the same
    policy, so it is read from there rather than duplicated here. The null sits on
    the same screen as a reference for the rule's validity, not as an input to it."""

    ffr_gate: float


@dataclass(frozen=True)
class ReportMeta:
    """Everything a report needs beyond the three domain objects.

    ``policy`` is held here so thresholds (``friction_threshold`` /
    ``component_hard_max`` / ``maximum_ffr``) and metric parameters (``alpha`` /
    ``clamp_ratio`` / ``small_sample_floor``) have one source of truth instead of
    being copied into the adapter."""

    policy: Policy
    evidence_mode: EvidenceMode
    experiment_id: str
    scenario_id: str
    provider: str
    model: str
    invalid_pairs: tuple[InvalidPair, ...] = ()
    null_control: NullControlOutcome | None = None
    trace_id: str | None = None


@dataclass(frozen=True)
class _Cell:
    """One ``r[i,p]`` in the per-pair table. ``ratio`` is ``None`` exactly when the
    pair was small-sample for this component and its ratio was not computed."""

    pair_id: int
    ratio: float | None
    small_sample: bool


_VERDICT_LABEL: dict[int, str] = {0: "PASS", 1: "BLOCK", 2: "ERROR"}
_DEFAULT_REASON: dict[State, str] = {
    State.OK: "no friction finding",
    State.DEGRADED_DATA: "some components dropped as small-sample",
}


def verdict_label(exit_result: ExitResult) -> str:
    """PASS / BLOCK / ERROR from the exit code. Unknown codes fail closed to ERROR."""
    return _VERDICT_LABEL.get(exit_result.exit_code, "ERROR")


def _primary_reason(assessment: Assessment) -> str:
    """The detail of the finding matching the primary verdict, else a default."""
    for finding in assessment.findings:
        if finding.state is assessment.state and finding.detail:
            return finding.detail
    return _DEFAULT_REASON.get(assessment.state, assessment.state.value)


def _fmt(value: float) -> str:
    """A ratio / FFR / threshold, fixed to four decimals so a near-threshold
    difference is not hidden by rounding."""
    return f"{value:.4f}"


def _fmt_count(value: float) -> str:
    """A count median: an integer when whole, one decimal when the even-K median
    lands between two counts."""
    return str(int(value)) if value == int(value) else f"{value:.1f}"


def _components(policy: Policy) -> list[ComponentName]:
    """The fixed component set (§3.3), in a deterministic order for tables."""
    return sorted(policy.metrics.weights)


def _successful_sorted(experiment: ValidatedExperiment) -> list[PairMeasurement]:
    """The successful pairs — the set FFR is computed over (§3.7) — by pair id."""
    return sorted(experiment.successful_pairs, key=lambda p: p.pair_id)


def _count_medians(
    pairs: Sequence[PairMeasurement], name: ComponentName
) -> tuple[float, float] | None:
    """Median baseline and candidate counts for one component over ``pairs``.

    Descriptive only: this is *not* how ``r[i]`` is computed (that is the median of
    per-pair ratios), which is why the report keeps them in separate columns."""
    if not pairs:
        return None
    baseline = statistics.median([p.baseline[name] for p in pairs])
    candidate = statistics.median([p.candidate[name] for p in pairs])
    return float(baseline), float(candidate)


def _pair_cells(
    pairs: Sequence[PairMeasurement], name: ComponentName, policy: Policy
) -> list[_Cell]:
    """Recompute ``r[i,p]`` for one component across ``pairs``.

    Reuses the domain friction functions rather than reimplementing the ratio, so a
    cell equals the value the gate saw; small-sample pairs are marked, not dropped
    silently."""
    cells: list[_Cell] = []
    for pair in pairs:
        baseline = pair.baseline[name]
        candidate = pair.candidate[name]
        if is_small_sample(baseline, candidate, floor=policy.metrics.small_sample_floor):
            cells.append(_Cell(pair.pair_id, None, True))
        else:
            ratio = component_ratio(
                baseline,
                candidate,
                alpha=policy.metrics.alpha,
                clamp_ratio=policy.metrics.clamp_ratio,
            )
            cells.append(_Cell(pair.pair_id, ratio, False))
    return cells


def _statistical_disclaimer(k: int) -> str:
    return (
        f"No statistical significance is claimed. At K={k} a sign test cannot "
        "establish significance (one-sided p floor 1/16 = 0.0625). This is an "
        "observation measured against a concurrently collected null control, under "
        "a decision rule fixed before the treatment data was seen."
    )


def render_markdown(
    experiment: ValidatedExperiment,
    assessment: Assessment,
    exit_result: ExitResult,
    meta: ReportMeta,
) -> str:
    """Render the GitHub-step-summary Markdown report.

    Guarantees the report contains, for every verdict: the PASS/BLOCK/ERROR heading
    and primary reason; a per-component median table; every ``r[i,p]`` per pair; the
    ``FFR_gate`` vs threshold comparison; the null control beside it; the
    attempted/valid/invalid breakdown with reasons; K and the no-significance line;
    the evidence mode; and the SigNoz trace id when present.
    """
    policy = meta.policy
    epsilon = policy.numeric.epsilon
    threshold = policy.decision.friction_threshold
    hard_max = policy.decision.component_hard_max
    k = policy.experiment.runs_per_variant
    label = verdict_label(exit_result)
    successful = _successful_sorted(experiment)
    components = _components(policy)

    lines: list[str] = []

    # --- Heading + framing (items 1, 7, 9) ---------------------------------
    lines.append(f"# MORROW — {label}")
    lines.append("")
    lines.append(f"**Primary reason:** {_primary_reason(assessment)} (`{assessment.state.value}`)")
    lines.append("")
    advisory = " (advisory)" if exit_result.advisory else ""
    lines.append(
        f"- Mode `{exit_result.mode.value}`{advisory} · "
        f"Evidence `{meta.evidence_mode.value}` · "
        f"Experiment `{meta.experiment_id}` · Scenario `{meta.scenario_id}`"
    )
    lines.append(f"- Provider `{meta.provider}` · Model `{meta.model}`")
    lines.append(f"- Repetitions: K = {k} pairs")
    lines.append(f"- {_statistical_disclaimer(k)}")
    lines.append("")

    # --- Verdict + findings ------------------------------------------------
    lines.append("## Verdict")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Verdict | {label} |")
    lines.append(f"| Exit code | {exit_result.exit_code} |")
    lines.append(f"| State | `{assessment.state.value}` |")
    lines.append(f"| Mode | `{exit_result.mode.value}` |")
    lines.append(f"| Advisory | {'yes' if exit_result.advisory else 'no'} |")
    lines.append(f"| Strict | {'yes' if exit_result.strict else 'no'} |")
    lines.append("")
    if assessment.findings:
        lines.append("Findings:")
        lines.append("")
        for finding in assessment.findings:
            detail = f": {finding.detail}" if finding.detail else ""
            lines.append(f"- `{finding.state.value}`{detail}")
        lines.append("")

    # --- Future Friction Ratio (item 4) ------------------------------------
    lines.append("## Future Friction Ratio")
    lines.append("")
    if assessment.ffr_gate is None:
        lines.append("FFR was not computed for this verdict (no FFR-eligible successful pairs).")
        lines.append("")
        lines.append(f"- Threshold (`friction_threshold`): {_fmt(threshold)}")
        lines.append("")
    else:
        exceeds = exceeds_threshold(assessment.ffr_gate, threshold, epsilon)
        relation = ">" if exceeds else "<="
        verdict_word = "exceeds threshold" if exceeds else "within threshold"
        lines.append("| Quantity | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| FFR_gate (one-sided) | {_fmt(assessment.ffr_gate)} |")
        lines.append(f"| Threshold (`friction_threshold`) | {_fmt(threshold)} |")
        lines.append(f"| Comparison | FFR_gate {relation} threshold — {verdict_word} |")
        if assessment.ffr_display is not None:
            lines.append(f"| FFR_display (two-sided) | {_fmt(assessment.ffr_display)} |")
        lines.append("")

    # --- Component medians + r[i] (item 2) ---------------------------------
    lines.append("## Components — median over successful pairs")
    lines.append("")
    lines.append(
        f"| Component | Baseline median | Candidate median | r[i] | "
        f"Over hard-max ({_fmt(hard_max)})? |"
    )
    lines.append("| --- | --- | --- | --- | --- |")
    for name in components:
        medians = _count_medians(successful, name)
        baseline_median = "—" if medians is None else _fmt_count(medians[0])
        candidate_median = "—" if medians is None else _fmt_count(medians[1])
        if name in assessment.dropped_components:
            ri, over = "dropped (small-sample)", "—"
        elif name in assessment.component_ratios:
            ratio = assessment.component_ratios[name]
            ri = _fmt(ratio)
            over = "yes" if exceeds_threshold(ratio, hard_max, epsilon) else "no"
        else:
            ri, over = "—", "—"
        lines.append(
            f"| `{name.value}` | {baseline_median} | {candidate_median} | {ri} | {over} |"
        )
    lines.append("")
    lines.append(
        "> r[i] is the median of the per-pair ratios, not the ratio of the two "
        "medians in this table."
    )
    lines.append("")

    # --- Every r[i,p] (item 3) ---------------------------------------------
    lines.append("## Per-pair ratios r[i,p]")
    lines.append("")
    if not successful:
        lines.append("No successful pairs, so no per-pair ratios were computed.")
        lines.append("")
    else:
        cells_by_component = {name: _pair_cells(successful, name, policy) for name in components}
        lines.append("| Pair | " + " | ".join(f"`{name.value}`" for name in components) + " |")
        lines.append("| --- | " + " | ".join("---" for _ in components) + " |")
        for index, pair in enumerate(successful):
            row = [str(pair.pair_id)]
            for name in components:
                cell = cells_by_component[name][index]
                row.append("small-sample" if cell.ratio is None else _fmt(cell.ratio))
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # --- Trial breakdown (item 6) ------------------------------------------
    valid = assessment.valid_pair_count
    invalid = sorted(meta.invalid_pairs, key=lambda p: p.pair_id)
    lines.append("## Trial breakdown")
    lines.append("")
    lines.append("| Metric | Count |")
    lines.append("| --- | --- |")
    lines.append(f"| Pairs attempted (N) | {valid + len(invalid)} |")
    lines.append(f"| Valid (M) | {valid} |")
    lines.append(f"| Invalid (K) | {len(invalid)} |")
    lines.append(f"| Successful (both arms) | {assessment.successful_pair_count} |")
    lines.append(f"| Baseline successes | {assessment.baseline_success_count} |")
    lines.append(f"| Candidate successes | {assessment.candidate_success_count} |")
    lines.append("")
    if invalid:
        lines.append("Invalid pairs (retained with reason):")
        lines.append("")
        for invalid_pair in invalid:
            lines.append(f"- pair {invalid_pair.pair_id}: {invalid_pair.reason}")
        lines.append("")

    # --- Null control on the same screen (item 5) --------------------------
    lines.append("## Null control")
    lines.append("")
    if meta.null_control is None:
        lines.append("No null control was provided for this run.")
        lines.append("")
    else:
        null_ffr = meta.null_control.ffr_gate
        max_ffr = policy.null_control.maximum_ffr
        within = not exceeds_threshold(null_ffr, max_ffr, epsilon)
        lines.append("| Quantity | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Null FFR_gate | {_fmt(null_ffr)} |")
        lines.append(f"| Tolerance (`maximum_ffr`) | {_fmt(max_ffr)} |")
        lines.append(f"| Within band | {'yes' if within else 'no'} |")
        lines.append("")

    # --- Evidence + trace (items 8, 9) -------------------------------------
    lines.append("## Evidence")
    lines.append("")
    lines.append(f"- Evidence mode: `{meta.evidence_mode.value}`")
    if meta.trace_id is not None:
        lines.append(f"- SigNoz trace id: `{meta.trace_id}`")
    else:
        lines.append("- SigNoz trace id: not available")

    return "\n".join(lines).rstrip("\n") + "\n"


def _pair_ratio_json(cells: Sequence[_Cell]) -> list[dict[str, object]]:
    return [
        {"pair_id": cell.pair_id, "ratio": cell.ratio, "small_sample": cell.small_sample}
        for cell in cells
    ]


def render_json(
    experiment: ValidatedExperiment,
    assessment: Assessment,
    exit_result: ExitResult,
    meta: ReportMeta,
) -> str:
    """Render the machine-readable report.

    Byte-reproducible by construction: ``sort_keys`` orders every object's keys,
    numbers stay numeric, ``allow_nan=False`` rejects any non-finite value, and the
    output is LF-terminated with a single trailing newline. The same input always
    produces the same bytes.
    """
    policy = meta.policy
    epsilon = policy.numeric.epsilon
    threshold = policy.decision.friction_threshold
    hard_max = policy.decision.component_hard_max
    successful = _successful_sorted(experiment)
    components = _components(policy)

    component_payload: list[dict[str, object]] = []
    for name in components:
        medians = _count_medians(successful, name)
        dropped = name in assessment.dropped_components
        ratio = assessment.component_ratios.get(name)
        over_hard_max = (
            ratio is not None and not dropped and exceeds_threshold(ratio, hard_max, epsilon)
        )
        component_payload.append(
            {
                "baseline_median": None if medians is None else medians[0],
                "candidate_median": None if medians is None else medians[1],
                "dropped": dropped,
                "name": name.value,
                "over_hard_max": over_hard_max,
                "pair_ratios": _pair_ratio_json(_pair_cells(successful, name, policy)),
                "ratio": None if dropped else ratio,
            }
        )

    ffr_gate = assessment.ffr_gate
    ffr_payload: dict[str, object] = {
        "computed": ffr_gate is not None,
        "display": assessment.ffr_display,
        "exceeds_threshold": (
            ffr_gate is not None and exceeds_threshold(ffr_gate, threshold, epsilon)
        ),
        "gate": ffr_gate,
        "threshold": threshold,
    }

    null_payload: dict[str, object] | None = None
    if meta.null_control is not None:
        null_ffr = meta.null_control.ffr_gate
        max_ffr = policy.null_control.maximum_ffr
        null_payload = {
            "ffr_gate": null_ffr,
            "maximum_ffr": max_ffr,
            "within_band": not exceeds_threshold(null_ffr, max_ffr, epsilon),
        }

    valid = assessment.valid_pair_count
    invalid = sorted(meta.invalid_pairs, key=lambda p: p.pair_id)

    payload: dict[str, object] = {
        "components": component_payload,
        "evidence_mode": meta.evidence_mode.value,
        "experiment_id": meta.experiment_id,
        "ffr": ffr_payload,
        "findings": [
            {"detail": finding.detail, "state": finding.state.value}
            for finding in assessment.findings
        ],
        "model": meta.model,
        "null_control": null_payload,
        "pairs": {
            "attempted": valid + len(invalid),
            "invalid": [{"pair_id": p.pair_id, "reason": p.reason} for p in invalid],
            "valid": valid,
        },
        "provider": meta.provider,
        "repetitions_k": policy.experiment.runs_per_variant,
        "scenario_id": meta.scenario_id,
        "statistical_significance_claimed": False,
        "success": {
            "baseline": assessment.baseline_success_count,
            "candidate": assessment.candidate_success_count,
            "successful_pairs": assessment.successful_pair_count,
            "valid_pairs": valid,
        },
        "trace_id": meta.trace_id,
        "verdict": {
            "advisory": exit_result.advisory,
            "exit_code": exit_result.exit_code,
            "label": verdict_label(exit_result),
            "mode": exit_result.mode.value,
            "primary_reason": _primary_reason(assessment),
            "state": assessment.state.value,
            "strict": exit_result.strict,
        },
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
