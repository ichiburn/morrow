"""Normalize Claude Code's ``--output-format stream-json`` into MORROW events.

Grounded in a real capture, not in guesswork: a 76-line stream from an actual run is
committed as a fixture and its per-kind counts are asserted in the contract tests. See
``docs/architecture/design.md`` §1.2 for the breakdown.

Two properties matter more than convenience here.

*Nothing unbounded escapes.* Tool inputs carry file paths, shell command bodies and
patch text. None of that crosses into an event — paths become opaque references, commands
become an enum pair, and patch bodies are simply not read.

*The output is byte-reproducible.* Events are ordered by ``(source_line_index,
content_index)`` and renumbered, so the same stream always produces the same file. Provider
timestamps are dropped rather than synthesized when absent.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from morrow.adapters.commands import classify
from morrow.adapters.refs import RefRegistry
from morrow.domain.events import (
    AgentEvent,
    CommandEvent,
    CommandPurpose,
    CompletionEvent,
    FileReadEvent,
    KnownModel,
    NormalizationAudit,
    OpaqueEvent,
    PatchEvent,
    PlanEvent,
    RawKind,
    SearchEvent,
    SessionStartEvent,
    StopReason,
    TerminalReason,
    TestEvent,
    ToolOtherEvent,
)

# Tool name -> what it means for the measurement.
_READ_TOOLS = frozenset({"Read", "NotebookRead"})
_SEARCH_TOOLS = frozenset({"Grep", "Glob"})
_PATCH_TOOLS = frozenset({"Edit", "Write", "NotebookEdit", "MultiEdit"})
_PLAN_TOOLS = frozenset({"TodoWrite", "ExitPlanMode"})
_SHELL_TOOLS = frozenset({"Bash", "BashOutput"})

_SYSTEM_SUBTYPE_TO_RAW_KIND: dict[str, RawKind] = {
    "init": RawKind.INIT,
    "thinking_tokens": RawKind.THINKING_TOKENS,
    "notification": RawKind.NOTIFICATION,
    "commands_changed": RawKind.COMMANDS_CHANGED,
    "hook_started": RawKind.HOOK,
    "hook_response": RawKind.HOOK,
}

_STOP_REASONS = {r.value for r in StopReason}
_TERMINAL_REASONS = {r.value for r in TerminalReason}


class _Pending:
    """A tool_use waiting for its tool_result."""

    __slots__ = ("content_index", "kind_payload", "line_index", "tool_ref")

    def __init__(
        self, line_index: int, content_index: int, tool_ref: str, kind_payload: dict[str, Any]
    ) -> None:
        self.line_index = line_index
        self.content_index = content_index
        self.tool_ref = tool_ref
        self.kind_payload = kind_payload


def _model_of(raw: str) -> KnownModel:
    lowered = raw.lower()
    if "opus" in lowered:
        return KnownModel.CLAUDE_OPUS
    if "sonnet" in lowered:
        return KnownModel.CLAUDE_SONNET
    if "haiku" in lowered:
        return KnownModel.CLAUDE_HAIKU
    return KnownModel.OTHER


def _relative_path(raw: str, workspace: Path) -> str:
    """Reduce a provider path to a workspace-relative POSIX path.

    Provider paths are absolute in practice, and the absolute prefix differs between the
    baseline and candidate workspaces. Comparing or hashing them unreduced would make the
    two sides look different for no reason. A path outside the workspace keeps only its
    final component, since its location is not ours to record.
    """
    candidate = Path(raw)
    if not candidate.is_absolute():
        return PurePosixPath(candidate.as_posix()).as_posix()
    try:
        return candidate.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return PurePosixPath(candidate.name).as_posix()


def _content_items(message: Any) -> Sequence[Any]:
    if not isinstance(message, dict):
        return ()
    content = message.get("content")
    return content if isinstance(content, list) else ()


class ClaudeStreamNormalizer:
    """Turns one run's stream-json into ordered, closed events plus an audit record."""

    def __init__(self, run_id: str, workspace: Path, registry: RefRegistry | None = None) -> None:
        self._run_id = run_id
        self._workspace = workspace
        self._refs = registry if registry is not None else RefRegistry()
        self._pending: dict[str, _Pending] = {}
        self._emitted: list[tuple[tuple[int, int], dict[str, Any]]] = []
        self._launcher_count = 0
        self._total_lines = 0
        self._unparsable = 0
        self._unknown_kinds = 0
        self._orphaned_results = 0
        self._duplicate_tool_ids = 0
        self._direct_tests = 0
        self._unclassifiable = 0
        self._unpaired = 0

    # -- public ---------------------------------------------------------------------

    @property
    def refs(self) -> RefRegistry:
        """Evaluator-side mapping from real values to references. Never published."""
        return self._refs

    def normalize(self, lines: Iterator[str]) -> tuple[list[AgentEvent], NormalizationAudit]:
        for line_index, line in enumerate(lines):
            if not line.strip():
                continue
            self._total_lines += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                self._unparsable += 1
                continue
            if isinstance(record, dict):
                self._dispatch(line_index, record)
            else:
                self._unknown_kinds += 1

        self._flush_pending()
        return self._build(), self._audit()

    # -- dispatch -------------------------------------------------------------------

    def _dispatch(self, line_index: int, record: dict[str, Any]) -> None:
        record_type = record.get("type")
        if record_type == "assistant":
            self._on_assistant(line_index, record)
        elif record_type == "user":
            self._on_user(line_index, record)
        elif record_type == "result":
            self._on_result(line_index, record)
        elif record_type == "system":
            self._on_system(line_index, record)
        elif record_type == "rate_limit_event":
            self._push(line_index, 0, {"kind": "opaque", "raw_kind": RawKind.RATE_LIMIT})
        else:
            self._unknown_kinds += 1
            self._push(line_index, 0, {"kind": "opaque", "raw_kind": RawKind.UNKNOWN})

    def _on_system(self, line_index: int, record: dict[str, Any]) -> None:
        subtype = record.get("subtype")
        if subtype == "init":
            session_id = str(record.get("session_id", ""))
            self._push(
                line_index,
                0,
                {
                    "kind": "session_start",
                    "raw_kind": RawKind.INIT,
                    "session_ref": self._refs.sessions.ref(session_id),
                    "model": _model_of(str(record.get("model", ""))),
                },
            )
            return
        raw_kind = _SYSTEM_SUBTYPE_TO_RAW_KIND.get(str(subtype))
        if raw_kind is None:
            self._unknown_kinds += 1
            raw_kind = RawKind.UNKNOWN
        self._push(line_index, 0, {"kind": "opaque", "raw_kind": raw_kind})

    def _on_assistant(self, line_index: int, record: dict[str, Any]) -> None:
        for content_index, item in enumerate(_content_items(record.get("message"))):
            if not isinstance(item, dict):
                continue
            if item.get("type") != "tool_use":
                self._push(
                    line_index,
                    content_index,
                    {"kind": "opaque", "raw_kind": RawKind.ASSISTANT_TEXT},
                )
                continue
            self._on_tool_use(line_index, content_index, item)

    def _on_tool_use(self, line_index: int, content_index: int, item: dict[str, Any]) -> None:
        tool_id = str(item.get("id", ""))
        if self._refs.tools.known(tool_id):
            self._duplicate_tool_ids += 1
        tool_ref = self._refs.tools.ref(tool_id)
        payload = self._payload_for_tool(str(item.get("name", "")), item.get("input"))
        self._pending[tool_id] = _Pending(line_index, content_index, tool_ref, payload)

    def _payload_for_tool(self, name: str, tool_input: Any) -> dict[str, Any]:
        params = tool_input if isinstance(tool_input, dict) else {}

        if name in _READ_TOOLS:
            return {
                "kind": "file_read",
                "raw_kind": RawKind.ASSISTANT_TOOL_USE,
                "path_ref": self._path_ref(params.get("file_path") or params.get("notebook_path")),
            }
        if name in _PATCH_TOOLS:
            return {
                "kind": "patch",
                "raw_kind": RawKind.ASSISTANT_TOOL_USE,
                "path_ref": self._path_ref(params.get("file_path") or params.get("notebook_path")),
            }
        if name in _SEARCH_TOOLS:
            return {"kind": "search", "raw_kind": RawKind.ASSISTANT_TOOL_USE}
        if name in _PLAN_TOOLS:
            return {"kind": "plan", "raw_kind": RawKind.ASSISTANT_TOOL_USE}
        if name in _SHELL_TOOLS:
            return self._payload_for_shell(params.get("command"))
        return {"kind": "tool_other", "raw_kind": RawKind.ASSISTANT_TOOL_USE}

    def _payload_for_shell(self, command: Any) -> dict[str, Any]:
        result = classify(command if isinstance(command, str) else "")
        if result.purpose is CommandPurpose.TEST_LAUNCHER:
            launcher_seq = self._launcher_count
            self._launcher_count += 1
            return {
                "kind": "test",
                "raw_kind": RawKind.ASSISTANT_TOOL_USE,
                "launcher_seq": launcher_seq,
            }
        if result.purpose is CommandPurpose.DIRECT_TEST:
            self._direct_tests += 1
        elif result.purpose is CommandPurpose.UNCLASSIFIABLE:
            self._unclassifiable += 1
        return {
            "kind": "command",
            "raw_kind": RawKind.ASSISTANT_TOOL_USE,
            "executable": result.executable,
            "purpose": result.purpose,
        }

    def _path_ref(self, raw: Any) -> str:
        text = raw if isinstance(raw, str) and raw else "<unknown>"
        return self._refs.paths.ref(_relative_path(text, self._workspace))

    def _on_user(self, line_index: int, record: dict[str, Any]) -> None:
        for item in _content_items(record.get("message")):
            if not isinstance(item, dict) or item.get("type") != "tool_result":
                continue
            tool_id = str(item.get("tool_use_id", ""))
            pending = self._pending.pop(tool_id, None)
            if pending is None:
                self._orphaned_results += 1
                continue
            payload = dict(pending.kind_payload)
            payload["tool_ref"] = pending.tool_ref
            payload["success"] = not bool(item.get("is_error"))
            self._push(pending.line_index, pending.content_index, payload)

    def _on_result(self, line_index: int, record: dict[str, Any]) -> None:
        raw_usage = record.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        denials = record.get("permission_denials")
        cost = record.get("total_cost_usd")
        stop_reason = str(record.get("stop_reason", ""))
        terminal_reason = str(record.get("terminal_reason", ""))
        self._push(
            line_index,
            0,
            {
                "kind": "completion",
                "raw_kind": RawKind.RESULT,
                "success": not bool(record.get("is_error")),
                "num_turns": _non_negative_int(record.get("num_turns")),
                "output_tokens": _non_negative_int(usage.get("output_tokens")),
                "api_duration_ms": _non_negative_int(record.get("duration_api_ms")),
                "cost_micro_usd": _micro_usd(cost),
                "stop_reason": (
                    StopReason(stop_reason) if stop_reason in _STOP_REASONS else StopReason.UNKNOWN
                ),
                "terminal_reason": (
                    TerminalReason(terminal_reason)
                    if terminal_reason in _TERMINAL_REASONS
                    else TerminalReason.UNKNOWN
                ),
                "permission_denial_count": len(denials) if isinstance(denials, list) else 0,
            },
        )

    # -- assembly -------------------------------------------------------------------

    def _flush_pending(self) -> None:
        """Tool calls whose result never arrived keep ``success = None``.

        They are not assumed to have succeeded. The count goes into the audit record so
        that a truncated stream is visible as incomplete evidence rather than as a run
        that happened to use fewer tools.
        """
        self._unpaired = len(self._pending)
        for pending in self._pending.values():
            payload = dict(pending.kind_payload)
            payload["tool_ref"] = pending.tool_ref
            payload["success"] = None
            self._push(pending.line_index, pending.content_index, payload)
        self._pending.clear()

    def _push(self, line_index: int, content_index: int, payload: dict[str, Any]) -> None:
        self._emitted.append(((line_index, content_index), payload))

    def _build(self) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        for seq, (_, payload) in enumerate(sorted(self._emitted, key=lambda item: item[0])):
            events.append(_construct(seq, self._run_id, payload))
        return events

    def _audit(self) -> NormalizationAudit:
        return NormalizationAudit(
            total_provider_lines=self._total_lines,
            unparsable_lines=self._unparsable,
            unknown_raw_kinds=self._unknown_kinds,
            unpaired_tool_uses=self._unpaired,
            orphaned_tool_results=self._orphaned_results,
            duplicate_tool_ids=self._duplicate_tool_ids,
            direct_test_invocations=self._direct_tests,
            unclassifiable_commands=self._unclassifiable,
        )


_CONSTRUCTORS: dict[str, Any] = {
    "session_start": SessionStartEvent,
    "plan": PlanEvent,
    "search": SearchEvent,
    "file_read": FileReadEvent,
    "patch": PatchEvent,
    "command": CommandEvent,
    "test": TestEvent,
    "tool_other": ToolOtherEvent,
    "completion": CompletionEvent,
    "opaque": OpaqueEvent,
}


def _construct(seq: int, run_id: str, payload: dict[str, Any]) -> Any:
    fields = {k: v for k, v in payload.items() if k != "kind"}
    return _CONSTRUCTORS[payload["kind"]](seq=seq, run_id=run_id, **fields)


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, int(value))


def _micro_usd(value: Any) -> int:
    """USD as an integer count of micro-dollars; no float reaches an event."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, round(float(value) * 1_000_000))
