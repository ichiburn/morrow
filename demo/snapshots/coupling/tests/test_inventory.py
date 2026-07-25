"""Reservation relies on Redis SETNX + TTL semantics."""

from orders.composition import warm_catalog
from orders.domain.inventory import InventoryService
from orders.domain.models import RESERVATION_TTL, hold_key


def test_reserve_sets_a_bounded_ttl_hold(client) -> None:
    warm_catalog(client)
    inv = InventoryService(client)
    assert inv.reserve("ord-1", "WIDGET", 1).ok

    key = hold_key("ord-1", "WIDGET")
    ttl = client.ttl(key)
    assert 0 < ttl <= RESERVATION_TTL  # EX semantics: the hold self-expires

    # NX semantics: the hold key exists, so a second nx-set must fail.
    assert client.set(key, "9", nx=True, ex=RESERVATION_TTL) is None


def test_reserve_duplicate_rejected(client) -> None:
    warm_catalog(client)
    inv = InventoryService(client)
    assert inv.reserve("ord-1", "WIDGET", 1).ok
    duplicate = inv.reserve("ord-1", "WIDGET", 1)
    assert not duplicate.ok
    assert duplicate.reason == "duplicate_reservation"


def test_reserve_insufficient_stock(client) -> None:
    warm_catalog(client)
    inv = InventoryService(client)
    result = inv.reserve("ord-1", "SPROCKET", 1)
    assert not result.ok
    assert result.reason == "insufficient_stock"
