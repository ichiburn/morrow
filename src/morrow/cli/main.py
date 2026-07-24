"""MORROW command line entry point.

Three modes, and the exit codes are part of the contract — see
docs/architecture/evidence.md §4.2 for the full state table.

    measure   run the experiment and record the evidence; friction findings are advisory
    verify    re-derive the verdict from recorded evidence and compare it to what was recorded
    gate      recompute from the cassette and fail the build on a friction finding

Evidence, infrastructure, trust-boundary and not-comparable errors exit 2 in *every*
mode. "Could not measure" is never reported as "nothing wrong".
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="morrow",
    help="Measure whether today's pull request makes tomorrow's changes harder.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the MORROW version."""
    from morrow import __version__

    typer.echo(__version__)


if __name__ == "__main__":  # pragma: no cover
    app()
