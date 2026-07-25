from orders.composition import warm_catalog
from orders.domain.inventory import InventoryService


def test_reserve_success(cache) -> None:
    warm_catalog(cache)
    inv = InventoryService(cache)
    assert inv.reserve("ord-1", "WIDGET", 5).ok


def test_reserve_insufficient_stock(cache) -> None:
    warm_catalog(cache)
    inv = InventoryService(cache)
    result = inv.reserve("ord-1", "SPROCKET", 1)
    assert not result.ok
    assert result.reason == "insufficient_stock"


def test_reserve_is_idempotent(cache) -> None:
    warm_catalog(cache)
    inv = InventoryService(cache)
    assert inv.reserve("ord-1", "WIDGET", 1).ok
    duplicate = inv.reserve("ord-1", "WIDGET", 1)
    assert not duplicate.ok
    assert duplicate.reason == "duplicate_reservation"
