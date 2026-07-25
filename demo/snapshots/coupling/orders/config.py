"""Application configuration.

The Redis connection URL is parsed here into host/port/db, which the composition
root feeds straight into ``redis.Redis(...)``.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RedisSettings:
    host: str
    port: int
    db: int


def parse_redis_url(url: str) -> RedisSettings:
    parsed = urlsplit(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    db = int(parsed.path.lstrip("/") or "0")
    return RedisSettings(host=host, port=port, db=db)


@dataclass(frozen=True)
class Config:
    redis_url: str = "redis://localhost:6379/0"
    reservation_ttl_seconds: int = 300

    @property
    def redis(self) -> RedisSettings:
        return parse_redis_url(self.redis_url)
