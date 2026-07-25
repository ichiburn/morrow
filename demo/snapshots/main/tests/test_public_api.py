"""Public API contract.

This file is byte-identical across both demo snapshots. It pins the
``OrderService`` method signatures and the observable behavior, so the two
snapshots are proven to expose the same API and produce the same outputs.
"""

import inspect

from orders.domain.models import OrderItem
from orders.domain.order_service import OrderService


def test_quote_signature() -> None:
    params = list(inspect.signature(OrderService.quote).parameters)
    assert params == ["self", "items"]


def test_place_order_signature() -> None:
    params = list(inspect.signature(OrderService.place_order).parameters)
    assert params == ["self", "order_id", "customer_id", "items"]


def test_quote_small_order(service) -> None:
    quote = service.quote([OrderItem("WIDGET", 2), OrderItem("GIZMO", 1)])
    assert quote.subtotal == 2500
    assert quote.discount == 100  # WELCOME (threshold 0)
    assert quote.total == 2400
    assert quote.currency == "USD"


def test_quote_promotion_tier(service) -> None:
    quote = service.quote([OrderItem("GADGET", 3)])
    assert quote.subtotal == 7500
    assert quote.discount == 1000  # BIG10 beats WELCOME
    assert quote.total == 6500


def test_place_order_confirmed(service) -> None:
    result = service.place_order("ord-1", "cust-1", [OrderItem("WIDGET", 2)])
    assert result.status == "confirmed"
    assert result.quote.total == 1900  # 2000 - 100
    assert result.reason is None


def test_place_order_out_of_stock(service) -> None:
    result = service.place_order("ord-2", "cust-1", [OrderItem("SPROCKET", 1)])
    assert result.status == "rejected"
    assert result.reason == "insufficient_stock"
