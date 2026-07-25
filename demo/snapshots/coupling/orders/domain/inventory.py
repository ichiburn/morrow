"""Inventory: check stock and hold a reservation.

The reservation hold uses Redis ``SET key value NX EX ttl``: NX makes the hold
unique (a duplicate reservation for the same order/sku fails), and EX lets the
hold expire so an abandoned checkout releases itself.
"""

import redis

from orders.domain.models import RESERVATION_TTL, Reservation, hold_key, stock_key


class InventoryService:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def reserve(self, order_id: str, sku: str, quantity: int) -> Reservation:
        raw = self._client.get(stock_key(sku))
        stock = int(raw) if raw is not None else 0
        if quantity > stock:
            return Reservation(ok=False, reason="insufficient_stock")

        # SETNX + TTL: only the first reservation for this (order, sku) wins,
        # and the hold self-expires after RESERVATION_TTL seconds.
        acquired = self._client.set(
            hold_key(order_id, sku), str(quantity), nx=True, ex=RESERVATION_TTL
        )
        if not acquired:
            return Reservation(ok=False, reason="duplicate_reservation")
        return Reservation(ok=True)
