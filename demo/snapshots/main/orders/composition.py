"""Composition root: the single place that picks a cache implementation.

Everything else depends on ``CachePort``. To swap the backend (e.g. add an
in-memory cache for local and test runs), only the one starred line below
changes, plus the new adapter module.
"""

from orders.adapters.redis_cache import RedisCache
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
from orders.domain.ports import CachePort
from orders.domain.pricing import PricingService
from orders.domain.promotions import PromotionService


def warm_catalog(cache: CachePort) -> None:
    """Load the fixed demo catalog into the cache."""
    for sku, price in PRICES.items():
        cache.set(price_key(sku), str(price))
    for sku, quantity in STOCK.items():
        cache.set(stock_key(sku), str(quantity))
    for code, (threshold, discount) in PROMOTIONS.items():
        cache.add_scored(PROMO_INDEX, code, float(threshold))
        cache.set(promo_key(code), str(discount))


def build_order_service(config: Config, *, cache: CachePort | None = None) -> OrderService:
    active_cache: CachePort = (
        cache if cache is not None else RedisCache.from_url(config.redis_url)  # ★ impl choice
    )
    warm_catalog(active_cache)
    return OrderService(
        PricingService(active_cache),
        InventoryService(active_cache),
        PromotionService(active_cache),
    )
