"""Pricing: turn order items into priced line items.

Uses a Redis pipeline to fetch every SKU price in one round trip -- a real
performance win when an order has many lines.
"""

from collections.abc import Sequence

import redis

from orders.domain.models import LineItem, OrderItem, price_key


class UnknownSku(Exception):
    def __init__(self, sku: str) -> None:
        super().__init__(f"unknown sku: {sku}")
        self.sku = sku


class PricingService:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def price_items(self, items: Sequence[OrderItem]) -> tuple[LineItem, ...]:
        items = list(items)
        pipe = self._client.pipeline()
        for item in items:
            pipe.get(price_key(item.sku))
        prices = pipe.execute()

        lines: list[LineItem] = []
        for item, raw in zip(items, prices, strict=True):
            if raw is None:
                raise UnknownSku(item.sku)
            unit = int(raw)
            lines.append(
                LineItem(
                    sku=item.sku,
                    quantity=item.quantity,
                    unit_price=unit,
                    line_total=unit * item.quantity,
                )
            )
        return tuple(lines)
