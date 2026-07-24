"""The normalized agent event model.

Every field here is closed. There is no free-form string and no arbitrary map, because
these events are published: they are committed to the repository as cassettes and exported
to SigNoz. A provider's raw tool input can contain anything at all — a heredoc, a
``python -c`` body, an API key in an argument, a source fragment in a path — so none of it
crosses this boundary. Paths and tool call identifiers are re-assigned as opaque references
whose mapping lives only on the evaluator side.

Ordering is decided by ``seq`` alone. Timestamps are deliberately absent: a value synthesized
when the provider omits one would make the same input produce different bytes. Per-run start
and end times live in the manifest instead.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

# --- opaque identifiers ------------------------------------------------------------------
# Constrained so an identifier cannot become a smuggling channel for arbitrary text.

RunId = Annotated[str, Field(pattern=r"^r[0-9]{1,3}$")]
ToolRef = Annotated[str, Field(pattern=r"^t[0-9]{1,4}$")]
PathRef = Annotated[str, Field(pattern=r"^p[0-9]{1,4}$")]
SessionRef = Annotated[str, Field(pattern=r"^s[0-9]{1,3}$")]


class RawKind(StrEnum):
    """The provider event shape a normalized event came from.

    ``UNKNOWN`` is kept distinct from the known-but-uninteresting kinds on purpose. If a
    provider adds or renames an event we care about, folding it into a generic "other"
    bucket would let the metric silently under-count. An unknown kind is a data-quality
    signal, not a shrug.
    """

    INIT = "init"
    ASSISTANT_TOOL_USE = "assistant_tool_use"
    TOOL_RESULT = "tool_result"
    RESULT = "result"
    # Known kinds that carry no measurement signal.
    THINKING_TOKENS = "thinking_tokens"
    RATE_LIMIT = "rate_limit"
    NOTIFICATION = "notification"
    HOOK = "hook"
    COMMANDS_CHANGED = "commands_changed"
    ASSISTANT_TEXT = "assistant_text"
    # Anything the parser did not recognise.
    UNKNOWN = "unknown"


KNOWN_UNINTERESTING_KINDS: frozenset[RawKind] = frozenset(
    {
        RawKind.THINKING_TOKENS,
        RawKind.RATE_LIMIT,
        RawKind.NOTIFICATION,
        RawKind.HOOK,
        RawKind.COMMANDS_CHANGED,
        RawKind.ASSISTANT_TEXT,
    }
)


class KnownExecutable(StrEnum):
    """Executables the classifier recognises. Unknown names never reach the artifact."""

    MORROW_TEST = "morrow_test"
    PYTEST = "pytest"
    PYTHON = "python"
    GIT = "git"
    UV = "uv"
    RUFF = "ruff"
    MYPY = "mypy"
    PIP = "pip"
    OTHER = "other"


class CommandPurpose(StrEnum):
    """What a shell command was for, decided inside the trust boundary.

    ``test_launcher`` is the fixed launcher MORROW places in the worktree; only those
    invocations count toward ``test_cycles``. ``direct_test`` means the agent ran a test
    runner without going through the launcher, which is a data-quality problem rather than
    a free measurement. ``unclassifiable`` is never silently treated as ``other`` — see
    ``docs/architecture/evidence.md`` §6.4.
    """

    TEST_LAUNCHER = "test_launcher"
    DIRECT_TEST = "direct_test"
    OTHER = "other"
    UNCLASSIFIABLE = "unclassifiable"


class StopReason(StrEnum):
    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    TOOL_USE = "tool_use"
    UNKNOWN = "unknown"


class TerminalReason(StrEnum):
    COMPLETED = "completed"
    API_ERROR = "api_error"
    BUDGET_EXCEEDED = "budget_exceeded"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class KnownModel(StrEnum):
    CLAUDE_OPUS = "claude_opus"
    CLAUDE_SONNET = "claude_sonnet"
    CLAUDE_HAIKU = "claude_haiku"
    OTHER = "other"


class EventKind(StrEnum):
    SESSION_START = "session_start"
    PLAN = "plan"
    SEARCH = "search"
    FILE_READ = "file_read"
    PATCH = "patch"
    COMMAND = "command"
    TEST = "test"
    TOOL_OTHER = "tool_other"
    COMPLETION = "completion"
    OPAQUE = "opaque"


class _EventBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: NonNegativeInt
    run_id: RunId
    tool_ref: ToolRef | None = None
    raw_kind: RawKind
    #: ``None`` means the outcome was never confirmed. It is never guessed.
    success: bool | None = None
    duration_ms: NonNegativeInt | None = None


class SessionStartEvent(_EventBase):
    kind: Literal[EventKind.SESSION_START] = EventKind.SESSION_START
    session_ref: SessionRef
    model: KnownModel


class PlanEvent(_EventBase):
    kind: Literal[EventKind.PLAN] = EventKind.PLAN


class SearchEvent(_EventBase):
    kind: Literal[EventKind.SEARCH] = EventKind.SEARCH


class FileReadEvent(_EventBase):
    kind: Literal[EventKind.FILE_READ] = EventKind.FILE_READ
    path_ref: PathRef


class PatchEvent(_EventBase):
    kind: Literal[EventKind.PATCH] = EventKind.PATCH
    path_ref: PathRef


class CommandEvent(_EventBase):
    kind: Literal[EventKind.COMMAND] = EventKind.COMMAND
    executable: KnownExecutable
    purpose: CommandPurpose


class TestEvent(_EventBase):
    kind: Literal[EventKind.TEST] = EventKind.TEST
    #: Index into the launcher's own log, which is the primary source for ``test_cycles``.
    launcher_seq: NonNegativeInt


class ToolOtherEvent(_EventBase):
    kind: Literal[EventKind.TOOL_OTHER] = EventKind.TOOL_OTHER


class CompletionEvent(_EventBase):
    kind: Literal[EventKind.COMPLETION] = EventKind.COMPLETION
    num_turns: NonNegativeInt
    output_tokens: NonNegativeInt
    api_duration_ms: NonNegativeInt
    #: Integer micro-USD. No floating point enters a normalized event.
    cost_micro_usd: NonNegativeInt
    stop_reason: StopReason
    terminal_reason: TerminalReason
    permission_denial_count: NonNegativeInt


class OpaqueEvent(_EventBase):
    """A provider event with no measurement signal. Carries no body."""

    kind: Literal[EventKind.OPAQUE] = EventKind.OPAQUE


AgentEvent = Annotated[
    SessionStartEvent
    | PlanEvent
    | SearchEvent
    | FileReadEvent
    | PatchEvent
    | CommandEvent
    | TestEvent
    | ToolOtherEvent
    | CompletionEvent
    | OpaqueEvent,
    Field(discriminator="kind"),
]


class NormalizationAudit(BaseModel):
    """What the normalizer could not account for.

    This is part of the evidence, not a log line. A run whose parse left tool calls
    unresolved, or which contained provider events the parser did not recognise, is not
    silently treated as a clean run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_provider_lines: NonNegativeInt
    unparsable_lines: NonNegativeInt = 0
    unknown_raw_kinds: NonNegativeInt = 0
    unpaired_tool_uses: NonNegativeInt = 0
    orphaned_tool_results: NonNegativeInt = 0
    duplicate_tool_ids: NonNegativeInt = 0
    direct_test_invocations: NonNegativeInt = 0
    unclassifiable_commands: NonNegativeInt = 0

    @property
    def is_clean(self) -> bool:
        return not any(
            (
                self.unparsable_lines,
                self.unknown_raw_kinds,
                self.unpaired_tool_uses,
                self.orphaned_tool_results,
                self.duplicate_tool_ids,
                self.direct_test_invocations,
                self.unclassifiable_commands,
            )
        )
