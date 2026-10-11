"""Exact rupee arithmetic for the counter. Integer rupees only: no paise, and nothing on credit.

The ₹2000 note is left out of change plans (withdrawn from circulation), but `total_of` still counts one if
the cash reader saw it. Nothing here judges whether a note is genuine.
"""
from collections.abc import Iterable, Mapping
from typing import Any

DENOMINATIONS = (500, 200, 100, 50, 20, 10, 5, 2, 1)


class InsufficientTender(ValueError):
    """The customer handed over less than the total. `short` is how many rupees are missing."""

    def __init__(self, total: int, tendered: int):
        self.total = total
        self.tendered = tendered
        self.short = total - tendered
        super().__init__(f"short by {self.short}")


def _rupees(value: Any, name: str) -> int:
    """A whole, non-negative rupee amount. Integral floats (from JSON) are accepted; paise are not."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a whole number of rupees, got {type(value).__name__}")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{name} must be whole rupees (no paise), got {value}")
    amount = int(value)
    if amount < 0:
        raise ValueError(f"{name} must be non-negative, got {amount}")
    return amount


def change_due(total: int, tendered: int) -> int:
    total = _rupees(total, "total")
    tendered = _rupees(tendered, "tendered")
    if tendered < total:
        raise InsufficientTender(total, tendered)
    return tendered - total


def notes_for(amount: int) -> list[dict]:
    """Fewest notes and coins that make `amount`, highest first: [{"denomination": 50, "count": 1}, ...]."""
    rem = _rupees(amount, "amount")
    out: list[dict] = []
    for d in DENOMINATIONS:
        count, rem = divmod(rem, d)
        if count:
            out.append({"denomination": d, "count": count})
    return out


def _field(note: Any, key: str) -> Any:
    return note[key] if isinstance(note, Mapping) else getattr(note, key)


def total_of(notes: Iterable[Any]) -> int:
    """Sum of [{"denomination": int, "count": int}, ...] (dicts or objects with those attributes)."""
    total = 0
    for note in notes:
        denomination = _rupees(_field(note, "denomination"), "denomination")
        count = _rupees(_field(note, "count"), "count")
        if denomination == 0:
            raise ValueError("denomination must be positive")
        total += denomination * count
    return total
