"""The order service degrades gracefully when the promotions cache is down.

Here the degradation is triggered by the domain-level ``CacheUnavailable``; the
domain never references any backend-specific exception.
"""

from orders.composition import warm_catalog
from orders.domain.inventory import InventoryService
from orders.domain.models import OrderItem
from orders.domain.order_service import OrderService
from orders.domain.ports import CacheUnavailable
from orders.domain.pricing import PricingService


class _BrokenPromotions:
    def best_discount(self, subtotal: int) -> int:
        raise CacheUnavailable("promotions cache down")


def test_quote_degrades_when_promotions_unavailable(cache) -> None:
    warm_catalog(cache)
    svc = OrderService(
        PricingService(cache),
        InventoryService(cache),
        _BrokenPromotions(),  # type: ignore[arg-type]
    )
    quote = svc.quote([OrderItem("WIDGET", 2), OrderItem("GIZMO", 1)])
    assert quote.subtotal == 2500
    assert quote.discount == 0  # degraded: no promo applied
    assert quote.total == 2500
