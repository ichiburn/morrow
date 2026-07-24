"""Classify a shell command inside the trust boundary.

Only the *result* of this classification is published — an enum and nothing else. The raw
command string never leaves this module, because an agent's shell command can carry
anything: a heredoc, a ``python -c`` body, a token in an argument.

The classification is deliberately narrow. Parsing arbitrary shell correctly is not
something this can do, so anything it cannot decompose is reported as
``UNCLASSIFIABLE`` rather than being quietly filed under "other". ``test_cycles`` is a
gating component; letting an unparsed command silently drop out of it would make the
candidate look cheaper than it was.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence

from morrow.domain.events import CommandPurpose, KnownExecutable

#: Prefixes that wrap another command without changing what it is.
_WRAPPERS: frozenset[str] = frozenset({"env", "timeout", "nice", "ionice", "stdbuf"})

#: The launcher MORROW places in the worktree. Only these count as a test cycle.
_LAUNCHER_NAMES: frozenset[str] = frozenset({"morrow-test", "./morrow-test"})

#: Token prefixes that mean "a test runner was invoked directly".
_DIRECT_TEST_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("python3", "-m", "pytest"),
    ("uv", "run", "pytest"),
)

#: Shell constructs this classifier does not attempt to interpret.
_OPAQUE_SHELL_TOKENS: frozenset[str] = frozenset({"-c", "-lc", "-lic"})
_SHELLS: frozenset[str] = frozenset({"sh", "bash", "zsh", "dash"})

_EXECUTABLE_BY_NAME: dict[str, KnownExecutable] = {
    "pytest": KnownExecutable.PYTEST,
    "python": KnownExecutable.PYTHON,
    "python3": KnownExecutable.PYTHON,
    "git": KnownExecutable.GIT,
    "uv": KnownExecutable.UV,
    "ruff": KnownExecutable.RUFF,
    "mypy": KnownExecutable.MYPY,
    "pip": KnownExecutable.PIP,
}


class Classification:
    """The publishable result of looking at a command."""

    __slots__ = ("executable", "purpose")

    def __init__(self, executable: KnownExecutable, purpose: CommandPurpose) -> None:
        self.executable = executable
        self.purpose = purpose

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Classification)
            and other.executable is self.executable
            and other.purpose is self.purpose
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Classification({self.executable.value}, {self.purpose.value})"


_UNCLASSIFIABLE = Classification(KnownExecutable.OTHER, CommandPurpose.UNCLASSIFIABLE)


def _split_segments(command: str) -> list[list[str]] | None:
    """Split on ``&&``, ``||``, ``;`` and ``|``, returning argv per segment.

    Returns ``None`` when the command cannot be tokenised at all.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in {"&&", "||", ";", "|", "&"}:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def _strip_wrappers(argv: Sequence[str]) -> list[str]:
    """Drop leading wrappers and their options: ``timeout 60 pytest`` -> ``pytest``."""
    remaining = list(argv)
    while remaining:
        head = remaining[0].rsplit("/", 1)[-1]
        if head not in _WRAPPERS:
            break
        remaining = remaining[1:]
        # Consume the wrapper's own arguments: options, and for `timeout`/`nice`
        # the bare duration or priority that follows.
        while remaining and (remaining[0].startswith("-") or _looks_like_wrapper_arg(remaining[0])):
            remaining = remaining[1:]
    return remaining


def _looks_like_wrapper_arg(token: str) -> bool:
    """A duration (``60``, ``5m``) or a ``KEY=value`` assignment passed to ``env``."""
    if "=" in token and not token.startswith("="):
        return True
    stripped = token.rstrip("smhd")
    return bool(stripped) and stripped.isdigit()


def _is_opaque_shell(argv: Sequence[str]) -> bool:
    """``bash -lc '...'`` hides a whole command line this classifier will not parse."""
    if not argv:
        return False
    head = argv[0].rsplit("/", 1)[-1]
    return head in _SHELLS and any(token in _OPAQUE_SHELL_TOKENS for token in argv[1:])


def _matches_prefix(argv: Sequence[str], prefix: Sequence[str]) -> bool:
    return len(argv) >= len(prefix) and all(argv[i] == prefix[i] for i in range(len(prefix)))


def _classify_segment(argv: Sequence[str]) -> Classification | None:
    """Classify one segment. ``None`` means "not a test, keep looking"."""
    if not argv:
        return None
    if _is_opaque_shell(argv):
        return _UNCLASSIFIABLE

    stripped = _strip_wrappers(argv)
    if not stripped:
        return None
    if _is_opaque_shell(stripped):
        return _UNCLASSIFIABLE

    head = stripped[0]
    basename = head.rsplit("/", 1)[-1]

    if head in _LAUNCHER_NAMES or basename == "morrow-test":
        return Classification(KnownExecutable.MORROW_TEST, CommandPurpose.TEST_LAUNCHER)

    if any(_matches_prefix(stripped, prefix) for prefix in _DIRECT_TEST_PREFIXES):
        return Classification(
            _EXECUTABLE_BY_NAME.get(basename, KnownExecutable.OTHER),
            CommandPurpose.DIRECT_TEST,
        )

    return Classification(
        _EXECUTABLE_BY_NAME.get(basename, KnownExecutable.OTHER),
        CommandPurpose.OTHER,
    )


#: Ranked most to least significant. A compound command is described by its most
#: significant segment, so ``cd x && ./morrow-test`` is a test cycle.
_PRECEDENCE: tuple[CommandPurpose, ...] = (
    CommandPurpose.UNCLASSIFIABLE,
    CommandPurpose.TEST_LAUNCHER,
    CommandPurpose.DIRECT_TEST,
    CommandPurpose.OTHER,
)


def classify(command: str) -> Classification:
    """Reduce a raw shell command to a publishable classification.

    ``UNCLASSIFIABLE`` wins over everything else: if any part of the command was opaque,
    the whole command is treated as opaque rather than being described by the part that
    happened to be readable.
    """
    segments = _split_segments(command)
    if segments is None:
        return _UNCLASSIFIABLE

    results = [c for c in (_classify_segment(argv) for argv in segments) if c is not None]
    if not results:
        return Classification(KnownExecutable.OTHER, CommandPurpose.OTHER)

    for purpose in _PRECEDENCE:
        for result in results:
            if result.purpose is purpose:
                return result
    return results[0]  # pragma: no cover - _PRECEDENCE covers every member
