"""The cache port the domain depends on.

The domain speaks in terms of *intent* (get many prices, hold a reservation,
rank promotions) rather than any particular backend. Concrete backends live in
``orders.adapters`` and are selected once, in ``orders.composition``.
"""

from collections.abc import Sequence
from typing import Protocol


class CacheUnavailable(Exception):
    """Raised by an adapter when the backing cache cannot be reached.

    The domain catches this to degrade gracefully; it never imports any
    backend-specific exception type.
    """


class CachePort(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None: ...

    def get_many(self, keys: Sequence[str]) -> list[str | None]:
        """Fetch several keys, ideally in a single round trip."""
        ...

    def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        """Set only if the key is absent; return whether it was set."""
        ...

    def add_scored(self, collection: str, member: str, score: float) -> None:
        """Add a member to a score-ordered collection."""
        ...

    def range_by_score(
        self, collection: str, min_score: float, max_score: float
    ) -> list[str]:
        """Return members whose score is within ``[min_score, max_score]``,
        in ascending score order."""
        ...
