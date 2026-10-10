"""Pure: agree two independent cash reads. Totals are computed here, never by the model.

No counterfeit or genuineness judgement is made anywhere. Malformed model output never raises: it is
never counted, and it blocks agreement.
"""
from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal
from typing import Any

from aira_live.vision.cash_models import CashRead
from aira_live.vision.numbers import normalize_amount

NOTES = frozenset({10, 20, 50, 100, 200, 500})
COINS = frozenset({1, 2, 5, 10, 20})
VALID_DENOMINATIONS = NOTES | COINS

HINT_SPREAD = "cash.spread_notes"
HINT_UNSUPPORTED = "cash.unsupported_note"
HINT_SHOW_AGAIN = "cash.show_again"
DISAGREE_CONFIDENCE_FACTOR = 0.5


class _Tally:
    def __init__(self) -> None:
        self.counts: Counter[int] = Counter()
        self.unsupported = False  # a readable value that is not valid INR money (e.g. a 2000 note)
        self.malformed = False    # something the model returned that is not a usable number


def _as_int(value: Any) -> int | None:
    """A whole number from a model value (int, integral float, or numeric string, Indic digits ok)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    if isinstance(value, str):
        d = normalize_amount(value)
        if d is None or d != d.to_integral_value():
            return None
        return int(d)
    return None


def _as_dict(raw: Any) -> dict:
    return raw if isinstance(raw, dict) else {}


def _as_list(value: Any, tally: _Tally) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    tally.malformed = True
    return []


def _valid_for_kind(denomination: int, kind: Any) -> bool:
    k = kind.strip().lower() if isinstance(kind, str) else ""
    if k == "note":
        return denomination in NOTES
    if k == "coin":
        return denomination in COINS
    return denomination in VALID_DENOMINATIONS


def _tally_a(raw: dict) -> _Tally:
    """Read A: aggregated items [{"denomination", "count", "kind"}]."""
    tally = _Tally()
    for item in _as_list(raw.get("items"), tally):
        if not isinstance(item, dict):
            tally.malformed = True
            continue
        d, n = _as_int(item.get("denomination")), _as_int(item.get("count"))
        if d is None or n is None or n < 0:
            tally.malformed = True
            continue
        if not _valid_for_kind(d, item.get("kind")):
            tally.unsupported = True
            continue
        if n:
            tally.counts[d] += n
    return tally


def _tally_b(raw: dict) -> _Tally:
    """Read B: one entry per piece. A bare list reply arrives from generate_json as {"items": [...]}."""
    tally = _Tally()
    pieces = raw.get("pieces") if "pieces" in raw else raw.get("items")
    for piece in _as_list(pieces, tally):
        d = _as_int(piece.get("denomination") if isinstance(piece, dict) else piece)
        if d is None:
            tally.malformed = True
            continue
        if d not in VALID_DENOMINATIONS:
            tally.unsupported = True
            continue
        tally.counts[d] += 1
    return tally


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1"}
    if isinstance(value, (int, float)):
        return value == 1
    return False


def _confidence(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(f):
        return 0.0
    return min(1.0, max(0.0, f))


def _total(counts: Counter[int]) -> int:
    return int(sum(Decimal(d) * n for d, n in counts.items()))


def merge_cash(raw_a: dict, raw_b: dict) -> CashRead:
    """Agree read A (aggregated) with read B (per piece). Never raises on bad model output."""
    a, b = _as_dict(raw_a), _as_dict(raw_b)
    ta, tb = _tally_a(a), _tally_b(b)
    overlapping = _flag(a.get("overlapping")) or _flag(b.get("overlapping"))
    unsupported = ta.unsupported or tb.unsupported
    malformed = ta.malformed or tb.malformed
    conf = min(_confidence(a.get("confidence")), _confidence(b.get("confidence")))
    agreed = bool(ta.counts) and ta.counts == tb.counts and not (overlapping or unsupported or malformed)
    if agreed:
        hint = ""
    elif overlapping:
        hint = HINT_SPREAD
    elif unsupported:
        hint = HINT_UNSUPPORTED
    else:
        hint = HINT_SHOW_AGAIN
    notes = [{"denomination": d, "count": n} for d, n in sorted(ta.counts.items(), reverse=True)]
    return CashRead(
        notes=notes,
        total=_total(ta.counts) if agreed else None,
        agreed=agreed,
        confidence=round(conf if agreed else conf * DISAGREE_CONFIDENCE_FACTOR, 3),
        hint_key=hint,
    )
