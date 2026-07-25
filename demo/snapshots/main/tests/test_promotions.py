from orders.composition import warm_catalog
from orders.domain.promotions import PromotionService


def test_best_discount_tiers(cache) -> None:
    warm_catalog(cache)
    promo = PromotionService(cache)
    assert promo.best_discount(1000) == 100  # WELCOME only
    assert promo.best_discount(6000) == 1000  # BIG10
    assert promo.best_discount(12000) == 2500  # MEGA
    assert promo.best_discount(0) == 100  # threshold 0 applies
