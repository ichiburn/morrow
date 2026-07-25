"""Redis-backed implementation of :class:`CachePort`.

All Redis-specific semantics live here: pipelining for batch reads, ``SET NX EX``
for reservation holds, and sorted sets for ranked promotions. Backend errors are
translated to the domain's :class:`CacheUnavailable`.
"""

from collections.abc import Sequence

import redis

from orders.domain.ports import CacheUnavailable


class RedisCache:
    """Implements ``CachePort`` structurally (duck-typed Protocol)."""

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    @classmethod
    def from_url(cls, url: str) -> "RedisCache":
        return cls(redis.Redis.from_url(url, decode_responses=True))

    def get(self, key: str) -> str | None:
        try:
            return self._client.get(key)
        except redis.exceptions.ConnectionError as exc:
            raise CacheUnavailable(str(exc)) from exc

    def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        try:
            self._client.set(key, value, ex=ttl_seconds)
        except redis.exceptions.ConnectionError as exc:
            raise CacheUnavailable(str(exc)) from exc

    def get_many(self, keys: Sequence[str]) -> list[str | None]:
        keys = list(keys)
        if not keys:
            return []
        pipe = self._client.pipeline()
        for key in keys:
            pipe.get(key)
        try:
            return list(pipe.execute())
        except redis.exceptions.ConnectionError as exc:
            raise CacheUnavailable(str(exc)) from exc

    def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        try:
            return bool(self._client.set(key, value, nx=True, ex=ttl_seconds))
        except redis.exceptions.ConnectionError as exc:
            raise CacheUnavailable(str(exc)) from exc

    def add_scored(self, collection: str, member: str, score: float) -> None:
        try:
            self._client.zadd(collection, {member: score})
        except redis.exceptions.ConnectionError as exc:
            raise CacheUnavailable(str(exc)) from exc

    def range_by_score(
        self, collection: str, min_score: float, max_score: float
    ) -> list[str]:
        try:
            return list(self._client.zrangebyscore(collection, min_score, max_score))
        except redis.exceptions.ConnectionError as exc:
            raise CacheUnavailable(str(exc)) from exc
