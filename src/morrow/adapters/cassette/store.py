"""Cassette bytes on disk: how they are written, and how they are read back.

Both directions go through one encoder. A cassette is only useful if regenerating it from
the same evidence produces the same bytes — otherwise ``verify`` would report drift that
is really just an encoder that sorts keys differently today than it did yesterday. So the
encoding is pinned here: keys sorted, integers only where the schema says integers,
``allow_nan=False``, ASCII, LF, exactly one trailing newline.

Reading is deliberately paranoid about what it will accept from a directory. Every entry
must be a regular file directly under the root and must not be a symlink, because a
cassette is a thing people fetch from a pull request and a symlink named ``r0.events.jsonl``
would make ``verify`` read whatever it points at (evidence.md §5.1).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from morrow.domain.cassette import MANIFEST_NAME, Manifest
from morrow.domain.events import AgentEvent

#: Ceilings on what a cassette may be, enforced before any of it is read.
#:
#: A cassette arrives from a pull request, and ``read_cassette`` loads all of it into
#: memory to hash it. Without a bound, a 20 GB ``r0.events.jsonl`` — or a million tiny
#: files — kills the verifier before a single digest is checked, which turns "the evidence
#: is hostile" into "the process died" rather than into exit 2.
#:
#: The limits sit far above anything a real recording produces: the largest committed
#: event stream is 25 KB across 20 files, so these leave three orders of magnitude of
#: headroom while still bounding the damage.
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILE_COUNT = 256


class CassetteReadError(RuntimeError):
    """The cassette could not be read at all: missing directory, unreadable file, a
    symlink, a nested path, or more bytes than the limits above allow. This is an
    infrastructure failure, distinct from a cassette whose bytes are present but do not
    match their digests."""


def encode_json(payload: Any) -> bytes:
    """The one JSON encoding used for every cassette file.

    ``allow_nan=False`` matters: a NaN would serialise as the non-standard ``NaN`` token,
    which most parsers reject, and a value that cannot round-trip has no business in
    evidence.
    """
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False)
    return (text + "\n").encode("ascii")


def encode_events(events: Sequence[AgentEvent]) -> bytes:
    """JSON Lines, one normalized event per line, in ``seq`` order.

    Each line is compact and key-sorted; the file ends with a single newline. Events are
    written in ascending ``seq`` because ``seq`` is the only ordering the model defines
    (evidence.md §6.2).
    """
    lines = [
        json.dumps(
            event.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        for event in sorted(events, key=lambda e: e.seq)
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def digest_of(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CassetteBytes:
    """A cassette as it was found on disk, before any of it is trusted.

    ``files`` excludes the manifest: the manifest lists the digests, so it cannot list its
    own, and including it would invite a self-referential check that always passes.
    """

    root: Path
    manifest_bytes: bytes
    files: Mapping[str, bytes]


def read_cassette(root: Path) -> CassetteBytes:
    """Read every file in a cassette directory into memory.

    Rejects anything that is not a regular, non-symlink file directly under ``root``. The
    manifest must be present; its contents are not parsed here, because a manifest whose
    bytes are corrupt is a verification verdict, not a read failure.
    """
    # A symlinked root would have verification read a directory the caller did not name.
    # ``is_dir()`` alone follows the link, so the link itself is checked first.
    if root.is_symlink():
        raise CassetteReadError(f"cassette root is a symlink: {root}")
    if not root.is_dir():
        raise CassetteReadError(f"not a cassette directory: {root}")

    entries = sorted(root.iterdir())
    if len(entries) > MAX_FILE_COUNT:
        raise CassetteReadError(
            f"cassette holds {len(entries)} entries, over the limit of {MAX_FILE_COUNT}"
        )

    files: dict[str, bytes] = {}
    manifest_bytes: bytes | None = None
    total = 0
    for entry in entries:
        if entry.is_symlink():
            raise CassetteReadError(f"symlink in cassette: {entry.name}")
        if not entry.is_file():
            raise CassetteReadError(f"not a regular file in cassette: {entry.name}")
        # Size is checked before the read, so an oversized file is refused rather than
        # loaded and then complained about.
        size = entry.stat().st_size
        if size > MAX_FILE_BYTES:
            raise CassetteReadError(
                f"{entry.name} is {size} bytes, over the per-file limit of {MAX_FILE_BYTES}"
            )
        total += size
        if total > MAX_TOTAL_BYTES:
            raise CassetteReadError(
                f"cassette exceeds the total size limit of {MAX_TOTAL_BYTES} bytes"
            )
        try:
            payload = entry.read_bytes()
        except OSError as error:  # unreadable file, permissions, device
            raise CassetteReadError(f"cannot read {entry.name}: {error}") from error
        # The file could have been swapped between the stat and the read. Re-checking the
        # length closes the window for growth; a same-size substitution is caught by the
        # digest a step later.
        if len(payload) > MAX_FILE_BYTES:
            raise CassetteReadError(f"{entry.name} grew past the per-file limit while reading")
        if entry.name == MANIFEST_NAME:
            manifest_bytes = payload
        else:
            files[entry.name] = payload

    if manifest_bytes is None:
        raise CassetteReadError(f"cassette has no {MANIFEST_NAME}: {root}")
    return CassetteBytes(root=root, manifest_bytes=manifest_bytes, files=files)


def write_cassette(root: Path, manifest: Manifest, files: Mapping[str, bytes]) -> None:
    """Write a cassette, checking that the manifest describes exactly these files.

    The check is here rather than in ``verify`` as well as: a cassette that is
    self-inconsistent the moment it is written is a bug in the recorder, and finding it at
    write time points at the right place.
    """
    listed = set(manifest.digests)
    actual = set(files)
    if listed != actual:
        missing = sorted(listed - actual)
        extra = sorted(actual - listed)
        raise ValueError(
            f"manifest digests do not match the files being written "
            f"(missing {missing}, unexpected {extra})"
        )
    for name, payload in files.items():
        recorded = manifest.digests[name]
        if digest_of(payload) != recorded:
            raise ValueError(f"digest recorded for {name} does not match its bytes")

    root.mkdir(parents=True, exist_ok=True)
    for name, payload in sorted(files.items()):
        (root / name).write_bytes(payload)
    (root / MANIFEST_NAME).write_bytes(encode_json(manifest.model_dump(mode="json")))
