"""Report rendering adapter: turn a decided experiment into the two published
surfaces (``morrow-report.md`` / ``morrow-report.json``)."""

from __future__ import annotations

from morrow.adapters.report.render import (
    EvidenceMode,
    InvalidPair,
    NullControlOutcome,
    ReportMeta,
    render_json,
    render_markdown,
    verdict_label,
)

__all__ = [
    "EvidenceMode",
    "InvalidPair",
    "NullControlOutcome",
    "ReportMeta",
    "render_json",
    "render_markdown",
    "verdict_label",
]
