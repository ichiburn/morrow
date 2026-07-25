"""The public order service: quote and place orders.

Orchestrates pricing, promotions and inventory. Promotions are best-effort: if
Redis is unreachable we catch ``redis.exceptions.ConnectionError`` and still
quote the order without a discount.
"""

from collections.abc import Sequence

import redis

from orders.domain.inventory import InventoryService
from orders.domain.models import CURRENCY, OrderItem, OrderResult, Quote
from orders.domain.pricing import PricingService
from orders.domain.promotions import PromotionService


class OrderService:
    def __init__(
        self,
        pricing: PricingService,
        inventory: InventoryService,
        promotions: PromotionService,
    ) -> None:
        self._pricing = pricing
        self._inventory = inventory
        self._promotions = promotions

    def quote(self, items: Sequence[OrderItem]) -> Quote:
        lines = self._pricing.price_items(items)
        subtotal = sum(line.line_total for line in lines)
        try:
            discount = self._promotions.best_discount(subtotal)
        except redis.exceptions.ConnectionError:
            # Promotions are non-critical; degrade to no discount.
            discount = 0
        return Quote(
            lines=lines,
            subtotal=subtotal,
            discount=discount,
            total=subtotal - discount,
            currency=CURRENCY,
        )

    def place_order(
        self, order_id: str, customer_id: str, items: Sequence[OrderItem]
    ) -> OrderResult:
        quote = self.quote(items)
        for line in quote.lines:
            reservation = self._inventory.reserve(order_id, line.sku, line.quantity)
            if not reservation.ok:
                return OrderResult(
                    order_id=order_id,
                    status="rejected",
                    quote=quote,
                    reason=reservation.reason,
                )
        return OrderResult(order_id=order_id, status="confirmed", quote=quote)
