"""Pricing: turn order items into priced line items.

Depends only on :class:`CachePort`. Batch fetching is expressed as a single
``get_many`` call; whether that becomes a pipeline, an mget, or a dict lookup
is the adapter's concern.
"""

from collections.abc import Sequence

from orders.domain.models import LineItem, OrderItem, price_key
from orders.domain.ports import CachePort


class UnknownSku(Exception):
    def __init__(self, sku: str) -> None:
        super().__init__(f"unknown sku: {sku}")
        self.sku = sku


class PricingService:
    def __init__(self, cache: CachePort) -> None:
        self._cache = cache

    def price_items(self, items: Sequence[OrderItem]) -> tuple[LineItem, ...]:
        items = list(items)
        prices = self._cache.get_many([price_key(item.sku) for item in items])
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
