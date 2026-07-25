"""Composition root: build a Redis client and wire the services.

Each domain service talks to the same ``redis.Redis`` client directly.
"""

import redis

from orders.config import Config
from orders.domain.inventory import InventoryService
from orders.domain.models import (
    PRICES,
    PROMO_INDEX,
    PROMOTIONS,
    STOCK,
    price_key,
    promo_key,
    stock_key,
)
from orders.domain.order_service import OrderService
from orders.domain.pricing import PricingService
from orders.domain.promotions import PromotionService


def warm_catalog(client: redis.Redis) -> None:
    """Load the fixed demo catalog into Redis."""
    for sku, price in PRICES.items():
        client.set(price_key(sku), str(price))
    for sku, quantity in STOCK.items():
        client.set(stock_key(sku), str(quantity))
    for code, (threshold, discount) in PROMOTIONS.items():
        client.zadd(PROMO_INDEX, {code: float(threshold)})
        client.set(promo_key(code), str(discount))


def build_order_service(
    config: Config | None = None, *, client: redis.Redis | None = None
) -> OrderService:
    settings = (config or Config()).redis
    active_client = client if client is not None else redis.Redis(
        host=settings.host, port=settings.port, db=settings.db, decode_responses=True
    )
    warm_catalog(active_client)
    return OrderService(
        PricingService(active_client),
        InventoryService(active_client),
        PromotionService(active_client),
    )
