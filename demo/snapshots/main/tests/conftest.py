import fakeredis
import pytest

from orders.adapters.redis_cache import RedisCache
from orders.composition import build_order_service
from orders.config import Config
from orders.domain.order_service import OrderService


@pytest.fixture
def client() -> fakeredis.FakeStrictRedis:
    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture
def cache(client: fakeredis.FakeStrictRedis) -> RedisCache:
    return RedisCache(client)


@pytest.fixture
def service(cache: RedisCache) -> OrderService:
    return build_order_service(Config(), cache=cache)
