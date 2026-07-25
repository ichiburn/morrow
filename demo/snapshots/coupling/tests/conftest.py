import fakeredis
import pytest

from orders.composition import build_order_service
from orders.config import Config
from orders.domain.order_service import OrderService


@pytest.fixture
def client() -> fakeredis.FakeStrictRedis:
    return fakeredis.FakeStrictRedis(decode_responses=True)


@pytest.fixture
def service(client: fakeredis.FakeStrictRedis) -> OrderService:
    return build_order_service(Config(), client=client)
