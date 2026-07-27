"""MORROW command line entry point.

The exit codes are part of the contract — see docs/architecture/evidence.md §4.2 for the
full state table.

    show      summarise a cassette without judging it
    verify    re-derive the verdict from recorded evidence and compare it to what was recorded
    gate      recompute from the cassette and fail the build on a friction finding

These are the modes that exist. Recording an experiment is done by
``scripts/record_one.py``.

Evidence, infrastructure, trust-boundary and not-comparable errors exit 2 in *every*
mode. "Could not measure" is never reported as "nothing wrong".

The exit code is produced by the domain's ``enforce`` table, never assembled here. This
layer chooses what to print; it does not get a vote on the verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from morrow.adapters.cassette.verify import Verification, verify_path
from morrow.adapters.report.render import verdict_label
from morrow.domain.assessment import Mode

app = typer.Typer(
    name="morrow",
    help="Measure whether today's pull request makes tomorrow's changes harder.",
    no_args_is_help=True,
    add_completion=False,
)

_CASSETTE_ARGUMENT = typer.Argument(
    exists=True,
    file_okay=False,
    dir_okay=True,
    readable=True,
    help="Directory holding manifest.json and the recorded evidence.",
)
_STRICT_OPTION = typer.Option(
    "--strict/--no-strict",
    help="Promote DEGRADED_DATA from pass to failure.",
)
_REPORT_OPTION = typer.Option(
    "--report",
    dir_okay=False,
    writable=True,
    help="Write the regenerated Markdown report here (e.g. $GITHUB_STEP_SUMMARY).",
)


def _emit(outcome: Verification, report_path: Path | None) -> None:
    """Print the outcome, then write the regenerated report if one was asked for.

    Ordering matters on a failure: the reason goes to the terminal even when the report
    cannot be written, because the reason is the part a human needs.
    """
    label = verdict_label(outcome.exit_result)
    typer.echo(f"{label} · {outcome.state.value} · exit {outcome.exit_code}")
    if outcome.detail:
        typer.echo(outcome.detail)
    if outcome.assessment is not None and outcome.assessment.ffr_gate is not None:
        typer.echo(f"FFR_gate {outcome.assessment.ffr_gate:.4f}")
    if report_path is not None and outcome.report_markdown is not None:
        # An unwritable report path must not become the exit code. The verdict is already
        # decided and already printed; letting an OSError escape here would replace a
        # deliberate 0/1/2 with whatever the traceback produces — and a verify that
        # reproduced its evidence would exit 1, which reads as a friction finding.
        try:
            report_path.write_text(outcome.report_markdown, encoding="utf-8")
        except OSError as error:
            typer.echo(f"could not write the report to {report_path}: {error}", err=True)
        else:
            typer.echo(f"report written to {report_path}")


def _column(values: list[str], width: int) -> str:
    return "".join(value.rjust(width) for value in values)


def _detail_line(detail: str) -> str:
    """The reason, trimmed to the part a reader needs on one line."""
    # `verify` prefixes its detail with how the reproduction went; `show` has already said
    # the state, so only the reason it gives is interesting here.
    marker = "the re-derived verdict is "
    if marker in detail:
        detail = detail.split(marker, 1)[1]
        if ": " in detail:
            detail = detail.split(": ", 1)[1]
    return detail


@app.command()
def show(cassette: Annotated[Path, _CASSETTE_ARGUMENT]) -> None:
    """Summarise a cassette: what it recorded, and what that evidence decides.

    The ratios and the verdict are re-derived rather than read out of the recorded report:
    the point of a cassette is that its numbers can be recomputed, and a viewer that
    trusted the report would be showing the one thing nobody needs to take on faith.

    The null-control figure is the exception, and it is printed as what it is — a number
    imported from a different experiment, which this cassette cannot re-derive.

    Exits 0 whenever the cassette could be read and summarised, whatever the verdict is.
    Judging is what ``verify`` and ``gate`` are for; this only looks.
    """
    outcome = verify_path(cassette)
    manifest, assessment = outcome.manifest, outcome.assessment
    if manifest is None or assessment is None:
        typer.echo(f"cannot summarise: {outcome.state.value} — {outcome.detail}", err=True)
        raise typer.Exit(2)

    policy = manifest.policy
    typer.echo("")
    typer.echo(
        f"  {manifest.experiment_id}  ·  {manifest.kind.value}"
        f"  ·  {manifest.model.value}  ·  {assessment.valid_pair_count} pairs"
    )
    typer.echo("")
    typer.echo(f"  {assessment.state.value}")
    typer.echo(f"  {_detail_line(outcome.detail)}")
    typer.echo("")

    components = sorted(policy.metrics.weights)
    width = max(len(name.value) for name in components) + 3
    header = "  pair" + _column([name.value for name in components], width)
    typer.echo(header)
    typer.echo("  " + "─" * (len(header) - 2))

    experiment = outcome.experiment
    if experiment is not None:
        from morrow.domain.friction import component_ratio, is_small_sample

        for pair in sorted(experiment.successful_pairs, key=lambda p: p.pair_id):
            cells: list[str] = []
            for name in components:
                base, cand = pair.baseline[name], pair.candidate[name]
                if is_small_sample(base, cand, floor=policy.metrics.small_sample_floor):
                    cells.append("small-sample")
                else:
                    ratio = component_ratio(
                        base,
                        cand,
                        alpha=policy.metrics.alpha,
                        clamp_ratio=policy.metrics.clamp_ratio,
                    )
                    cells.append(f"{ratio:.4f}")
            typer.echo(f"  {pair.pair_id:>4}" + _column(cells, width))

    typer.echo("")
    if assessment.ffr_gate is not None:
        typer.echo(
            f"  FFR_gate     {assessment.ffr_gate:.4f}"
            f"    threshold {policy.decision.friction_threshold:.4f}"
        )
    else:
        typer.echo("  FFR_gate     not reported — the experiment was invalidated")
    if manifest.null_control_ffr_gate is not None:
        typer.echo(
            f"  null control {manifest.null_control_ffr_gate:.4f}"
            f"    band      {policy.null_control.maximum_ffr:.4f}"
        )
    typer.echo("")


@app.command()
def verify(
    cassette: Annotated[Path, _CASSETTE_ARGUMENT],
    strict: Annotated[bool, _STRICT_OPTION] = False,
    report: Annotated[Path | None, _REPORT_OPTION] = None,
) -> None:
    """Re-derive the verdict from a cassette and compare it to the recorded report.

    Exits 0 only when every digest matched, the evidence passed validation, and the
    regenerated report is byte-identical to the recorded one. Anything else is exit 2.
    """
    outcome = verify_path(cassette, mode=Mode.VERIFY, strict=strict)
    _emit(outcome, report)
    raise typer.Exit(outcome.exit_code)


@app.command()
def gate(
    cassette: Annotated[Path, _CASSETTE_ARGUMENT],
    strict: Annotated[bool, _STRICT_OPTION] = False,
    report: Annotated[Path | None, _REPORT_OPTION] = None,
) -> None:
    """Recompute the verdict from a cassette and block the build on a friction finding.

    The recorded report is never read (evidence.md §4.6): the decision comes from the
    evidence, and the report this prints is regenerated from that same recomputation.
    """
    outcome = verify_path(cassette, mode=Mode.GATE, strict=strict)
    _emit(outcome, report)
    raise typer.Exit(outcome.exit_code)


@app.command()
def version() -> None:
    """Print the MORROW version."""
    from morrow import __version__

    typer.echo(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()
