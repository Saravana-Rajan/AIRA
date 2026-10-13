"""Order vs physically counted vs paper invoice. An invoice is never proof of receipt: the count is.

`order_items` are `{sku, name, qty, unit?, agreedPrice?, packSizeL?, packSizeG?}` and `counted` maps sku to the
verified quantity, both in the order's selling units (L for milk and curd, packs/jars/boxes otherwise). Bill
lines may use other units (packets of a litre product, kg of a pack product); they are converted with the pack
size (from packSizeL/packSizeG, else from the size in the item name). Bill-side discrepancies are reported in the
bill's own units so a tool can read them back as printed.

compare() returns codes only; tools return them as facts with business.hints.fact(kind, ...).
A bill line is booked against an order line only when its name and size match one item clearly; otherwise it is
reported as UNKNOWN_INVOICE_LINE and never guessed. An invoice whose two reads disagree is never used.
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from aira_live.business.text import normalize_digits, tokens

if TYPE_CHECKING:  # duck-typed at runtime: .agreed, .disagreements, .lines[name, qty, unit, unit_price, line_total], .total
    from aira_live.vision.invoice import InvoiceRead

MATCH_MIN = 0.6       # name similarity a bill line needs to be booked against an order line
MATCH_MARGIN = 0.05   # a different item scoring this close to the best means "not sure": report, never guess
EPS = 1e-6

# Unit word -> size of one unit in the base of its dimension (volume: ml, mass: g).
_VOLUME = {"l": 1000.0, "lt": 1000.0, "ltr": 1000.0, "ltrs": 1000.0, "litre": 1000.0, "litres": 1000.0,
           "liter": 1000.0, "liters": 1000.0, "லிட்டர்": 1000.0, "லி": 1000.0, "लीटर": 1000.0, "ली": 1000.0,
           "ml": 1.0, "மிலி": 1.0, "मिली": 1.0, "एमएल": 1.0}
_MASS = {"kg": 1000.0, "kgs": 1000.0, "கிலோ": 1000.0, "किलो": 1000.0,
         "g": 1.0, "gm": 1.0, "gms": 1.0, "gram": 1.0, "grams": 1.0, "கிராம்": 1.0, "ग्राम": 1.0}
_COUNT = {"pack", "packs", "pkt", "pkts", "packet", "packets", "pouch", "pouches", "pc", "pcs", "piece", "pieces",
          "nos", "no", "unit", "units", "box", "boxes", "jar", "jars", "tub", "tubs", "bottle", "bottles",
          "பாக்கெட்", "பெட்டி", "पैकेट", "डिब्बा"}
_UNIT_WORDS = set(_VOLUME) | set(_MASS) | _COUNT
_SIZE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(" + "|".join(re.escape(u) for u in sorted({*_VOLUME, *_MASS}, key=len, reverse=True))
    + r")(?![a-zऀ-ॿ஀-௿])")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

Size = tuple[str, float]   # ("v", ml) | ("m", g) | ("c", 1.0)


class ReconcileResult(BaseModel):
    status: Literal["match", "discrepancy"]
    discrepancies: list[dict]


def _clean(s: str) -> str:
    return normalize_digits(s or "").lower()


def _size(name: str) -> Size | None:
    """The pack size written in a product name ("Sakthi Curd 500 ml" -> ("v", 500.0)), or None."""
    m = _SIZE.search(_clean(name))
    if m is None:
        return None
    qty, unit = float(m.group(1)), m.group(2)
    return ("v", qty * _VOLUME[unit]) if unit in _VOLUME else ("m", qty * _MASS[unit])


def _unit(unit: str | None) -> Size | None:
    """A quantity unit ("L", "pkt.", "kg", "500ml pouch") as (dimension, size in base), or None if unknown."""
    key = re.sub(r"[\s.]", "", _clean(unit or ""))
    if key in _VOLUME:
        return ("v", _VOLUME[key])
    if key in _MASS:
        return ("m", _MASS[key])
    if key in _COUNT or _SIZE.search(_clean(unit or "")):   # "500ml pouch" is one pack
        return ("c", 1.0)
    for tok in tokens(unit or ""):
        if tok in _COUNT:
            return ("c", 1.0)
    return None


def _pack_size(item: dict) -> Size | None:
    if item.get("packSizeL"):
        return ("v", float(item["packSizeL"]) * 1000.0)
    if item.get("packSizeG"):
        return ("m", float(item["packSizeG"]))
    return _size(item.get("name", ""))


def _base(name: str) -> str:
    """The product words of a name: no size, unit words or bare numbers ("SAKTHI CURD 1L" -> "sakthi curd")."""
    return " ".join(t for t in tokens(_SIZE.sub(" ", _clean(name)))
                    if t not in _UNIT_WORDS and not _NUMBER.fullmatch(t))


def _factor(line_unit: str | None, item: dict) -> float | None:
    """How many of the item's selling units one unit on the bill is (1 pkt of 500 ml = 0.5 L; 1 kg = 5 x 200 g).
    1.0 when either unit is unknown; None when the units cannot be converted."""
    lu, iu = _unit(line_unit), _unit(item.get("unit"))
    if lu is None or iu is None:
        return 1.0
    if lu[0] == iu[0]:
        return lu[1] / iu[1]
    pack = _pack_size(item)
    if pack is None:
        return None
    if lu[0] == "c" and pack[0] == iu[0]:      # packets on the bill, litres in the order
        return pack[1] / iu[1]
    if iu[0] == "c" and pack[0] == lu[0]:      # kg (or L) on the bill, packs in the order
        return lu[1] / pack[1]
    return None


def _match_line(line_name: str, order_items: list[dict]) -> dict | None:
    target, line_size = _base(line_name), _size(line_name)
    scored: list[tuple[float, dict]] = []
    for it in order_items:
        item_size = _pack_size(it)
        if line_size and item_size and (line_size[0] != item_size[0] or abs(line_size[1] - item_size[1]) > EPS):
            continue                                       # a 500 ml line is never a 1 L item
        scored.append((SequenceMatcher(None, target, _base(it.get("name", ""))).ratio(), it))
    if not scored:
        return None
    scored.sort(key=lambda s: s[0], reverse=True)
    best_score, best = scored[0]
    if best_score < MATCH_MIN:
        return None
    if any(it["sku"] != best["sku"] and score >= best_score - MATCH_MARGIN for score, it in scored[1:]):
        return None                                        # two items fit about equally well: not sure
    return best


def _rupees(x: float | Decimal) -> int:
    """Whole rupees, half rounded up (₹22.50 -> ₹23), never banker's rounding."""
    return int(Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _price(x: float) -> int | float:
    return round(x) if abs(x - round(x)) < EPS else round(x, 2)


def compare(order_items: list[dict], counted: dict[str, float], invoice: InvoiceRead | None) -> ReconcileResult:
    """order_items: [{sku, name, qty, agreedPrice?}]; counted: sku -> physically counted qty (the only receipt proof)."""
    d: list[dict] = []
    ordered: dict[str, float] = {}
    for it in order_items:
        ordered[it["sku"]] = ordered.get(it["sku"], 0.0) + float(it["qty"])
    for sku, q in ordered.items():
        c = float(counted.get(sku, 0.0))
        if c < q - EPS:
            d.append({"kind": "SHORT_DELIVERY", "sku": sku, "expected": q, "actual": c})
        elif c > q + EPS:
            d.append({"kind": "OVER_DELIVERY", "sku": sku, "expected": q, "actual": c})
    for sku, c in counted.items():
        if sku not in ordered and float(c) > EPS:
            d.append({"kind": "UNORDERED_ITEM", "sku": sku, "expected": 0.0, "actual": float(c)})

    if invoice is not None:
        if not invoice.agreed:
            # Two reads disagree: say "not sure" and ask for another view. None of the bill's numbers are used.
            d.append({"kind": "INVOICE_UNREADABLE", "fields": list(invoice.disagreements)})
        else:
            billed: dict[str, float] = {}           # sku -> billed qty in the order's selling units
            units: dict[str, set[float]] = {}       # sku -> conversion factors of its bill lines
            line_sum = 0
            for line in invoice.lines:
                line_sum += int(line.line_total)
                it = _match_line(line.name, order_items)
                if it is None:
                    d.append({"kind": "UNKNOWN_INVOICE_LINE", "name": line.name})
                    continue
                sku, f = it["sku"], _factor(line.unit, it) or 1.0
                billed[sku] = billed.get(sku, 0.0) + float(line.qty) * f
                units.setdefault(sku, set()).add(f)
                if it.get("agreedPrice") is not None:
                    agreed = float(it["agreedPrice"]) * f       # agreed price per bill unit
                    if abs(line.unit_price - agreed) > 0.5 + EPS:
                        d.append({"kind": "PRICE_MISMATCH", "sku": sku, "expected": _price(agreed),
                                  "actual": line.unit_price})
                expected_total = float(line.qty) * line.unit_price
                if abs(line.line_total - expected_total) > 0.5 + EPS:
                    d.append({"kind": "LINE_TOTAL_MISMATCH", "sku": sku, "expected": _rupees(expected_total),
                              "actual": line.line_total})
            for sku, b in billed.items():
                c = float(counted.get(sku, 0.0))
                if abs(b - c) > EPS:
                    f = next(iter(units[sku])) if len(units[sku]) == 1 else 1.0
                    d.append({"kind": "INVOICE_QTY_MISMATCH", "sku": sku, "expected": round(c / f, 6),
                              "actual": round(b / f, 6)})
            if invoice.total is not None and invoice.total != line_sum:
                d.append({"kind": "TOTAL_MISMATCH", "expected": line_sum, "actual": invoice.total})
    return ReconcileResult(status="match" if not d else "discrepancy", discrepancies=d)


def payable(order_items: list[dict], counted: dict[str, float]) -> int:
    """Pay for what was verified as received (never more than ordered) at the agreed price, in whole rupees."""
    total = Decimal(0)
    for it in order_items:
        if it.get("agreedPrice") is None:
            raise ValueError(f"no agreed price for {it['sku']}")
        qty = max(0.0, min(float(counted.get(it["sku"], 0.0)), float(it["qty"])))
        total += Decimal(str(qty)) * Decimal(str(it["agreedPrice"]))
    return _rupees(total)
