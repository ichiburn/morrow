"""Filesystem snapshots and churn, computed without touching git.

``final_churn`` is the one gating component that does not depend on how the agent chose to
use its tools, so it has to be right. Two earlier designs got it wrong in ways that would
have quietly biased the result:

*Using git.* ``git add -N`` plus ``git diff`` loses every change the agent staged,
committed, or hid behind an edited ``.gitignore``, and an earlier version also missed
untracked files entirely — which would have erased the main branch's expected solution
(creating one new file) while still counting the candidate's edits to tracked ones.

*Storing only hashes.* A pre-run manifest of ``(size, sha256)`` cannot produce a line diff.
Deleted content is gone, and modified content has nothing to diff against. So the pre-run
snapshot keeps the actual bytes.

Both passes use the same non-dereferencing walker. A snapshot taken with a copy that
follows symlinks, compared against a walk that does not, compares two different trees.
"""

from __future__ import annotations

import difflib
import hashlib
import shutil
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path


class SnapshotRejectedError(RuntimeError):
    """The tree contains something this measurement will not reason about.

    Raised for symlinks, FIFOs, sockets, devices, and for trees past the configured size
    caps. The caller turns this into ``INVALID_RUN`` and invalidates the pair rather than
    guessing a churn value.
    """


@dataclass(frozen=True)
class SnapshotLimits:
    """Caps that turn a pathological tree into an invalid run rather than a hang."""

    max_files: int = 20_000
    max_total_bytes: int = 256 * 1024 * 1024
    max_file_bytes: int = 8 * 1024 * 1024


DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git/",
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    "*.pyc",
)


@dataclass(frozen=True)
class FileRecord:
    """One regular file, as seen by the walker."""

    relative_path: str
    size_bytes: int
    sha256: str
    line_count: int
    is_binary: bool


@dataclass
class Snapshot:
    """A walk of the workspace, plus where the pre-run bytes were kept."""

    records: dict[str, FileRecord] = field(default_factory=dict)
    content_root: Path | None = None

    def content_of(self, relative_path: str) -> Path | None:
        if self.content_root is None:
            return None
        candidate = self.content_root / relative_path
        return candidate if candidate.is_file() else None


@dataclass(frozen=True)
class Churn:
    """The result the friction engine consumes."""

    added_lines: int
    deleted_lines: int
    files_added: int
    files_deleted: int
    files_modified: int
    binary_bytes_changed: int
    binary_files_changed: int

    @property
    def total_lines(self) -> int:
        """``final_churn``: text lines only. Binary is reported separately."""
        return self.added_lines + self.deleted_lines


def _is_excluded(relative_path: str, excludes: tuple[str, ...]) -> bool:
    for pattern in excludes:
        if pattern.endswith("/"):
            prefix = pattern.rstrip("/")
            parts = relative_path.split("/")
            if prefix in parts[:-1] or relative_path.startswith(f"{prefix}/"):
                return True
        elif fnmatch(relative_path, pattern) or fnmatch(Path(relative_path).name, pattern):
            return True
    return False


def _walk(root: Path, excludes: tuple[str, ...]) -> Iterator[Path]:
    """Yield regular files in a deterministic order, never following symlinks.

    Directory entries are sorted by name so the traversal order is fixed, which keeps the
    resulting record ordering — and therefore any hash over it — reproducible.
    """
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.encode("utf-8"))
        except PermissionError as exc:  # pragma: no cover - environment dependent
            raise SnapshotRejectedError(f"cannot read directory: {current}") from exc

        for entry in entries:
            relative = entry.relative_to(root).as_posix()
            if _is_excluded(relative, excludes):
                continue
            mode = entry.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise SnapshotRejectedError(f"symlink in workspace: {relative}")
            if stat.S_ISDIR(mode):
                stack.append(entry)
                continue
            if not stat.S_ISREG(mode):
                raise SnapshotRejectedError(f"non-regular file in workspace: {relative}")
            yield entry


def _read_record(path: Path, root: Path, limits: SnapshotLimits) -> FileRecord:
    relative = path.relative_to(root).as_posix()
    data = path.read_bytes()
    if len(data) > limits.max_file_bytes:
        raise SnapshotRejectedError(f"file exceeds size cap: {relative} ({len(data)} bytes)")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return FileRecord(
            relative_path=relative,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            line_count=0,
            is_binary=True,
        )

    return FileRecord(
        relative_path=relative,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        line_count=len(text.splitlines()),
        is_binary=False,
    )


def take_snapshot(
    workspace: Path,
    *,
    content_root: Path | None = None,
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES,
    limits: SnapshotLimits | None = None,
) -> Snapshot:
    """Walk ``workspace``; when ``content_root`` is given, also keep the bytes.

    The pre-run call passes ``content_root`` so a real diff is possible afterwards. The
    post-run call does not: by then the pre-run bytes are what the comparison needs.
    """
    caps = limits if limits is not None else SnapshotLimits()
    snapshot = Snapshot(content_root=content_root)
    total_bytes = 0

    for path in _walk(workspace, excludes):
        record = _read_record(path, workspace, caps)
        total_bytes += record.size_bytes
        if len(snapshot.records) >= caps.max_files:
            raise SnapshotRejectedError(f"workspace exceeds file cap: {caps.max_files}")
        if total_bytes > caps.max_total_bytes:
            raise SnapshotRejectedError(f"workspace exceeds byte cap: {caps.max_total_bytes}")

        snapshot.records[record.relative_path] = record
        if content_root is not None:
            destination = content_root / record.relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination, follow_symlinks=False)

    # The traversal itself is depth-first and therefore not globally ordered. Sorting the
    # records by relative path makes any hash over the snapshot independent of directory
    # layout, which is what "the same tree yields the same bytes" has to mean.
    snapshot.records = {
        key: snapshot.records[key] for key in sorted(snapshot.records, key=str.encode)
    }
    return snapshot


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):  # pragma: no cover - guarded by is_binary
        return []


def _diff_counts(before: list[str], after: list[str]) -> tuple[int, int]:
    added = deleted = 0
    for line in difflib.unified_diff(before, after, n=0, lineterm=""):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            deleted += 1
    return added, deleted


def compute_churn(pre: Snapshot, post: Snapshot, workspace: Path) -> Churn:
    """Compare two snapshots into the churn the friction engine consumes.

    A file the agent created counts its whole length as added, which is the point: the
    expected solution on the port-boundary side is "write one new adapter", and a churn
    definition that missed it would make the coupled side look artificially expensive.
    """
    added_lines = deleted_lines = 0
    files_added = files_deleted = files_modified = 0
    binary_bytes = 0
    binary_files = 0

    pre_paths = set(pre.records)
    post_paths = set(post.records)

    for relative in sorted(post_paths - pre_paths):
        record = post.records[relative]
        if record.is_binary:
            binary_files += 1
            binary_bytes += record.size_bytes
            continue
        files_added += 1
        added_lines += record.line_count

    for relative in sorted(pre_paths - post_paths):
        record = pre.records[relative]
        if record.is_binary:
            binary_files += 1
            binary_bytes += record.size_bytes
            continue
        files_deleted += 1
        deleted_lines += record.line_count

    for relative in sorted(pre_paths & post_paths):
        before_record = pre.records[relative]
        after_record = post.records[relative]
        if before_record.sha256 == after_record.sha256:
            continue
        if before_record.is_binary or after_record.is_binary:
            binary_files += 1
            binary_bytes += abs(after_record.size_bytes - before_record.size_bytes)
            continue

        before_path = pre.content_of(relative)
        after_path = workspace / relative
        if before_path is None:
            # Without the pre-run bytes a line diff is not recoverable. Approximating it
            # would be inventing a number, so the whole file counts as rewritten.
            files_modified += 1
            deleted_lines += before_record.line_count
            added_lines += after_record.line_count
            continue

        plus, minus = _diff_counts(_lines(before_path), _lines(after_path))
        if plus or minus:
            files_modified += 1
        added_lines += plus
        deleted_lines += minus

    return Churn(
        added_lines=added_lines,
        deleted_lines=deleted_lines,
        files_added=files_added,
        files_deleted=files_deleted,
        files_modified=files_modified,
        binary_bytes_changed=binary_bytes,
        binary_files_changed=binary_files,
    )
