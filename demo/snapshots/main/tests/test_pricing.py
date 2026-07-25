import pytest

from orders.composition import warm_catalog
from orders.domain.models import OrderItem
from orders.domain.pricing import PricingService, UnknownSku


def test_price_items_returns_line_totals(cache) -> None:
    warm_catalog(cache)
    svc = PricingService(cache)
    lines = svc.price_items([OrderItem("WIDGET", 2), OrderItem("GADGET", 1)])
    assert [(line.sku, line.line_total) for line in lines] == [
        ("WIDGET", 2000),
        ("GADGET", 2500),
    ]


def test_price_items_unknown_sku(cache) -> None:
    warm_catalog(cache)
    svc = PricingService(cache)
    with pytest.raises(UnknownSku):
        svc.price_items([OrderItem("NOPE", 1)])
