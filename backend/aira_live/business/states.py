"""Explicit transaction states (spec §5). State changes only through transition()."""
from typing import Literal

ORDER_STATES = ["DRAFT", "ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED", "DISCREPANCY",
                "PAYMENT_PENDING", "PAYMENT_CONFIRMED", "CLOSED"]
SALE_STATES = ["SALE_PENDING", "SALE_COMPLETED", "SALE_VOID"]
PAYMENT_STATES = ["PENDING", "CONFIRMED", "FAILED"]

_ORDER = {
    "DRAFT": {"ORDER_PLACED"},
    "ORDER_PLACED": {"DELIVERY_PENDING"},
    "DELIVERY_PENDING": {"DELIVERY_RECONCILED", "DISCREPANCY"},
    "DISCREPANCY": {"DELIVERY_RECONCILED", "PAYMENT_PENDING"},
    # A matching count can be followed by a bill that does not match (the bill is checked after the count).
    "DELIVERY_RECONCILED": {"PAYMENT_PENDING", "DISCREPANCY"},
    "PAYMENT_PENDING": {"PAYMENT_CONFIRMED"},
    "PAYMENT_CONFIRMED": {"CLOSED"},
    "CLOSED": set(),
}
_SALE = {"SALE_PENDING": {"SALE_COMPLETED", "SALE_VOID"}, "SALE_COMPLETED": set(), "SALE_VOID": set()}
_PAYMENT = {"PENDING": {"CONFIRMED", "FAILED"}, "CONFIRMED": set(), "FAILED": set()}
_TABLES = {"order": _ORDER, "sale": _SALE, "payment": _PAYMENT}


class InvalidTransition(ValueError):
    pass


def transition(entity: Literal["order", "sale", "payment"], current: str, target: str) -> str:
    table = _TABLES.get(entity)
    if table is None:
        raise InvalidTransition(f"unknown entity {entity!r}")
    if current not in table:
        raise InvalidTransition(f"{entity}: unknown state {current!r}")
    if target not in table[current]:
        raise InvalidTransition(f"{entity}: {current} -> {target} not allowed")
    return target


def allowed(entity: Literal["order", "sale", "payment"], current: str) -> set[str]:
    return set(_TABLES.get(entity, {}).get(current, set()))
