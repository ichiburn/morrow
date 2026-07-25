"""Application configuration.

The cache backend is addressed by an opaque connection string. The domain never
sees this; only the adapter (``RedisCache.from_url``) interprets it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    redis_url: str = "redis://localhost:6379/0"
    reservation_ttl_seconds: int = 300
