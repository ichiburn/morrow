"""Opaque reference registries.

Real paths, tool call identifiers and session identifiers never reach a published
artifact. They are replaced by short opaque references, and the mapping back stays on the
evaluator side. Besides keeping source structure out of the cassettes, this removes the
dictionary attack against hashed paths: knowing a cassette mentions ``p12`` tells you
nothing about whether ``src/auth.py`` exists.

References are assigned in first-appearance order, so the same provider stream always
yields the same references. That is what makes the normalized output byte-reproducible.
"""

from __future__ import annotations

from collections.abc import Mapping


class _Registry:
    """Assigns ``<prefix><n>`` references in first-appearance order."""

    __slots__ = ("_forward", "_limit", "_prefix")

    def __init__(self, prefix: str, limit: int) -> None:
        self._prefix = prefix
        self._limit = limit
        self._forward: dict[str, str] = {}

    def ref(self, value: str) -> str:
        existing = self._forward.get(value)
        if existing is not None:
            return existing
        if len(self._forward) >= self._limit:
            raise RegistryExhaustedError(
                f"more than {self._limit} distinct {self._prefix!r} references in one run"
            )
        assigned = f"{self._prefix}{len(self._forward)}"
        self._forward[value] = assigned
        return assigned

    def known(self, value: str) -> bool:
        return value in self._forward

    @property
    def mapping(self) -> Mapping[str, str]:
        """Real value -> reference. Evaluator side only; never written to a cassette."""
        return dict(self._forward)

    def __len__(self) -> int:
        return len(self._forward)


class RegistryExhaustedError(RuntimeError):
    """Raised when a run produces more distinct references than the schema allows.

    The reference patterns are bounded (``^p[0-9]{1,4}$`` and friends) so that an
    identifier cannot become an unbounded text channel. Overflowing the bound is a
    measurement failure, not something to silently truncate.
    """


class RefRegistry:
    """The three registries a single run needs."""

    def __init__(self) -> None:
        # Bounds match the patterns in morrow.domain.events.
        self.paths = _Registry("p", 10_000)
        self.tools = _Registry("t", 10_000)
        self.sessions = _Registry("s", 1_000)
