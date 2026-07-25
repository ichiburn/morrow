"""Pricing batches every SKU into one pipeline round trip."""

from unittest.mock import patch

import pytest

from orders.composition import warm_catalog
from orders.domain.models import OrderItem
from orders.domain.pricing import PricingService, UnknownSku


def test_price_items_uses_a_single_pipeline(client) -> None:
    warm_catalog(client)
    svc = PricingService(client)
    with patch.object(client, "pipeline", wraps=client.pipeline) as spy:
        lines = svc.price_items([OrderItem("WIDGET", 2), OrderItem("GADGET", 1)])
    assert [(line.sku, line.line_total) for line in lines] == [
        ("WIDGET", 2000),
        ("GADGET", 2500),
    ]
    spy.assert_called_once()  # one pipeline for all SKUs


def test_price_items_unknown_sku(client) -> None:
    warm_catalog(client)
    svc = PricingService(client)
    with pytest.raises(UnknownSku):
        svc.price_items([OrderItem("NOPE", 1)])
