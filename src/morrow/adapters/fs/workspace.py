"""Prepare the directory an agent run happens in.

The workspace is the only thing the agent may write to. Everything the evaluator needs to
judge the run — the frozen tests, the pre-run snapshot, the launcher's log — lives outside
it. That separation is for preventing mix-ups, not for security: the agent runs under the
same user and could reach outside if it tried. MORROW is therefore only ever pointed at a
repository its operator already trusts.

Three details here decide whether the measurement is honest.

*The launcher goes in before the snapshot.* If ``./morrow-test`` were written after the
pre-run walk, its bytes would show up as work the agent did, in every single run.

*The virtualenv lives outside the workspace.* An agent that pip-installs its way past a
failing test would otherwise satisfy acceptance while producing no churn at all. That is
not hypothetical — it was observed in the very first capture taken for this project.

*The variant name never appears in the path.* The agent is given an opaque run id, so it
cannot infer from its own working directory whether it is expected to do well.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

LAUNCHER_NAME = "morrow-test"

#: The launcher is intentionally tiny and fixed. It records one line per invocation and
#: forwards to the acceptance command, so `test_cycles` comes from a log the evaluator
#: owns rather than from guessing at shell strings.
LAUNCHER_TEMPLATE = """#!/bin/sh
# Placed by MORROW. Runs the frozen acceptance command and records the invocation.
#
# The log path is defaulted rather than required. An earlier version used `set -u` with a
# bare $MORROW_LAUNCHER_LOG; when the variable was not passed through to the agent's
# environment the launcher failed on every call, and a run that tested five times reported
# zero test cycles. A recording instrument must not fail closed on its own plumbing.
log="${{MORROW_LAUNCHER_LOG:-{fallback_log}}}"
seq=0
if [ -f "$log" ]; then
  seq=$(wc -l < "$log" | tr -d ' ')
fi
start=$(date +%s%3N 2>/dev/null || echo 0)
{command} "$@"
status=$?
end=$(date +%s%3N 2>/dev/null || echo 0)
printf '{{"launcher_seq":%s,"exit_code":%s,"duration_ms":%s}}\\n' \\
  "$seq" "$status" "$((end - start))" >> "$log" 2>/dev/null || true
exit $status
"""


@dataclass(frozen=True)
class PreparedWorkspace:
    run_id: str
    root: Path
    venv_path: Path
    launcher_log: Path
    launcher_digest: str


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_launcher(workspace: Path, command: str, fallback_log: Path) -> tuple[Path, str]:
    """Write the fixed test launcher and return its path and digest.

    The digest is checked again after the run. If it changed, the agent edited the one
    instrument the measurement depends on, and the run is invalidated rather than trusted.
    """
    launcher = workspace / LAUNCHER_NAME
    launcher.write_text(
        LAUNCHER_TEMPLATE.format(command=command, fallback_log=fallback_log),
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    return launcher, _digest(launcher)


def prepare(
    *,
    run_id: str,
    source_tree: Path,
    work_root: Path,
    state_root: Path,
    acceptance_command: str,
) -> PreparedWorkspace:
    """Copy the source tree into a fresh workspace and install the launcher.

    ``source_tree`` is a committed snapshot of the repository under test. Copying rather
    than checking out keeps the demo self-contained and makes the starting state identical
    on both arms, which is the whole point of a paired run.
    """
    workspace = work_root / run_id
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.parent.mkdir(parents=True, exist_ok=True)

    shutil.copytree(
        source_tree,
        workspace,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "*.pyc"
        ),
    )

    launcher_log = state_root / "launcher-log" / f"{run_id}.jsonl"
    launcher_log.parent.mkdir(parents=True, exist_ok=True)
    launcher_log.write_text("", encoding="utf-8")

    _, launcher_digest = write_launcher(workspace, acceptance_command, launcher_log)

    venv_path = state_root / "venvs" / run_id
    venv_path.parent.mkdir(parents=True, exist_ok=True)

    return PreparedWorkspace(
        run_id=run_id,
        root=workspace,
        venv_path=venv_path,
        launcher_log=launcher_log,
        launcher_digest=launcher_digest,
    )


def launcher_unchanged(workspace: PreparedWorkspace) -> bool:
    launcher = workspace.root / LAUNCHER_NAME
    return launcher.is_file() and _digest(launcher) == workspace.launcher_digest


def count_launcher_invocations(launcher_log: Path) -> int:
    """``test_cycles``, taken from the launcher's own record rather than from the stream."""
    if not launcher_log.is_file():
        return 0
    return sum(1 for line in launcher_log.read_text(encoding="utf-8").splitlines() if line.strip())
