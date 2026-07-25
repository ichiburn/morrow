"""Domain models and the fixed demo catalog.

Pure data: no I/O, no cache, no framework. Shared verbatim by both demo
snapshots so that the observable behavior is identical across them.
"""

from dataclasses import dataclass

# --- Fixed demo catalog (single source of truth for seed data) --------------

# All monetary amounts are integer minor units (cents) to avoid float error.
PRICES: dict[str, int] = {
    "WIDGET": 1000,
    "GADGET": 2500,
    "GIZMO": 500,
    "SPROCKET": 750,
}

STOCK: dict[str, int] = {
    "WIDGET": 100,
    "GADGET": 50,
    "GIZMO": 200,
    "SPROCKET": 0,  # intentionally out of stock
}

# code -> (minimum spend threshold, discount) both in cents.
PROMOTIONS: dict[str, tuple[int, int]] = {
    "WELCOME": (0, 100),
    "BIG10": (5000, 1000),
    "MEGA": (10000, 2500),
}

CURRENCY = "USD"
RESERVATION_TTL = 300  # seconds a reservation hold is kept

# The ranked index name for promotions (min-spend threshold -> code).
PROMO_INDEX = "promo:threshold"


# --- Key builders (keep read and write paths in agreement) ------------------


def price_key(sku: str) -> str:
    return f"price:{sku}"


def stock_key(sku: str) -> str:
    return f"stock:{sku}"


def hold_key(order_id: str, sku: str) -> str:
    return f"hold:{order_id}:{sku}"


def promo_key(code: str) -> str:
    return f"promo:{code}"


# --- Value objects ----------------------------------------------------------


@dataclass(frozen=True)
class OrderItem:
    sku: str
    quantity: int


@dataclass(frozen=True)
class LineItem:
    sku: str
    quantity: int
    unit_price: int
    line_total: int


@dataclass(frozen=True)
class Quote:
    lines: tuple[LineItem, ...]
    subtotal: int
    discount: int
    total: int
    currency: str


@dataclass(frozen=True)
class Reservation:
    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    status: str  # "confirmed" | "rejected"
    quote: Quote
    reason: str | None = None
