"""Pure: compare two independent invoice reads field by field and check the bill's own arithmetic.

No model calls and no I/O. The verdict (`agreed`) and every sum are computed here, never by the model.
Any doubt (a field the reads disagree on, an unreadable field, a bill whose own sums are wrong, a
handwritten bill) lands in `disagreements`, and `agreed` is True only when that list is empty.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from aira_live.vision.invoice_models import InvoiceLine, InvoiceRead
from aira_live.vision.numbers import normalize_amount, parse_qty, rupees

TOLERANCE = Decimal("1")      # ₹1 rounding tolerance
NAME_MATCH = 0.75             # minimum similarity to pair lines across reads
SUPPLIER_MATCH = 0.85         # minimum similarity for two supplier names to count as the same
FIELDS = ("qty", "unit_price", "line_total")


@dataclass
class ParsedLine:
    name: str
    qty: Decimal | None
    unit: str
    unit_price: Decimal | None
    line_total: Decimal | None


def _norm_text(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm_text(a), _norm_text(b)).ratio()


def _fmt(d: Decimal | None) -> str:
    if d is None:
        return "?"
    return str(d.normalize().quantize(Decimal("1")) if d == d.to_integral_value() else d.normalize())


def _finite(d: Decimal | None) -> Decimal | None:
    return d if d is not None and d.is_finite() else None


def _amount(value: Any) -> Decimal | None:
    try:
        return _finite(normalize_amount(value))
    except Exception:  # noqa: BLE001 - an unparseable amount is "unreadable", never a crash
        return None


def _qty(value: Any, unit_hint: Any) -> tuple[Decimal | None, str]:
    try:
        qty, unit = parse_qty(value, str(unit_hint or ""))
    except Exception:  # noqa: BLE001
        return None, ""
    return _finite(qty), unit


def _text(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _parse(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    lines: list[ParsedLine] = []
    items = raw.get("lines")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        qty, unit = _qty(item.get("qty"), item.get("unit"))
        lines.append(ParsedLine(
            name=str(item.get("name") or "").strip(),
            qty=qty,
            unit=unit,
            unit_price=_amount(item.get("unit_price")),
            line_total=_amount(item.get("line_total")),
        ))
    return {
        "supplier": _text(raw.get("supplier")),
        "date": _text(raw.get("date")),
        "handwritten": bool(raw.get("handwritten")),
        "lines": lines,
        "total": _amount(raw.get("total")),
    }


def _match(a_lines: list[ParsedLine], b_lines: list[ParsedLine]) -> list[tuple[ParsedLine | None, ParsedLine | None]]:
    """Pair lines by name similarity so a different line order between reads is not a disagreement.

    Pairs keep read A's order (index i+1 is read A's line number); lines only in read B come last.
    """
    remaining = list(b_lines)
    pairs: list[tuple[ParsedLine | None, ParsedLine | None]] = []
    for la in a_lines:
        best, best_score = None, 0.0
        for lb in remaining:
            score = _similar(la.name, lb.name)
            if score > best_score:
                best, best_score = lb, score
        if best is not None and best_score >= NAME_MATCH:
            remaining.remove(best)
            pairs.append((la, best))
        else:
            pairs.append((la, None))
    pairs.extend((None, lb) for lb in remaining)
    return pairs


def arithmetic_issues(lines: list[ParsedLine], total: Decimal | None) -> list[str]:
    """Check qty × unit_price = line_total per line and Σ line_total = total, within ₹1."""
    issues: list[str] = []
    line_sum = Decimal("0")
    sum_known = True
    for i, ln in enumerate(lines, start=1):
        if ln.line_total is None:
            sum_known = False
        else:
            line_sum += ln.line_total
        if ln.qty is not None and ln.unit_price is not None and ln.line_total is not None:
            expected = ln.qty * ln.unit_price
            if abs(expected - ln.line_total) > TOLERANCE:
                issues.append(
                    f"arith: line {i} {ln.name} {_fmt(ln.qty)}×{_fmt(ln.unit_price)}={_fmt(expected)} "
                    f"but bill says {_fmt(ln.line_total)}"
                )
    if total is not None and sum_known and lines and abs(line_sum - total) > TOLERANCE:
        issues.append(f"arith: lines sum to {_fmt(line_sum)} but bill total says {_fmt(total)}")
    return issues


def _to_model(ln: ParsedLine, unit: str = "") -> InvoiceLine:
    # The interface fields are not optional; an unreadable value becomes 0 and is always listed in
    # `disagreements`, so a result carrying a placeholder never has agreed=True.
    return InvoiceLine(
        name=ln.name,
        qty=float(ln.qty) if ln.qty is not None else 0.0,
        unit=unit or ln.unit,
        unit_price=rupees(ln.unit_price) or 0,
        line_total=rupees(ln.line_total) or 0,
    )


def merge_reads(raw_a: dict, raw_b: dict) -> InvoiceRead:
    """Merge read A and read B. Values come from read A; any doubt lands in `disagreements`."""
    a, b = _parse(raw_a), _parse(raw_b)
    dis: list[str] = []

    if _norm_text(a["supplier"]) != _norm_text(b["supplier"]) and \
            _similar(a["supplier"] or "", b["supplier"] or "") < SUPPLIER_MATCH:
        dis.append(f"supplier: {a['supplier']!r} vs {b['supplier']!r}")
    if (a["date"] or "") != (b["date"] or ""):
        dis.append(f"date: {a['date']!r} vs {b['date']!r}")

    if not a["lines"] and not b["lines"]:
        dis.append("lines: none read")

    out_lines: list[InvoiceLine] = []
    for i, (la, lb) in enumerate(_match(a["lines"], b["lines"]), start=1):
        if la is None or lb is None:
            only = la or lb
            dis.append(f"line {only.name}: only in one read")
            if la is not None:
                out_lines.append(_to_model(la))
            continue
        for field in FIELDS:
            va, vb = getattr(la, field), getattr(lb, field)
            if va is None or vb is None:
                dis.append(f"line {i} {la.name} {field}: unreadable")
            elif va != vb:
                dis.append(f"line {i} {la.name} {field}: {_fmt(va)} vs {_fmt(vb)}")
        # A unit seen by only one read is not a conflict; two different units are (30 L is not 30 packs).
        if la.unit and lb.unit and la.unit != lb.unit:
            dis.append(f"line {i} {la.name} unit: {la.unit} vs {lb.unit}")
        out_lines.append(_to_model(la, unit=la.unit or lb.unit))

    if a["total"] is None and b["total"] is None:
        dis.append("total: unreadable")
    elif a["total"] != b["total"]:
        dis.append(f"total: {_fmt(a['total'])} vs {_fmt(b['total'])}")
    dis.extend(arithmetic_issues(a["lines"], a["total"]))

    if a["handwritten"] or b["handwritten"]:
        dis.append("handwritten: confirm each line")
        for i, ln in enumerate(a["lines"], start=1):
            dis.append(f"line {i} {ln.name}: {_fmt(ln.qty)} {ln.unit} at {_fmt(ln.unit_price)} = {_fmt(ln.line_total)}")

    return InvoiceRead(
        supplier=a["supplier"],
        date=a["date"],
        lines=out_lines,
        total=rupees(a["total"]),
        agreed=not dis,
        disagreements=dis,
    )
