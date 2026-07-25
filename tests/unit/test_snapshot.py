"""Tests for the churn measurement.

Several of these exist because a review found the earlier design would have produced a
biased number. They are regression tests for specific mistakes, not coverage filler.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from morrow.adapters.fs.snapshot import (
    DEFAULT_EXCLUDES,
    SnapshotRejectedError,
    compute_churn,
    take_snapshot,
)


def _workspace(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def _snapshots(tmp_path: Path, workspace: Path):
    content_root = tmp_path / "pre"
    pre = take_snapshot(workspace, content_root=content_root)
    return pre


def test_creating_one_new_file_is_counted(tmp_path: Path) -> None:
    """The regression this whole module was rewritten for.

    On the port-boundary side, the expected solution to the future task is "add one new
    adapter". An earlier design measured churn with `git diff`, which does not see
    untracked files — so the baseline's work vanished and only the coupled candidate's
    edits were counted. That biases the experiment toward the conclusion it was supposed
    to be testing.
    """
    workspace = _workspace(tmp_path, {"orders/service.py": "x = 1\n"})
    pre = _snapshots(tmp_path, workspace)

    (workspace / "orders" / "memory_cache.py").write_text("a\nb\nc\n", encoding="utf-8")
    post = take_snapshot(workspace)

    churn = compute_churn(pre, post, workspace)
    assert churn.files_added == 1
    assert churn.added_lines == 3
    assert churn.total_lines == 3


def test_agent_git_activity_does_not_hide_changes(tmp_path: Path) -> None:
    """Churn is computed from the filesystem, so git state is irrelevant.

    An agent that stages, commits, or adds its work to `.gitignore` changes nothing here.
    """
    workspace = _workspace(tmp_path, {"a.py": "one\n"})
    pre = _snapshots(tmp_path, workspace)

    # Simulate the agent doing git things and trying to hide the new file.
    (workspace / ".git").mkdir()
    (workspace / ".git" / "index").write_text("binary-ish", encoding="utf-8")
    (workspace / ".gitignore").write_text("secret_work.py\n", encoding="utf-8")
    (workspace / "secret_work.py").write_text("hidden\nwork\n", encoding="utf-8")

    post = take_snapshot(workspace)
    churn = compute_churn(pre, post, workspace)

    # .git is excluded, but the "hidden" file is still measured.
    assert churn.files_added == 2  # .gitignore and secret_work.py
    assert churn.added_lines == 3


def test_acceptance_run_artifacts_are_excluded(tmp_path: Path) -> None:
    """Caches produced by the acceptance run must not count as the agent's work."""
    workspace = _workspace(tmp_path, {"a.py": "one\n"})
    pre = _snapshots(tmp_path, workspace)

    (workspace / ".pytest_cache" / "v" / "cache").mkdir(parents=True)
    (workspace / ".pytest_cache" / "v" / "cache" / "lastfailed").write_text("{}", encoding="utf-8")
    (workspace / "__pycache__").mkdir()
    (workspace / "__pycache__" / "a.cpython-312.pyc").write_text("junk", encoding="utf-8")

    post = take_snapshot(workspace)
    churn = compute_churn(pre, post, workspace)

    assert churn.files_added == 0
    assert churn.total_lines == 0


def test_modified_file_counts_only_the_changed_lines(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {"a.py": "one\ntwo\nthree\n"})
    pre = _snapshots(tmp_path, workspace)

    (workspace / "a.py").write_text("one\nTWO\nthree\nfour\n", encoding="utf-8")
    post = take_snapshot(workspace)

    churn = compute_churn(pre, post, workspace)
    assert churn.files_modified == 1
    assert churn.added_lines == 2  # "TWO" and "four"
    assert churn.deleted_lines == 1  # "two"


def test_deleted_file_counts_its_lines(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {"a.py": "one\ntwo\n", "b.py": "keep\n"})
    pre = _snapshots(tmp_path, workspace)

    (workspace / "a.py").unlink()
    post = take_snapshot(workspace)

    churn = compute_churn(pre, post, workspace)
    assert churn.files_deleted == 1
    assert churn.deleted_lines == 2


def test_unchanged_tree_produces_zero_churn(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, {"a.py": "one\n", "pkg/b.py": "two\n"})
    pre = _snapshots(tmp_path, workspace)
    post = take_snapshot(workspace)

    churn = compute_churn(pre, post, workspace)
    assert churn.total_lines == 0
    assert (churn.files_added, churn.files_deleted, churn.files_modified) == (0, 0, 0)


def test_binary_change_is_reported_separately_from_lines(tmp_path: Path) -> None:
    """Binary bytes and text lines are different units and are not summed.

    An earlier design converted bytes to notional lines, which let a single image or
    lockfile swing the gating metric for no defensible reason.
    """
    workspace = _workspace(tmp_path, {"a.py": "one\n"})
    (workspace / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
    pre = _snapshots(tmp_path, workspace)

    (workspace / "blob.bin").write_bytes(b"\x00\x01\x02\xff\xfe\xfd")
    post = take_snapshot(workspace)

    churn = compute_churn(pre, post, workspace)
    assert churn.binary_files_changed == 1
    assert churn.binary_bytes_changed == 2
    assert churn.total_lines == 0  # binary contributes nothing to the gating component


def test_symlink_makes_the_run_invalid(tmp_path: Path) -> None:
    """Symlinks are refused rather than followed.

    Following one would let the walk read outside the workspace; recording only its
    length would miss a retarget to a different path of the same length. Neither is worth
    the ambiguity, so the run is invalidated instead.
    """
    workspace = _workspace(tmp_path, {"a.py": "one\n"})
    (workspace / "link.py").symlink_to(workspace / "a.py")

    with pytest.raises(SnapshotRejectedError, match="symlink"):
        take_snapshot(workspace)


def test_walk_order_is_deterministic(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        {"z.py": "1\n", "a.py": "1\n", "pkg/m.py": "1\n", "pkg/b.py": "1\n"},
    )
    first = list(take_snapshot(workspace).records)
    second = list(take_snapshot(workspace).records)
    assert first == second == sorted(first)


def test_exclude_patterns_match_directories_and_globs() -> None:
    assert ".git/" in DEFAULT_EXCLUDES
    assert "*.pyc" in DEFAULT_EXCLUDES
