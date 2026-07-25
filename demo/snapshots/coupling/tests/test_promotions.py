"""Promotions use a Redis sorted set (ZADD / ZRANGEBYSCORE)."""

from orders.composition import warm_catalog
from orders.domain.models import PROMO_INDEX
from orders.domain.promotions import PromotionService


def test_sorted_set_ranks_by_threshold(client) -> None:
    warm_catalog(client)
    # ZRANGEBYSCORE returns applicable codes in ascending threshold order.
    assert client.zrangebyscore(PROMO_INDEX, 0, 12000) == ["WELCOME", "BIG10", "MEGA"]
    assert client.zrangebyscore(PROMO_INDEX, 0, 1000) == ["WELCOME"]


def test_best_discount_picks_highest_applicable(client) -> None:
    warm_catalog(client)
    promo = PromotionService(client)
    assert promo.best_discount(1000) == 100
    assert promo.best_discount(6000) == 1000
    assert promo.best_discount(12000) == 2500
