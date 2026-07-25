"""The order service catches ``redis.exceptions.ConnectionError`` and degrades.

When the promotions lookup hits a Redis connection error, the order still
quotes -- just without a discount.
"""

from unittest.mock import patch

import redis

from orders.composition import build_order_service
from orders.config import Config
from orders.domain.models import OrderItem


def test_quote_degrades_on_redis_connection_error(client) -> None:
    service = build_order_service(Config(), client=client)
    with patch.object(
        client, "zrangebyscore", side_effect=redis.exceptions.ConnectionError("down")
    ):
        quote = service.quote([OrderItem("WIDGET", 2), OrderItem("GIZMO", 1)])
    assert quote.subtotal == 2500
    assert quote.discount == 0  # degraded: promotions unavailable
    assert quote.total == 2500
