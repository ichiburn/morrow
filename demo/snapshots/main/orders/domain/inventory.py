"""Inventory: check stock and hold a reservation.

A reservation is a hold that must be unique per (order, sku) for a bounded
window. That intent is expressed as ``set_if_absent`` with a TTL; the domain
does not know how the adapter implements it.
"""

from orders.domain.models import RESERVATION_TTL, Reservation, hold_key, stock_key
from orders.domain.ports import CachePort


class InventoryService:
    def __init__(self, cache: CachePort) -> None:
        self._cache = cache

    def reserve(self, order_id: str, sku: str, quantity: int) -> Reservation:
        raw = self._cache.get(stock_key(sku))
        stock = int(raw) if raw is not None else 0
        if quantity > stock:
            return Reservation(ok=False, reason="insufficient_stock")

        acquired = self._cache.set_if_absent(
            hold_key(order_id, sku), str(quantity), RESERVATION_TTL
        )
        if not acquired:
            return Reservation(ok=False, reason="duplicate_reservation")
        return Reservation(ok=True)
