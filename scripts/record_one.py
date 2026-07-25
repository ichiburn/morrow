"""Record one agent run against one demo snapshot, inside a container.

    uv run python scripts/record_one.py main r0
    uv run python scripts/record_one.py coupling r1

The container is what makes the run measurable rather than merely observed: the agent is
unrestricted inside it, so shell commands actually execute, and the only things mounted
are the workspace, a per-run config directory, a per-run virtualenv and the directory the
launcher writes its log to. Resource limits are identical on both arms.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from morrow.adapters.claude.container import (
    DEFAULT_IMAGE,
    ContainerLimits,
    run_agent_in_container,
)
from morrow.adapters.claude.runner import AgentHomeBuilder, RunLimits
from morrow.adapters.claude.stream import ClaudeStreamNormalizer
from morrow.adapters.fs.snapshot import compute_churn, take_snapshot
from morrow.adapters.fs.workspace import count_launcher_invocations, launcher_unchanged, prepare
from morrow.domain.events import EventKind

ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / ".morrow" / "state"
WORK_ROOT = ROOT / ".morrow" / "work"
PROMPT = ROOT / "future-packs" / "replace-cache.prompt.md"
LAUNCHER_LOG_DIR = STATE_ROOT / "launcher-log"
MODEL = "claude-sonnet-5"

# Inside the container the launcher writes here; the directory is bind-mounted.
CONTAINER_LAUNCHER_LOG = "/morrow-state/{run_id}.jsonl"
# `uv run` without --no-sync so the environment is built inside the container, where the
# interpreter paths are valid. Building it on the host would bake host paths into the
# scripts and every invocation would fail.
LAUNCHER_COMMAND = "uv run pytest -q -p no:cacheprovider"


def _uid_gid() -> str:
    return f"{os.getuid()}:{os.getgid()}"


def _prewarm_environment(workspace: Path, venvs_dir: Path, venv_name: str) -> None:
    """Build the virtualenv in the container before the agent starts.

    Doing it up front keeps dependency installation out of the measured window, so the
    two arms are compared on the work the task required rather than on who happened to
    pay for the first `uv sync`.
    """
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "--user", _uid_gid(),
            "-v", f"{workspace}:/workspace",
            "-v", f"{venvs_dir}:/venvs",
            "-e", "HOME=/tmp",
            "-e", f"UV_PROJECT_ENVIRONMENT=/venvs/{venv_name}",
            "-w", "/workspace",
            "--entrypoint", "uv",
            DEFAULT_IMAGE,
            "sync", "--all-groups",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Swallowing this once already cost a recording pass. Setup failures have to be
        # legible, because a run that never had a working environment is not a run.
        raise SystemExit(
            f"environment prewarm failed (exit {result.returncode})\n"
            f"--- stdout ---\n{result.stdout[-2000:]}\n"
            f"--- stderr ---\n{result.stderr[-2000:]}"
        )


async def record(variant_dir: str, run_id: str = "r0") -> None:
    source_tree = ROOT / "demo" / "snapshots" / variant_dir
    if not source_tree.is_dir():
        raise SystemExit(f"no such snapshot: {source_tree}")

    launcher_log = LAUNCHER_LOG_DIR / f"{run_id}.jsonl"
    workspace = prepare(
        run_id=run_id,
        source_tree=source_tree,
        work_root=WORK_ROOT,
        state_root=STATE_ROOT,
        acceptance_command=LAUNCHER_COMMAND,
    )
    print(f"workspace: {workspace.root}")

    venvs_dir = STATE_ROOT / "venvs"
    venvs_dir.mkdir(parents=True, exist_ok=True)
    if workspace.venv_path.exists():
        shutil.rmtree(workspace.venv_path)
    _prewarm_environment(workspace.root, venvs_dir, run_id)
    print(f"venv:      {workspace.venv_path} (built in-container)")

    snapshot_root = STATE_ROOT / "snapshots" / f"{run_id}.pre"
    if snapshot_root.exists():
        shutil.rmtree(snapshot_root)
    pre = take_snapshot(workspace.root, content_root=snapshot_root)
    print(f"pre files: {len(pre.records)}")

    agent_home = AgentHomeBuilder(Path.home() / ".claude").build(STATE_ROOT / "agent-home" / run_id)

    outcome = await run_agent_in_container(
        container_name=f"morrow-{run_id}",
        workspace=workspace.root,
        agent_home=agent_home,
        venvs_dir=venvs_dir,
        venv_name=run_id,
        state_dir=LAUNCHER_LOG_DIR,
        prompt_path=PROMPT,
        stream_path=STATE_ROOT / "streams" / f"{run_id}.jsonl",
        stderr_path=STATE_ROOT / "streams" / f"{run_id}.stderr",
        model=MODEL,
        limits=RunLimits(wall_time_seconds=1200, max_budget_usd=3.00),
        container_limits=ContainerLimits(cpus="4", memory="8g"),
        uid_gid=_uid_gid(),
        extra_env={"MORROW_LAUNCHER_LOG": CONTAINER_LAUNCHER_LOG.format(run_id=run_id)},
    )
    print(f"terminal:  {outcome.terminal_status.value} exit={outcome.exit_code}")
    print(f"wall:      {outcome.wall_duration_ms} ms")

    post = take_snapshot(workspace.root)
    churn = compute_churn(pre, post, workspace.root)

    normalizer = ClaudeStreamNormalizer(run_id=run_id, workspace=workspace.root)
    lines = outcome.stream_path.read_text(encoding="utf-8").splitlines()
    events, audit = normalizer.normalize(iter(lines))

    files_read = len({e.path_ref for e in events if e.kind is EventKind.FILE_READ})
    test_cycles = count_launcher_invocations(launcher_log)
    denials = next(
        (e.permission_denial_count for e in events if e.kind is EventKind.COMPLETION), 0
    )

    print("--- measurement ---")
    print(f"launcher intact:      {launcher_unchanged(workspace)}")
    print(f"permission denials:   {denials}")
    print(f"files_read_distinct:  {files_read}")
    print(f"test_cycles:          {test_cycles}")
    print(
        f"final_churn:          {churn.total_lines} "
        f"(+{churn.added_lines} -{churn.deleted_lines})"
    )
    print(f"files +{churn.files_added} ~{churn.files_modified} -{churn.files_deleted}")
    print(f"events:               {len(events)}")
    print(f"audit:                {json.dumps(audit.model_dump())}")


if __name__ == "__main__":
    asyncio.run(
        record(
            sys.argv[1] if len(sys.argv) > 1 else "main",
            sys.argv[2] if len(sys.argv) > 2 else "r0",
        )
    )
