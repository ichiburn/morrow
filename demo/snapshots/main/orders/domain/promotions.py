"""Promotions: pick the best applicable discount for a subtotal.

Promotions are ranked by a minimum-spend threshold. Finding the applicable
ones is expressed as ``range_by_score``; the backend may realize that with a
sorted set, a SQL index, or an in-memory bisect.
"""

from orders.domain.models import PROMO_INDEX, promo_key
from orders.domain.ports import CachePort


class PromotionService:
    def __init__(self, cache: CachePort) -> None:
        self._cache = cache

    def best_discount(self, subtotal: int) -> int:
        codes = self._cache.range_by_score(PROMO_INDEX, 0, subtotal)
        best = 0
        for code in codes:
            raw = self._cache.get(promo_key(code))
            if raw is not None:
                best = max(best, int(raw))
        return best
