"""Pure number/quantity normalisation for Indian bills and cash. No I/O, no model calls."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Tamil digits U+0BE6..U+0BEF and Devanagari digits U+0966..U+096F -> ASCII
_INDIC = {chr(0x0BE6 + i): str(i) for i in range(10)} | {chr(0x0966 + i): str(i) for i in range(10)}
_CURRENCY = re.compile(r"(₹|\brs\.?|\binr\b|/-)", re.IGNORECASE)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_QTY = re.compile(r"(-?\d+(?:\.\d+)?)\s*([a-z]+)?")
# Every value here is one of the documented units: L, ml, pcs, pack, box, kg, g.
_UNITS = {
    "l": "L", "ltr": "L", "ltrs": "L", "lt": "L", "lit": "L",
    "litre": "L", "litres": "L", "liter": "L", "liters": "L",
    "ml": "ml", "mls": "ml",
    "pcs": "pcs", "pc": "pcs", "piece": "pcs", "pieces": "pcs", "nos": "pcs", "no": "pcs",
    "pack": "pack", "packs": "pack", "pkt": "pack", "pkts": "pack", "packet": "pack", "packets": "pack",
    "box": "box", "boxes": "box",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
}


def to_ascii_digits(s: str) -> str:
    return "".join(_INDIC.get(ch, ch) for ch in s)


def _finite(d: Decimal) -> Decimal | None:
    return d if d.is_finite() else None


def normalize_amount(value: str | int | float | Decimal | None) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return _finite(Decimal(str(value)))
        except InvalidOperation:
            return None
    s = _CURRENCY.sub("", to_ascii_digits(str(value))).replace(",", "").strip()
    m = _NUMBER.search(s)
    if not m:
        return None
    try:
        return _finite(Decimal(m.group(0)))
    except InvalidOperation:
        return None


def _unit(raw: str) -> str:
    """Map a raw unit word to the documented unit set; unknown words map to ""."""
    raw = raw.lower().strip().strip(".")
    return _UNITS.get(raw, "")


def parse_qty(value: str | int | float | None, unit_hint: str = "") -> tuple[Decimal | None, str]:
    hint = _unit(unit_hint)
    if value is None or isinstance(value, bool):
        return None, hint
    s = to_ascii_digits(str(value)).lower().replace(",", "")
    m = _QTY.search(s)
    if not m:
        return None, ""
    try:
        qty = _finite(Decimal(m.group(1)))
    except InvalidOperation:
        return None, ""
    if qty is None:
        return None, ""
    unit = _unit(m.group(2) or "") or hint
    return qty, unit


def rupees(d: Decimal | None) -> int | None:
    if d is None or not d.is_finite():
        return None
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
