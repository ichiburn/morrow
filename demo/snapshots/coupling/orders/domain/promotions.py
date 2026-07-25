"""Promotions: pick the best applicable discount for a subtotal.

Promotions live in a Redis sorted set scored by their minimum-spend threshold.
``ZRANGEBYSCORE index 0 subtotal`` returns exactly the applicable codes, in
ascending threshold order, without scanning every promotion.
"""

import redis

from orders.domain.models import PROMO_INDEX, promo_key


class PromotionService:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def best_discount(self, subtotal: int) -> int:
        codes = self._client.zrangebyscore(PROMO_INDEX, 0, subtotal)
        best = 0
        for code in codes:
            raw = self._client.get(promo_key(code))
            if raw is not None:
                best = max(best, int(raw))
        return best
