"""Edit a cassette's evidence, verify it, and show that the report no longer follows.

    uv run python scripts/tamper_demo.py

Shows the one property that makes a recorded verdict worth anything: the report has to
*follow from* the evidence beside it. A digest cannot establish that on its own — whoever
edits a number can update the digest in the same breath. Recomputation is what catches it.

The published cassette is **copied to a temporary directory and tampered with there.** An
earlier version edited it in place and restored it in a `finally`, which is fine until the
process is killed rather than raised in — and this runs inside a `vhs` recording, which
tears down its session on a timer. A tampered cassette left behind would then be committed,
and CI would not notice: it asserts exit codes, and a stale report exits 2 exactly like the
invalidated experiment does. Not touching the original removes the whole class of problem.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASSETTE = ROOT / "cassettes" / "treatment-replace-cache"
TARGET = "r1.churn.json"


def _verify(path: Path) -> tuple[str, int]:
    """Run the real CLI; return its verdict line and its exit code."""
    result = subprocess.run(
        [sys.executable, "-m", "morrow.cli.main", "verify", str(path)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    lines = (result.stdout + result.stderr).strip().splitlines()
    return (lines[0] if lines else "(no output)"), result.returncode


def _state(verdict_line: str) -> str:
    """The state field of ``<label> · <STATE> · exit <n>``, or "" if it is not one."""
    fields = verdict_line.split(" · ")
    return fields[1] if len(fields) >= 2 else ""


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="morrow-tamper-") as scratch:
        working = Path(scratch) / CASSETTE.name
        # symlinks=True: cassettes arrive from pull requests, and copytree's default is to
        # follow links. A symlink pointing outside the cassette — or at something huge —
        # would be dereferenced here, before the verifier's own symlink and size limits
        # ever see it. Copying links as links keeps that check where it belongs.
        shutil.copytree(CASSETTE, working, symlinks=True)

        print(
            f"# working on a copy of cassettes/{CASSETTE.name}"
            " — the published one is untouched\n"
        )
        print("$ morrow verify <copy>")
        before, before_code = _verify(working)
        print(f"  {before}\n")

        # The point of the demo is the *change* in verdict, so the starting verdict has to
        # be the known one. Without this, a cassette that was already stale would produce
        # the same two lines and the same passing assertion while demonstrating nothing.
        if _state(before) != "INVALID_EXPERIMENT" or before_code != 2:
            raise SystemExit(
                f"the cassette did not start where the demo expects: {before} "
                f"(exit {before_code})"
            )

        churn_path = working / TARGET
        manifest_path = working / "manifest.json"

        # Halve the candidate's churn — the direction someone would actually cheat in.
        churn = json.loads(churn_path.read_bytes())
        was = churn["added_lines"]
        churn["added_lines"] = was // 2
        edited = (json.dumps(churn, sort_keys=True, indent=2) + "\n").encode("ascii")
        churn_path.write_bytes(edited)

        # ...and update the digest, so the integrity check has nothing to complain about.
        manifest = json.loads(manifest_path.read_bytes())
        manifest["digests"][TARGET] = hashlib.sha256(edited).hexdigest()
        manifest_path.write_bytes(
            (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("ascii")
        )

        print(f"# edited {TARGET}: added_lines {was} -> {was // 2}, digest updated to match")
        print("$ morrow verify <copy>")
        after, after_code = _verify(working)
        print(f"  {after}")

        # The narration says recomputation catches this. Assert it rather than asserting
        # it in prose: a demo that prints its own conclusion regardless of the result is
        # exactly the kind of unearned claim this project is about.
        if _state(after) != "EVIDENCE_STALE" or after_code != 2:
            raise SystemExit(
                f"tamper demo did not demonstrate anything: before={before_code} "
                f"after={after_code} ({after})"
            )
        print("\n# every digest matches. the report no longer follows from the evidence.")


if __name__ == "__main__":
    main()
