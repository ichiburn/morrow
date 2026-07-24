"""Contract tests for the Claude Code stream-json normalizer.

The fixture is a real capture, not a hand-written sample: 76 lines from an actual
``claude -p --output-format stream-json`` run, with host-specific paths rewritten. If the
provider changes its output shape, these tests fail rather than letting a metric quietly
under-count.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from morrow.adapters.claude.stream import ClaudeStreamNormalizer
from morrow.domain.events import (
    CommandPurpose,
    EventKind,
    KnownExecutable,
    KnownModel,
    RawKind,
    StopReason,
    TerminalReason,
)

FIXTURE = Path(__file__).parent / "fixtures" / "claude_stream_capture.jsonl"
WORKSPACE = Path("/workspace")

#: The documented breakdown in docs/architecture/design.md §1.2. Both this table and the
#: total are asserted, so a partial edit of the fixture cannot pass silently.
EXPECTED_PROVIDER_KINDS: dict[tuple[str, str | None], int] = {
    ("system", "thinking_tokens"): 30,
    ("assistant", None): 22,
    ("user", None): 10,
    ("system", "commands_changed"): 4,
    ("system", "hook_started"): 3,
    ("system", "hook_response"): 3,
    ("system", "notification"): 1,
    ("system", "init"): 1,
    ("result", "success"): 1,
    ("rate_limit_event", None): 1,
}

_REF_PATTERNS = {
    "run_id": re.compile(r"^r[0-9]{1,3}$"),
    "tool_ref": re.compile(r"^t[0-9]{1,4}$"),
    "path_ref": re.compile(r"^p[0-9]{1,4}$"),
    "session_ref": re.compile(r"^s[0-9]{1,3}$"),
}

#: Every string-valued field on a published event must be one of these enums or one of
#: the opaque reference patterns below. Adding a field without adding it here fails the
#: leak test, which is the intent: a new free-form field should not slip in unnoticed.
_ENUM_FIELDS = {
    "kind": EventKind,
    "raw_kind": RawKind,
    "stop_reason": StopReason,
    "terminal_reason": TerminalReason,
    "purpose": CommandPurpose,
    "executable": KnownExecutable,
    "model": KnownModel,
}


@pytest.fixture
def lines() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def _normalize(lines: list[str]):
    normalizer = ClaudeStreamNormalizer(run_id="r0", workspace=WORKSPACE)
    events, audit = normalizer.normalize(iter(lines))
    return normalizer, events, audit


def test_fixture_matches_the_documented_provider_breakdown(lines: list[str]) -> None:
    counts = Counter[tuple[str, str | None]]()
    for line in lines:
        record = json.loads(line)
        counts[(record["type"], record.get("subtype"))] += 1

    assert dict(counts) == EXPECTED_PROVIDER_KINDS
    assert sum(counts.values()) == 76


def test_no_provider_event_is_unrecognised(lines: list[str]) -> None:
    """An unknown provider kind is a data-quality signal, never a silent drop.

    If Claude Code renames or adds an event we measure, ``unknown_raw_kinds`` becomes
    non-zero here instead of the metric quietly shrinking.
    """
    _, events, audit = _normalize(lines)

    assert audit.unknown_raw_kinds == 0
    assert audit.unparsable_lines == 0
    assert not any(e.raw_kind is RawKind.UNKNOWN for e in events)


def test_every_tool_use_is_paired_with_its_result(lines: list[str]) -> None:
    _, events, audit = _normalize(lines)

    assert audit.unpaired_tool_uses == 0
    assert audit.orphaned_tool_results == 0
    assert audit.duplicate_tool_ids == 0

    tool_events = [e for e in events if e.tool_ref is not None]
    assert len(tool_events) == 10
    # Every paired call has a confirmed outcome; none is left as an assumption.
    assert all(e.success is not None for e in tool_events)


def test_tool_calls_are_classified_by_what_they_cost(lines: list[str]) -> None:
    _, events, _ = _normalize(lines)
    kinds = Counter(e.kind for e in events)

    assert kinds[EventKind.FILE_READ] == 2
    assert kinds[EventKind.PATCH] == 3
    assert kinds[EventKind.COMMAND] == 3
    assert kinds[EventKind.SESSION_START] == 1
    assert kinds[EventKind.COMPLETION] == 1


def test_direct_test_invocations_are_counted_not_ignored(lines: list[str]) -> None:
    """This capture predates the fixed launcher, so both pytest runs are direct.

    They must not be counted as test cycles, and they must not vanish either: a run that
    bypasses the launcher produces incomplete evidence rather than a cheaper-looking
    candidate.
    """
    _, events, audit = _normalize(lines)

    assert audit.direct_test_invocations == 2
    assert not any(e.kind is EventKind.TEST for e in events)
    direct = [
        e
        for e in events
        if e.kind is EventKind.COMMAND and e.purpose is CommandPurpose.DIRECT_TEST  # type: ignore[union-attr]
    ]
    assert len(direct) == 2
    assert all(e.success is False for e in direct)  # both were blocked by the host sandbox


def test_completion_carries_the_measured_totals(lines: list[str]) -> None:
    _, events, _ = _normalize(lines)
    (completion,) = [e for e in events if e.kind is EventKind.COMPLETION]

    assert completion.num_turns == 11
    assert completion.output_tokens == 5064
    assert completion.api_duration_ms == 74829
    assert completion.cost_micro_usd == 589_990  # 0.58999 USD, held as an integer
    assert completion.stop_reason is StopReason.END_TURN
    assert completion.terminal_reason is TerminalReason.COMPLETED
    assert completion.permission_denial_count == 2
    assert completion.success is True


def test_seq_is_zero_based_and_contiguous(lines: list[str]) -> None:
    _, events, _ = _normalize(lines)
    assert [e.seq for e in events] == list(range(len(events)))


def test_no_free_form_string_reaches_a_published_event(lines: list[str]) -> None:
    """The whole point of the closed DTO: nothing arbitrary crosses the boundary.

    Every string in a serialized event is either an enum member or an opaque reference
    matching its bounded pattern. A path, a shell command body or a secret in an argument
    has no field to land in.
    """
    _, events, _ = _normalize(lines)

    for event in events:
        for field, value in event.model_dump(mode="json").items():
            if not isinstance(value, str):
                continue
            if field in _ENUM_FIELDS:
                assert value in {m.value for m in _ENUM_FIELDS[field]}, f"{field}={value!r}"
            elif field in _REF_PATTERNS:
                assert _REF_PATTERNS[field].match(value), f"{field}={value!r}"
            else:
                pytest.fail(f"unexpected free-form string field {field!r} = {value!r}")


def test_real_paths_stay_on_the_evaluator_side(lines: list[str]) -> None:
    normalizer, events, _ = _normalize(lines)

    # The registry knows the real paths ...
    assert len(normalizer.refs.paths) == 3
    assert any("calc.py" in real for real in normalizer.refs.paths.mapping)

    # ... and none of them appear in the events.
    serialized = json.dumps([e.model_dump(mode="json") for e in events])
    assert "calc.py" not in serialized
    assert "workspace" not in serialized


def test_normalization_is_byte_reproducible(lines: list[str]) -> None:
    def render() -> str:
        _, events, _ = _normalize(lines)
        return "\n".join(
            json.dumps(e.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            for e in events
        )

    assert render() == render()
