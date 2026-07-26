"""Edit a published cassette's evidence, verify it, and put it back.

    uv run python scripts/tamper_demo.py

Shows the one property that makes a recorded verdict worth anything: the report has to
*follow from* the evidence beside it. The digest alone cannot establish that — a cassette
author who edits a number can update the digest in the same breath. Recomputation is what
catches it.

The cassette is restored before this exits, including if verification behaves unexpectedly.
A demo that leaves the repository dirty is a demo that will eventually be committed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASSETTE = ROOT / "cassettes" / "treatment-replace-cache"
TARGET = "r1.churn.json"


def _verify() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "morrow.cli.main", "verify", str(CASSETTE)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return (result.stdout + result.stderr).strip().splitlines()[0]


def main() -> None:
    churn_path = CASSETTE / TARGET
    manifest_path = CASSETTE / "manifest.json"
    original_churn = churn_path.read_bytes()
    original_manifest = manifest_path.read_bytes()

    try:
        print(f"$ morrow verify cassettes/{CASSETTE.name}")
        print(f"  {_verify()}\n")

        # Halve the candidate's churn — the direction someone would actually cheat in — and
        # update its digest so step 1 has nothing to complain about.
        import hashlib

        churn = json.loads(original_churn)
        was = churn["added_lines"]
        churn["added_lines"] = was // 2
        edited = (json.dumps(churn, sort_keys=True, indent=2) + "\n").encode("ascii")
        churn_path.write_bytes(edited)

        manifest = json.loads(original_manifest)
        manifest["digests"][TARGET] = hashlib.sha256(edited).hexdigest()
        manifest_path.write_bytes(
            (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("ascii")
        )

        print(f"# edited {TARGET}: added_lines {was} -> {was // 2}, digest updated to match")
        print(f"$ morrow verify cassettes/{CASSETTE.name}")
        print(f"  {_verify()}")
        print("\n# the digests all match. the report no longer follows from the evidence.")
    finally:
        churn_path.write_bytes(original_churn)
        manifest_path.write_bytes(original_manifest)


if __name__ == "__main__":
    main()
