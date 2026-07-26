"""MORROW command line entry point.

Three modes, and the exit codes are part of the contract — see
docs/architecture/evidence.md §4.2 for the full state table.

    measure   run the experiment and record the evidence; friction findings are advisory
    verify    re-derive the verdict from recorded evidence and compare it to what was recorded
    gate      recompute from the cassette and fail the build on a friction finding

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
