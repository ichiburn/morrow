"""Run Claude Code headlessly, isolated from the host session.

This exists because of a measured failure. Invoking ``claude -p`` from inside another
Claude Code session inherits the parent's hooks and permission settings, and the child
could not run a single shell command: every ``Bash`` call came back denied and the run
ended with "test execution is blocked". A measurement of an agent that cannot run tests is
not a measurement.

Pointing ``CLAUDE_CONFIG_DIR`` at a purpose-built directory — no hooks, no memory, only the
credentials — fixed it: zero permission denials, no hook events in the stream, and the
agent went on to hit a missing dependency, install it, and retry. That retry is exactly the
signal MORROW is trying to observe, so the isolation is not hygiene; it is the measurement
apparatus.

Two limits are enforced here: wall-clock time and budget. Turn count is deliberately not
among them. Claude Code has no ``--max-turns`` flag, and ``num_turns`` only appears in the
terminal ``result`` event, so it cannot be used to stop a run in progress. Claiming a turn
cap that cannot be applied would be worse than not having one.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class TerminalStatus(StrEnum):
    """How the run ended, decided by the evaluator rather than by the provider.

    Evidence validation needs this: a run killed by the wall clock never emits the
    provider's ``result`` event, so requiring one unconditionally would classify every
    timeout as incomplete evidence instead of as the failed attempt it actually is.
    """

    COMPLETED = "completed"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    CRASHED = "crashed"


@dataclass(frozen=True)
class RunLimits:
    wall_time_seconds: int = 900
    max_budget_usd: float = 2.50
    #: Grace period between SIGTERM and SIGKILL for the process group.
    terminate_grace_seconds: float = 5.0


@dataclass(frozen=True)
class RunOutcome:
    terminal_status: TerminalStatus
    exit_code: int | None
    stream_path: Path
    wall_duration_ms: int


class AgentHomeBuilder:
    """Builds the per-run configuration directory the agent will use.

    One per run, never shared. A shared directory would leak session state, caches and
    settings from whichever run went first into the ones that followed, which breaks the
    only thing a paired comparison relies on: that both sides ran under the same
    conditions.
    """

    #: Copied from the host config so the agent can authenticate. Nothing else is.
    CREDENTIAL_FILES: tuple[str, ...] = (".credentials.json", ".config.json")

    #: No hooks, no memory, no plugins. The instructions the agent sees must be identical
    #: across runs, and anything the host happens to have configured is not identical.
    SETTINGS = '{\n  "hooks": {},\n  "includeCoAuthoredBy": false\n}\n'

    def __init__(self, host_config_dir: Path) -> None:
        self._host_config_dir = host_config_dir

    def build(self, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        for name in self.CREDENTIAL_FILES:
            source = self._host_config_dir / name
            if source.is_file():
                shutil.copyfile(source, destination / name)
                (destination / name).chmod(0o600)
        (destination / "settings.json").write_text(self.SETTINGS, encoding="utf-8")
        return destination


def build_argv(
    prompt_path: Path,
    *,
    model: str,
    limits: RunLimits,
    executable: str = "claude",
) -> list[str]:
    """The exact invocation. Kept in one place so it can be hashed into the manifest."""
    return [
        executable,
        "-p",
        prompt_path.read_text(encoding="utf-8"),
        "--output-format",
        "stream-json",
        "--verbose",
        # `acceptEdits` is not enough, and finding that out cost a full pair of
        # recordings. It permits file edits but still gates shell commands, so every
        # `./morrow-test` invocation came back "This command requires approval" — six
        # times on one arm, fourteen on the other. The agents implemented their changes
        # without ever being able to run a test. That is not a slower measurement of the
        # same thing; it is a measurement of a different thing.
        #
        # Running commands *is* the behaviour under study, so the run needs a mode that
        # allows them. This is consistent with the threat model rather than a hole in it:
        # the workspace was never a security boundary (documented in operations.md §7.2),
        # and MORROW is only ever pointed at a repository its operator already trusts.
        "--permission-mode",
        "bypassPermissions",
        "--model",
        model,
        "--max-budget-usd",
        f"{limits.max_budget_usd}",
    ]


def _child_environment(agent_home: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """A minimal environment, with the host session's markers removed.

    ``CLAUDECODE`` and friends tell a nested invocation it is running inside another
    session, which changes its behaviour. The run must not be able to tell.

    ``extra`` carries what the workspace itself needs — notably the launcher's log path.
    Leaving that out once cost a whole recording: the launcher ran five times, failed on
    an unset variable every time, and ``test_cycles`` came back as zero for a run that had
    clearly been testing.
    """
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TZ")
    env = {name: os.environ[name] for name in keep if name in os.environ}
    env["CLAUDE_CONFIG_DIR"] = str(agent_home)
    env["CI"] = "1"
    if extra:
        env.update(extra)
    return env


async def _terminate_group(process: asyncio.subprocess.Process, grace: float) -> None:
    """Signal the whole process group, not just the child.

    The agent spawns shells, which spawn test runners, which spawn more processes. Killing
    only the direct child leaves those running and holding the workspace.
    """
    if process.returncode is not None:
        return
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:  # pragma: no cover - race with natural exit
        return

    with suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=grace)
        return
    except TimeoutError:
        pass
    with suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)
    await process.wait()


async def run_agent(
    *,
    workspace: Path,
    agent_home: Path,
    prompt_path: Path,
    stream_path: Path,
    stderr_path: Path,
    model: str,
    limits: RunLimits,
    executable: str = "claude",
    extra_env: Mapping[str, str] | None = None,
) -> RunOutcome:
    """Run one agent attempt and leave its raw stream on disk.

    The stream file is provider output: it contains paths, command bodies and patch text,
    so it stays on the evaluator side and is never published. Only the normalized events
    derived from it go into a cassette.
    """
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    argv = build_argv(prompt_path, model=model, limits=limits, executable=executable)
    loop = asyncio.get_running_loop()
    started = loop.time()

    with stream_path.open("wb") as out, stderr_path.open("wb") as err:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workspace),
            env=_child_environment(agent_home, extra_env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            # New session, so the whole tree can be signalled as one group.
            start_new_session=True,
        )

        status = TerminalStatus.COMPLETED
        try:
            await asyncio.wait_for(process.wait(), timeout=limits.wall_time_seconds)
        except TimeoutError:
            status = TerminalStatus.TIMEOUT
            await _terminate_group(process, limits.terminate_grace_seconds)

    elapsed_ms = int((loop.time() - started) * 1000)
    exit_code = process.returncode

    if status is TerminalStatus.COMPLETED and exit_code not in (0, None):
        # A non-zero exit without a timeout means the CLI itself failed. Whether that was
        # the budget cap or something else is decided from the stream by the caller.
        status = TerminalStatus.CRASHED

    return RunOutcome(
        terminal_status=status,
        exit_code=exit_code,
        stream_path=stream_path,
        wall_duration_ms=elapsed_ms,
    )
