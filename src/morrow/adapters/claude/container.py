"""Run the agent inside a container.

The first real recordings exposed two problems that this fixes together.

The agent could not run tests. `--permission-mode acceptEdits` allows file edits but still
gates shell commands, so every `./morrow-test` invocation came back "This command requires
approval" — six times on one arm, fourteen on the other. Both agents implemented their
changes without ever running a test, which is not a slower measurement of the same thing.

The agent read outside its workspace. Looking for the reason its test command kept
failing, it went and read the host's Claude settings. Nothing stopped it, because a
working directory is not a boundary.

Running the agent unrestricted inside a container answers both. Commands are allowed,
because running commands is the behaviour under study; and "unrestricted" now means
unrestricted inside a container whose only mounts are the workspace, a per-run
configuration directory and a per-run virtualenv.

What this does not provide is network isolation. The agent has to reach the model API, so
`--network none` is not available and restricting egress to one host is separate work.
Filesystem isolation, process isolation, non-root execution and identical resource limits
on both arms are what a container buys here — the last of which is as much about
experimental control as about safety.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from morrow.adapters.claude.runner import RunLimits, RunOutcome, TerminalStatus

DEFAULT_IMAGE = "morrow-agent:0.1"

WORKSPACE_MOUNT = "/workspace"
AGENT_HOME_MOUNT = "/agent-home"
STATE_MOUNT = "/morrow-state"

#: The virtualenv's *parent* is mounted, not the virtualenv itself. `uv` recreates the
#: environment by removing and rebuilding the directory, and a bind-mounted directory
#: cannot be removed from inside the container — the first attempt died on
#: "failed to remove directory /venv: Permission denied".
VENVS_MOUNT = "/venvs"


@dataclass(frozen=True)
class ContainerLimits:
    """Identical on both arms of a pair, so resource pressure cannot masquerade as friction."""

    cpus: str = "4"
    memory: str = "8g"
    pids: int = 512


def build_docker_argv(
    *,
    container_name: str,
    workspace: Path,
    agent_home: Path,
    venvs_dir: Path,
    venv_name: str,
    state_dir: Path,
    prompt: str,
    model: str,
    limits: RunLimits,
    container_limits: ContainerLimits,
    image: str = DEFAULT_IMAGE,
    uid_gid: str,
    extra_env: Mapping[str, str] | None = None,
) -> list[str]:
    """The exact invocation, in one place so it can be hashed into the manifest."""
    argv: list[str] = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--user",
        uid_gid,
        "--cpus",
        container_limits.cpus,
        "--memory",
        container_limits.memory,
        "--pids-limit",
        str(container_limits.pids),
        # Nothing in this run needs to gain privileges.
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "-v",
        f"{workspace}:{WORKSPACE_MOUNT}",
        "-v",
        f"{agent_home}:{AGENT_HOME_MOUNT}",
        "-v",
        f"{venvs_dir}:{VENVS_MOUNT}",
        # The launcher's log lives on the evaluator side, so the directory holding it is
        # mounted rather than the workspace being trusted to carry it back.
        "-v",
        f"{state_dir}:{STATE_MOUNT}",
        "-e",
        "HOME=/tmp",
        "-e",
        f"CLAUDE_CONFIG_DIR={AGENT_HOME_MOUNT}",
        "-e",
        f"UV_PROJECT_ENVIRONMENT={VENVS_MOUNT}/{venv_name}",
        "-e",
        "CI=1",
    ]
    for key, value in (extra_env or {}).items():
        argv += ["-e", f"{key}={value}"]

    argv += [
        "-w",
        WORKSPACE_MOUNT,
        image,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        # Unrestricted inside a container that contains only the workspace. Running
        # commands is the behaviour being measured; gating it measures something else.
        "--permission-mode",
        "bypassPermissions",
        "--model",
        model,
        "--max-budget-usd",
        f"{limits.max_budget_usd}",
    ]
    return argv


async def _stop_container(name: str, grace: float) -> None:
    """Stop by name. Killing the local `docker run` client would leave the container up."""
    process = await asyncio.create_subprocess_exec(
        "docker",
        "stop",
        "--time",
        str(int(grace)),
        name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()


async def run_agent_in_container(
    *,
    container_name: str,
    workspace: Path,
    agent_home: Path,
    venvs_dir: Path,
    venv_name: str,
    state_dir: Path,
    prompt_path: Path,
    stream_path: Path,
    stderr_path: Path,
    model: str,
    limits: RunLimits,
    container_limits: ContainerLimits | None = None,
    image: str = DEFAULT_IMAGE,
    uid_gid: str,
    extra_env: Mapping[str, str] | None = None,
) -> RunOutcome:
    """Run one attempt in a container and leave its raw stream on the evaluator side."""
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    argv = build_docker_argv(
        container_name=container_name,
        workspace=workspace,
        agent_home=agent_home,
        venvs_dir=venvs_dir,
        venv_name=venv_name,
        state_dir=state_dir,
        prompt=prompt_path.read_text(encoding="utf-8"),
        model=model,
        limits=limits,
        container_limits=container_limits or ContainerLimits(),
        image=image,
        uid_gid=uid_gid,
        extra_env=extra_env,
    )

    loop = asyncio.get_running_loop()
    started = loop.time()
    status = TerminalStatus.COMPLETED

    with stream_path.open("wb") as out, stderr_path.open("wb") as err:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=out,
            stderr=err,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=limits.wall_time_seconds)
        except TimeoutError:
            status = TerminalStatus.TIMEOUT
            await _stop_container(container_name, limits.terminate_grace_seconds)
            await process.wait()

    exit_code = process.returncode
    if status is TerminalStatus.COMPLETED and exit_code not in (0, None):
        status = TerminalStatus.CRASHED

    return RunOutcome(
        terminal_status=status,
        exit_code=exit_code,
        stream_path=stream_path,
        wall_duration_ms=int((loop.time() - started) * 1000),
    )


def image_digest(image: str = DEFAULT_IMAGE) -> Sequence[str]:
    """The command whose output pins the image into the manifest."""
    return ("docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}{{.Id}}", image)
