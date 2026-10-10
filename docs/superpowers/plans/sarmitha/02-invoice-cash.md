# AIRA — Invoice & Cash Reading Implementation Plan (Sarmitha · 02)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Murugan holds up the vendor's paper bill or a handful of cash, and AIRA reads it twice, independently. Code checks that the two reads agree and that the arithmetic adds up. AIRA then returns a trustworthy result (`InvoiceRead` / `CashRead`) that lists exactly which fields to read back when it is unsure.

**Architecture:** Two independent Gemini reads per image:

- **Read A:** `model_for("read")` on the original frame.
- **Read B:** `model_for("verify")` on an enhanced copy, with a different prompt and output shape.

Both go through `aira_live.vision.gemini.generate_json` (from `sarmitha/01`). Pure, offline-tested modules then normalise numbers (₹/Rs, Indian commas, Tamil/Devanagari digits), match lines, compare field by field and check `qty × unit_price = line_total` and `Σ lines = total`. **No arithmetic or verdict ever comes from the model.** Kanish's tools (`invoice_check`, `supplier_pay`, `sale_pay`) consume these results.

**Tech Stack:** Python 3.12, pydantic v2, Pillow (image enhancement), `decimal`, `difflib`, pytest + pytest-asyncio (asyncio_mode=auto from the foundation `pyproject.toml`), uv.

**Spec:** `docs/superpowers/specs/2026-10-10-aira-design.md` (§2 steps 3, 4, 7; §3 principles 2, 3, 6; §6 Invoice; §7 benchmarks). **Interfaces:** `docs/superpowers/plans/2026-10-10-00-interfaces.md` (Vision section).

**Prereqs:**

- The foundation is merged: `aira_live.frames.Frame`, `aira_live.models.model_for`, and the `services/live` uv project.
- `sarmitha/01-vision-er2.md` Task 1 provides `aira_live/vision/gemini.py` (`generate_json`, `image_part`). If it isn't merged yet, tests here monkeypatch both functions, so you can still proceed.

## Global Constraints

- `read_invoice(frames: list[Frame]) -> InvoiceRead` and `read_cash(frames: list[Frame]) -> CashRead` keep the **exact** interface signatures and field names:
  - `InvoiceLine(name, qty, unit, unit_price, line_total)`
  - `InvoiceRead(supplier, date, lines, total, agreed, disagreements)`
  - `CashRead(notes, total, agreed, confidence)`
- **One additive field:** `CashRead.hint_key: str = ""`. It is backward compatible; see the cross-team note in Task 4.
- Model IDs are never written here. Use `generate_json("read", …)` and `generate_json("verify", …)` only.
- Money is in **INR**. `unit_price`, `line_total` and `total` are **int rupees** (rounded half-up). Comparisons use `Decimal` with a ₹1 tolerance.
- Valid cash: notes **10, 20, 50, 100, 200, 500**; coins **1, 2, 5, 10, 20**. Anything else (e.g. 2000, 1000) is **unsupported**: never counted, and flagged.
- **No counterfeit or genuineness claims, anywhere** (spec §3.6).
- Any read failure, disagreement or arithmetic problem means `agreed=False` with a precise entry in `disagreements` or `hint_key`. Never raise to the caller for model errors.
- Tests run fully offline (fake `generate_json` / `image_part`). Live runs need `GEMINI_API_KEY` and are only for the bench scorers.
- All commands run from `services/live`.

## Review Focus

1. **Indian number formats:** `"₹1,00,000"`, `"Rs. 45.50/-"`, Tamil `"௩௦"` and Devanagari `"३२०"` must normalise to 100000, 45.50, 30 and 320. Tested in Task 1 (`test_normalize_amount_indian_formats`).
2. **Lines in a different order** between Read A and Read B must still match, with no false disagreement. Tested in Task 2 (`test_lines_in_different_order_still_agree`).
3. **A bill whose own arithmetic is wrong** must be flagged even when both reads agree, e.g. 30 × ₹33 printed as ₹1,000. Tested in Task 2 (`test_arithmetic_error_flagged_even_when_reads_agree`).
4. **One model call times out or errors:** `agreed=False`, the reason is recorded, and no exception escapes. Tested in Task 3 (`test_second_read_failure_never_agrees`) and Task 4 (`test_read_cash_one_read_fails`).
5. **Overlapping notes or an unsupported note** (₹2000): never counted as agreed, `total=None`, and `hint_key` is `cash.spread_notes` / `cash.unsupported_note`. Tested in Task 4 (`test_overlapping_notes_ask_to_spread`, `test_unsupported_denomination_flagged`).

---

## File Structure

```
services/live/aira_live/vision/
  numbers.py          # pure: Indic digits, ₹/Rs/commas, amounts, quantities + units, rupee rounding
  invoice_models.py   # InvoiceLine, InvoiceRead (re-exported by invoice.py, per interface)
  invoice_merge.py    # pure: parse a raw read, match lines, compare fields, arithmetic checks
  invoice.py          # read_invoice(): enhance image, two concurrent reads, merge
  cash_models.py      # CashRead (re-exported by cash.py)
  cash_merge.py       # pure: denomination counts from both reads, validity, agreement, hint
  cash.py             # read_cash(): two concurrent reads, merge
  imaging.py          # pure: enhance_for_reading(jpeg_b64) -> jpeg_b64 (grayscale, autocontrast, upscale, sharpen)
  metrics.py          # pure: invoice_metrics(), cash_metrics() for bench scorers
services/live/aira_live/i18n/{en,ta,hi}.json   # + cash.* keys (merged, not overwritten)
services/live/tests/
  test_vision_numbers.py  test_vision_invoice_merge.py  test_vision_invoice.py
  test_vision_cash.py     test_vision_metrics.py
bench/
  score_invoice.py  score_cash.py
  data/invoice/labels.example.json   data/cash/labels.example.json
```

---

### Task 1: Number and quantity normalisation (pure)

**Files:**
- Create: `services/live/aira_live/vision/numbers.py`
- Test: `services/live/tests/test_vision_numbers.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces:
  - `to_ascii_digits(s: str) -> str`
  - `normalize_amount(value: str | int | float | Decimal | None) -> Decimal | None`
  - `parse_qty(value: str | int | float | None, unit_hint: str = "") -> tuple[Decimal | None, str]`, where unit ∈ `{"L","ml","pcs","pack","box","kg","g",""}`
  - `rupees(d: Decimal | None) -> int | None` (round half-up)

- [ ] **Step 1: Write the failing test**

```python
# services/live/tests/test_vision_numbers.py
from decimal import Decimal
from aira_live.vision.numbers import to_ascii_digits, normalize_amount, parse_qty, rupees


def test_to_ascii_digits_tamil_and_devanagari():
    assert to_ascii_digits("௩௦ L") == "30 L"
    assert to_ascii_digits("३२०") == "320"


def test_normalize_amount_indian_formats():
    assert normalize_amount("₹1,00,000") == Decimal("100000")
    assert normalize_amount("Rs. 45.50/-") == Decimal("45.50")
    assert normalize_amount("INR 1,250") == Decimal("1250")
    assert normalize_amount("௩௦") == Decimal("30")
    assert normalize_amount("३२०") == Decimal("320")
    assert normalize_amount(990) == Decimal("990")
    assert normalize_amount(45.5) == Decimal("45.5")


def test_normalize_amount_rejects_garbage():
    assert normalize_amount(None) is None
    assert normalize_amount("") is None
    assert normalize_amount("total") is None
    assert normalize_amount(True) is None


def test_parse_qty_units():
    assert parse_qty("30 Ltr") == (Decimal("30"), "L")
    assert parse_qty("500ml") == (Decimal("500"), "ml")
    assert parse_qty("12", "pkt") == (Decimal("12"), "pack")
    assert parse_qty("௩௦ L") == (Decimal("30"), "L")
    assert parse_qty("2 boxes") == (Decimal("2"), "box")
    assert parse_qty(None, "L") == (None, "L")
    assert parse_qty("abc") == (None, "")


def test_rupees_rounds_half_up():
    assert rupees(Decimal("45.5")) == 46
    assert rupees(Decimal("45.49")) == 45
    assert rupees(None) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_vision_numbers.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.vision.numbers'`.

- [ ] **Step 3: Write the implementation**

```python
# services/live/aira_live/vision/numbers.py
"""Pure number/quantity normalisation for Indian bills and cash. No I/O, no model calls."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Tamil digits U+0BE6..U+0BEF and Devanagari digits U+0966..U+096F -> ASCII
_INDIC = {chr(0x0BE6 + i): str(i) for i in range(10)} | {chr(0x0966 + i): str(i) for i in range(10)}
_CURRENCY = re.compile(r"(₹|\brs\.?|\binr\b|/-)", re.IGNORECASE)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_QTY = re.compile(r"(-?\d+(?:\.\d+)?)\s*([a-z]+)?")
_UNITS = {
    "l": "L", "ltr": "L", "ltrs": "L", "lt": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L",
    "ml": "ml", "pcs": "pcs", "pc": "pcs", "nos": "pcs", "no": "pcs",
    "pack": "pack", "packs": "pack", "pkt": "pack", "pkts": "pack",
    "box": "box", "boxes": "box", "kg": "kg", "g": "g",
}


def to_ascii_digits(s: str) -> str:
    return "".join(_INDIC.get(ch, ch) for ch in s)


def normalize_amount(value: str | int | float | Decimal | None) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    s = _CURRENCY.sub("", to_ascii_digits(str(value))).replace(",", "").strip()
    m = _NUMBER.search(s)
    if not m:
        return None
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return None


def _unit(raw: str) -> str:
    raw = raw.lower().strip(".").strip()
    return _UNITS.get(raw, raw) if raw else ""


def parse_qty(value: str | int | float | None, unit_hint: str = "") -> tuple[Decimal | None, str]:
    hint = _unit(unit_hint)
    if value is None:
        return None, hint
    s = to_ascii_digits(str(value)).lower().replace(",", "")
    m = _QTY.search(s)
    if not m:
        return None, ""
    qty = Decimal(m.group(1))
    unit = _unit(m.group(2) or "") or hint
    return qty, unit


def rupees(d: Decimal | None) -> int | None:
    if d is None:
        return None
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd services/live && uv run pytest -q tests/test_vision_numbers.py`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/live/aira_live/vision/numbers.py services/live/tests/test_vision_numbers.py
git commit -m "feat(vision): Indian amount/quantity normalisation (₹, lakh commas, Tamil/Devanagari digits)"
```

---

### Task 2: Invoice models and two-read merge with arithmetic checks (pure)

**Files:**
- Create: `services/live/aira_live/vision/invoice_models.py`, `services/live/aira_live/vision/invoice_merge.py`
- Test: `services/live/tests/test_vision_invoice_merge.py`

**Interfaces:**
- Consumes: `numbers.normalize_amount`, `numbers.parse_qty`, `numbers.rupees` (Task 1).
- Produces:
  - Models: `InvoiceLine(name: str, qty: float, unit: str, unit_price: int, line_total: int)` and `InvoiceRead(supplier: str | None, date: str | None, lines: list[InvoiceLine], total: int | None, agreed: bool, disagreements: list[str])`. Both are exactly as in the interfaces doc.
  - `merge_reads(raw_a: dict, raw_b: dict) -> InvoiceRead`
  - `arithmetic_issues(lines: list[ParsedLine], total: Decimal | None) -> list[str]`
- Raw read shape, which both prompts return (Task 3): `{"supplier": str|None, "date": str|None, "handwritten": bool, "lines": [{"name": str, "qty": str, "unit": str, "unit_price": str, "line_total": str}], "total": str|None}`.
- `disagreements` entries are human-readable, stable prefixes that Kanish's `invoice_check` reads back:
  - `"supplier: …"`, `"date: …"`
  - `"line {i} {name} {field}: A vs B"`, `"line {name}: only in one read"`
  - `"total: A vs B"`
  - `"arith: …"`
  - `"handwritten: confirm each line"`, followed by `"line {i} {name}: {qty} {unit} at {price} = {total}"`

- [ ] **Step 1: Write the failing test**

```python
# services/live/tests/test_vision_invoice_merge.py
from aira_live.vision.invoice_merge import merge_reads

CURD = {"name": "Sakthi Curd 1L", "qty": "30 L", "unit": "L", "unit_price": "₹33", "line_total": "₹990"}
MILK = {"name": "Aavin Milk 500ml", "qty": "20", "unit": "pack", "unit_price": "Rs. 24", "line_total": "480"}


def read(lines, total="₹1,470", supplier="Sakthi Dairy", date="2026-10-10", handwritten=False):
    return {"supplier": supplier, "date": date, "handwritten": handwritten, "lines": lines, "total": total}


def test_identical_reads_agree_and_convert_types():
    r = merge_reads(read([CURD, MILK]), read([CURD, MILK]))
    assert r.agreed is True
    assert r.disagreements == []
    assert r.total == 1470
    assert r.lines[0].name == "Sakthi Curd 1L"
    assert r.lines[0].qty == 30.0 and r.lines[0].unit == "L"
    assert r.lines[0].unit_price == 33 and r.lines[0].line_total == 990


def test_lines_in_different_order_still_agree():
    r = merge_reads(read([CURD, MILK]), read([MILK, CURD]))
    assert r.agreed is True, r.disagreements


def test_supplier_case_and_punctuation_ignored():
    r = merge_reads(read([CURD, MILK]), read([CURD, MILK], supplier="SAKTHI DAIRY."))
    assert r.agreed is True, r.disagreements


def test_qty_disagreement_is_reported_precisely():
    curd_b = dict(CURD, qty="32 L")
    r = merge_reads(read([CURD, MILK]), read([curd_b, MILK]))
    assert r.agreed is False
    assert any(d.startswith("line 1 Sakthi Curd 1L qty: 30 vs 32") for d in r.disagreements), r.disagreements


def test_arithmetic_error_flagged_even_when_reads_agree():
    bad = dict(CURD, line_total="₹1,000")  # 30 x 33 = 990, bill says 1000
    r = merge_reads(read([bad, MILK], total="₹1,480"), read([bad, MILK], total="₹1,480"))
    assert r.agreed is False
    assert any(d.startswith("arith: line 1") for d in r.disagreements), r.disagreements


def test_total_not_equal_to_sum_of_lines_flagged():
    r = merge_reads(read([CURD, MILK], total="₹1,570"), read([CURD, MILK], total="₹1,570"))
    assert r.agreed is False
    assert any(d.startswith("arith: lines sum to 1470") for d in r.disagreements), r.disagreements


def test_line_only_in_one_read():
    r = merge_reads(read([CURD, MILK]), read([CURD], total="₹1,470"))
    assert r.agreed is False
    assert any("only in one read" in d for d in r.disagreements)


def test_handwritten_bill_reads_back_every_line():
    r = merge_reads(read([CURD, MILK], handwritten=True), read([CURD, MILK]))
    assert r.agreed is False
    assert "handwritten: confirm each line" in r.disagreements
    assert any(d.startswith("line 2 Aavin Milk 500ml: 20 pack at 24 = 480") for d in r.disagreements)


def test_unreadable_field_is_a_disagreement():
    blank = dict(CURD, unit_price="")
    r = merge_reads(read([blank, MILK]), read([blank, MILK]))
    assert r.agreed is False
    assert any("unit_price: unreadable" in d for d in r.disagreements)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_vision_invoice_merge.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.vision.invoice_merge'`.

- [ ] **Step 3: Write the models**

```python
# services/live/aira_live/vision/invoice_models.py
from pydantic import BaseModel


class InvoiceLine(BaseModel):
    name: str
    qty: float
    unit: str
    unit_price: int
    line_total: int


class InvoiceRead(BaseModel):
    supplier: str | None
    date: str | None
    lines: list[InvoiceLine]
    total: int | None
    agreed: bool
    disagreements: list[str]
```

- [ ] **Step 4: Write the merge logic**

```python
# services/live/aira_live/vision/invoice_merge.py
"""Pure: compare two independent invoice reads field by field and check the bill's own arithmetic."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher

from aira_live.vision.invoice_models import InvoiceLine, InvoiceRead
from aira_live.vision.numbers import normalize_amount, parse_qty, rupees

TOLERANCE = Decimal("1")      # ₹1 rounding tolerance
NAME_MATCH = 0.75             # minimum similarity to pair lines across reads
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


def _parse(raw: dict) -> dict:
    lines = []
    for item in raw.get("lines") or []:
        qty, unit = parse_qty(item.get("qty"), item.get("unit") or "")
        lines.append(ParsedLine(
            name=str(item.get("name") or "").strip(),
            qty=qty,
            unit=unit,
            unit_price=normalize_amount(item.get("unit_price")),
            line_total=normalize_amount(item.get("line_total")),
        ))
    return {
        "supplier": (raw.get("supplier") or None),
        "date": (str(raw["date"]).strip() if raw.get("date") else None),
        "handwritten": bool(raw.get("handwritten")),
        "lines": lines,
        "total": normalize_amount(raw.get("total")),
    }


def _match(a_lines: list[ParsedLine], b_lines: list[ParsedLine]) -> list[tuple[ParsedLine | None, ParsedLine | None]]:
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


def _to_model(ln: ParsedLine) -> InvoiceLine:
    return InvoiceLine(
        name=ln.name,
        qty=float(ln.qty) if ln.qty is not None else 0.0,
        unit=ln.unit,
        unit_price=rupees(ln.unit_price) or 0,
        line_total=rupees(ln.line_total) or 0,
    )


def merge_reads(raw_a: dict, raw_b: dict) -> InvoiceRead:
    a, b = _parse(raw_a), _parse(raw_b)
    dis: list[str] = []

    if _norm_text(a["supplier"]) != _norm_text(b["supplier"]) and _similar(a["supplier"] or "", b["supplier"] or "") < 0.85:
        dis.append(f"supplier: {a['supplier']!r} vs {b['supplier']!r}")
    if (a["date"] or "") != (b["date"] or ""):
        dis.append(f"date: {a['date']!r} vs {b['date']!r}")

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
        out_lines.append(_to_model(la))

    if a["total"] != b["total"]:
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd services/live && uv run pytest -q tests/test_vision_invoice_merge.py`
Expected: `9 passed`.

- [ ] **Step 6: Commit**

```bash
git add services/live/aira_live/vision/invoice_models.py services/live/aira_live/vision/invoice_merge.py services/live/tests/test_vision_invoice_merge.py
git commit -m "feat(vision): invoice two-read merge with field agreement and bill arithmetic checks"
```

---

### Task 3: `read_invoice()`: image enhancement and two concurrent independent reads

**Files:**
- Create: `services/live/aira_live/vision/imaging.py`, `services/live/aira_live/vision/invoice.py`
- Modify: `services/live/pyproject.toml` (add `pillow` if `sarmitha/01` hasn't already)
- Test: `services/live/tests/test_vision_invoice.py`

**Interfaces:**
- Consumes:
  - `aira_live.frames.Frame(id, jpeg_b64, w, h, ts, purpose)` (foundation)
  - `aira_live.vision.gemini.generate_json(role, parts, schema_hint, thinking="low", timeout_s=6.0) -> dict` and `image_part(jpeg_b64) -> types.Part` (`sarmitha/01`)
  - `merge_reads` (Task 2)
- Produces:
  - `aira_live.vision.imaging.enhance_for_reading(jpeg_b64: str) -> str`
  - `aira_live.vision.invoice.read_invoice(frames: list[Frame]) -> InvoiceRead`
  - re-exports `InvoiceLine` and `InvoiceRead`
  - constants `INVOICE_SCHEMA_HINT`, `PROMPT_A`, `PROMPT_B`

- [ ] **Step 1: Add Pillow (skip if already present)**

Run: `cd services/live && (uv run python -c "import PIL" 2>/dev/null || uv add pillow)`
Expected: either no output (already installed) or `Resolved … Installed pillow`.

- [ ] **Step 2: Write the failing test**

```python
# services/live/tests/test_vision_invoice.py
import base64
import io

from PIL import Image

from aira_live.frames import Frame
from aira_live.vision import invoice
from aira_live.vision.imaging import enhance_for_reading

LINES = [{"name": "Sakthi Curd 1L", "qty": "30 L", "unit": "L", "unit_price": "33", "line_total": "990"}]
RAW = {"supplier": "Sakthi Dairy", "date": "2026-10-10", "handwritten": False, "lines": LINES, "total": "990"}


def jpeg_b64(w=320, h=200) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (240, 230, 220)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def frame(w=320, h=200) -> Frame:
    return Frame(id="f1", jpeg_b64=jpeg_b64(w, h), w=w, h=h, ts=1.0, purpose="invoice")


def patch(monkeypatch, read_a, read_b, fail_b=False):
    calls = []

    async def fake_generate_json(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        calls.append({"role": role, "image": parts[1], "schema": schema_hint})
        if role == "read":
            return read_a
        if fail_b:
            raise TimeoutError("verify timed out")
        return read_b

    monkeypatch.setattr(invoice, "generate_json", fake_generate_json)
    monkeypatch.setattr(invoice, "image_part", lambda b64: b64)
    return calls


def test_enhance_for_reading_returns_larger_grayscale_jpeg():
    out = enhance_for_reading(jpeg_b64(320, 200))
    img = Image.open(io.BytesIO(base64.b64decode(out)))
    assert img.mode == "L"
    assert img.width >= 480


async def test_two_independent_reads_agree(monkeypatch):
    calls = patch(monkeypatch, RAW, RAW)
    r = await invoice.read_invoice([frame()])
    assert r.agreed is True and r.total == 990
    assert sorted(c["role"] for c in calls) == ["read", "verify"]
    images = {c["role"]: c["image"] for c in calls}
    assert images["read"] != images["verify"], "read B must use the enhanced image"


async def test_second_read_failure_never_agrees(monkeypatch):
    patch(monkeypatch, RAW, None, fail_b=True)
    r = await invoice.read_invoice([frame()])
    assert r.agreed is False
    assert r.disagreements[0].startswith("second read failed (TimeoutError)")
    assert r.total == 990  # read A values still returned for read-back


async def test_both_reads_fail(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(invoice, "generate_json", boom)
    monkeypatch.setattr(invoice, "image_part", lambda b64: b64)
    r = await invoice.read_invoice([frame()])
    assert r.agreed is False and r.lines == [] and r.total is None
    assert r.disagreements == ["could not read the bill: hold it flat and retake"]


async def test_no_frames():
    r = await invoice.read_invoice([])
    assert r.agreed is False
    assert r.disagreements == ["no image: hold the bill in front of the camera"]


async def test_uses_largest_frame(monkeypatch):
    calls = patch(monkeypatch, RAW, RAW)
    small, big = frame(160, 100), frame(640, 400)
    await invoice.read_invoice([small, big])
    read_a = next(c for c in calls if c["role"] == "read")
    assert read_a["image"] == big.jpeg_b64
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_vision_invoice.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.vision.imaging'`.

- [ ] **Step 4: Write the image enhancement**

```python
# services/live/aira_live/vision/imaging.py
"""Pure image helpers. Read B runs on an enhanced copy so the two reads are genuinely independent."""
import base64
import io

from PIL import Image, ImageFilter, ImageOps

MIN_WIDTH = 1200


def enhance_for_reading(jpeg_b64: str) -> str:
    img = Image.open(io.BytesIO(base64.b64decode(jpeg_b64)))
    img = ImageOps.exif_transpose(img).convert("L")
    img = ImageOps.autocontrast(img, cutoff=1)
    if img.width < MIN_WIDTH:
        scale = max(1.5, MIN_WIDTH / img.width) if img.width < MIN_WIDTH / 1.5 else 1.5
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    img = img.filter(ImageFilter.SHARPEN)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()
```

- [ ] **Step 5: Write `read_invoice`**

```python
# services/live/aira_live/vision/invoice.py
"""Read a supplier bill twice, independently, and let code decide whether to trust it."""
from __future__ import annotations

import asyncio

from aira_live.frames import Frame
from aira_live.vision.gemini import generate_json, image_part
from aira_live.vision.imaging import enhance_for_reading
from aira_live.vision.invoice_merge import merge_reads
from aira_live.vision.invoice_models import InvoiceLine, InvoiceRead

__all__ = ["InvoiceLine", "InvoiceRead", "read_invoice"]

INVOICE_SCHEMA_HINT = (
    '{"supplier": string|null, "date": "YYYY-MM-DD"|null, "handwritten": boolean, '
    '"lines": [{"name": string, "qty": string, "unit": string, "unit_price": string, "line_total": string}], '
    '"total": string|null}'
)
PROMPT_A = (
    "You are reading a supplier invoice handed to a small Indian dairy shop (products like Aavin milk, "
    "Sakthi curd, Arun ice cream). Extract exactly what is printed or written. Copy every number as written, "
    "including mistakes; never compute, round or correct. Use null for anything you cannot read. "
    "Set handwritten=true if the quantities or prices are handwritten. Return JSON only: " + INVOICE_SCHEMA_HINT
)
PROMPT_B = (
    "Transcribe this bill as a table, row by row from top to bottom. For each item row give the product name, "
    "the quantity with its unit, the price per unit and the row amount, digit for digit as they appear "
    "(Tamil or Hindi digits are fine). Also give the shop/supplier name at the top, the bill date and the grand "
    "total at the bottom. Do not fix arithmetic. If a value is unclear write null. "
    "Say whether the figures are handwritten. Return JSON only: " + INVOICE_SCHEMA_HINT
)


def _best(frames: list[Frame]) -> Frame:
    return max(frames, key=lambda f: (f.w * f.h, f.ts))


async def _safe(coro) -> tuple[dict | None, str | None]:
    try:
        return await coro, None
    except Exception as exc:  # model/network errors become a disagreement, never an exception
        return None, type(exc).__name__


async def read_invoice(frames: list[Frame]) -> InvoiceRead:
    if not frames:
        return InvoiceRead(supplier=None, date=None, lines=[], total=None, agreed=False,
                           disagreements=["no image: hold the bill in front of the camera"])
    f = _best(frames)
    enhanced = enhance_for_reading(f.jpeg_b64)
    (raw_a, err_a), (raw_b, err_b) = await asyncio.gather(
        _safe(generate_json("read", [PROMPT_A, image_part(f.jpeg_b64)], INVOICE_SCHEMA_HINT,
                            thinking="medium", timeout_s=8.0)),
        _safe(generate_json("verify", [PROMPT_B, image_part(enhanced)], INVOICE_SCHEMA_HINT,
                            thinking="medium", timeout_s=8.0)),
    )
    if raw_a is None and raw_b is None:
        return InvoiceRead(supplier=None, date=None, lines=[], total=None, agreed=False,
                           disagreements=["could not read the bill: hold it flat and retake"])
    if raw_a is None or raw_b is None:
        only = raw_a if raw_a is not None else raw_b
        single = merge_reads(only, only)
        reason = f"second read failed ({err_a or err_b}): confirm every line"
        return single.model_copy(update={"agreed": False, "disagreements": [reason, *single.disagreements]})
    return merge_reads(raw_a, raw_b)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd services/live && uv run pytest -q tests/test_vision_invoice.py tests/test_vision_invoice_merge.py`
Expected: `15 passed`.

- [ ] **Step 7: Commit**

```bash
git add services/live/pyproject.toml services/live/uv.lock services/live/aira_live/vision/imaging.py services/live/aira_live/vision/invoice.py services/live/tests/test_vision_invoice.py
git commit -m "feat(vision): read_invoice with two independent reads (original + enhanced) and safe failure"
```

---

### Task 4: Cash reading: two reads, valid INR denominations, totals in code, spread-the-notes hint

**Files:**
- Create: `services/live/aira_live/vision/cash_models.py`, `services/live/aira_live/vision/cash_merge.py`, `services/live/aira_live/vision/cash.py`
- Modify: `services/live/aira_live/i18n/en.json`, `ta.json`, `hi.json` (merge in the `cash.*` keys)
- Test: `services/live/tests/test_vision_cash.py`

**Interfaces:**
- Consumes: `Frame`, `generate_json`, `image_part` (as in Task 3).
- Produces:
  - `CashRead(notes: list[dict], total: int | None, agreed: bool, confidence: float, hint_key: str = "")`, where `notes` is `[{"denomination": int, "count": int}]`, highest denomination first.
  - `merge_cash(raw_a: dict, raw_b: dict) -> CashRead`
  - `read_cash(frames: list[Frame]) -> CashRead`
  - constants `VALID_DENOMINATIONS = {1, 2, 5, 10, 20, 50, 100, 200, 500}`
  - i18n keys `cash.spread_notes`, `cash.unsupported_note`, `cash.show_again`
- Raw shapes:
  - **Read A** (aggregated): `{"items": [{"denomination": int, "count": int, "kind": "note"|"coin"}], "overlapping": bool, "confidence": float}`
  - **Read B** (one entry per piece, a different structure on purpose): `{"pieces": [int, ...], "overlapping": bool, "confidence": float}`

> **Cross-team note (Kanish, Saravana):** `CashRead.hint_key` is a new, additive field with default `""`, so existing callers are unaffected. Kanish's `supplier_pay` / `sale_pay` speak `i18n.t(result.hint_key, lang)` whenever `hint_key` is non-empty. Saravana: please add `hint_key: str = ""` to the `CashRead` line in `2026-10-10-00-interfaces.md` in the same PR.

- [ ] **Step 1: Write the failing test**

```python
# services/live/tests/test_vision_cash.py
import base64
import io

from PIL import Image

from aira_live.frames import Frame
from aira_live.vision import cash
from aira_live.vision.cash_merge import merge_cash

A_1000 = {"items": [{"denomination": 500, "count": 2, "kind": "note"}], "overlapping": False, "confidence": 0.9}
B_1000 = {"pieces": [500, 500], "overlapping": False, "confidence": 0.85}


def test_agreeing_reads_total_in_code():
    r = merge_cash(A_1000, B_1000)
    assert r.agreed is True
    assert r.total == 1000
    assert r.notes == [{"denomination": 500, "count": 2}]
    assert r.confidence == 0.85
    assert r.hint_key == ""


def test_mixed_notes_and_coins_sorted_desc():
    a = {"items": [{"denomination": 100, "count": 1, "kind": "note"}, {"denomination": 10, "count": 2, "kind": "coin"},
                   {"denomination": 50, "count": 1, "kind": "note"}], "overlapping": False, "confidence": 0.8}
    b = {"pieces": [10, 100, 50, 10], "overlapping": False, "confidence": 0.8}
    r = merge_cash(a, b)
    assert r.agreed is True and r.total == 170
    assert [n["denomination"] for n in r.notes] == [100, 50, 10]


def test_disagreement_means_no_total():
    b = {"pieces": [500, 100], "overlapping": False, "confidence": 0.9}
    r = merge_cash(A_1000, b)
    assert r.agreed is False and r.total is None
    assert r.hint_key == "cash.show_again"
    assert r.confidence < 0.5


def test_overlapping_notes_ask_to_spread():
    b = dict(B_1000, overlapping=True)
    r = merge_cash(A_1000, b)
    assert r.agreed is False and r.total is None
    assert r.hint_key == "cash.spread_notes"


def test_unsupported_denomination_flagged():
    a = {"items": [{"denomination": 2000, "count": 1, "kind": "note"}], "overlapping": False, "confidence": 0.9}
    b = {"pieces": [2000], "overlapping": False, "confidence": 0.9}
    r = merge_cash(a, b)
    assert r.agreed is False and r.total is None
    assert r.hint_key == "cash.unsupported_note"
    assert r.notes == []


def test_nothing_seen():
    r = merge_cash({"items": [], "overlapping": False, "confidence": 0.2}, {"pieces": [], "confidence": 0.2})
    assert r.agreed is False and r.hint_key == "cash.show_again"


def _frame() -> Frame:
    buf = io.BytesIO()
    Image.new("RGB", (320, 200), (200, 200, 200)).save(buf, format="JPEG")
    return Frame(id="c1", jpeg_b64=base64.b64encode(buf.getvalue()).decode(), w=320, h=200, ts=1.0, purpose="cash")


async def test_read_cash_two_roles(monkeypatch):
    roles = []

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        roles.append(role)
        return A_1000 if role == "read" else B_1000

    monkeypatch.setattr(cash, "generate_json", fake)
    monkeypatch.setattr(cash, "image_part", lambda b64: b64)
    r = await cash.read_cash([_frame()])
    assert r.agreed is True and r.total == 1000
    assert sorted(roles) == ["read", "verify"]


async def test_read_cash_one_read_fails(monkeypatch):
    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        if role == "verify":
            raise TimeoutError("slow")
        return A_1000

    monkeypatch.setattr(cash, "generate_json", fake)
    monkeypatch.setattr(cash, "image_part", lambda b64: b64)
    r = await cash.read_cash([_frame()])
    assert r.agreed is False and r.total is None
    assert r.hint_key == "cash.show_again"


async def test_read_cash_no_frames():
    r = await cash.read_cash([])
    assert r.agreed is False and r.hint_key == "cash.show_again"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_vision_cash.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.vision.cash_merge'`.

- [ ] **Step 3: Write the models and the merge**

```python
# services/live/aira_live/vision/cash_models.py
from pydantic import BaseModel


class CashRead(BaseModel):
    notes: list[dict]          # [{"denomination": int, "count": int}], highest first
    total: int | None          # computed in code; None unless both reads agree
    agreed: bool
    confidence: float
    hint_key: str = ""         # i18n key to speak when not agreed (additive field)
```

```python
# services/live/aira_live/vision/cash_merge.py
"""Pure: agree two independent cash reads. Totals are computed here, never by the model.
No counterfeit/genuineness judgement is made anywhere."""
from __future__ import annotations

from collections import Counter

from aira_live.vision.cash_models import CashRead

NOTES = {10, 20, 50, 100, 200, 500}
COINS = {1, 2, 5, 10, 20}
VALID_DENOMINATIONS = NOTES | COINS


def _counts_a(raw: dict) -> tuple[Counter, bool]:
    counts: Counter = Counter()
    invalid = False
    for item in raw.get("items") or []:
        try:
            d, n = int(item.get("denomination")), int(item.get("count"))
        except (TypeError, ValueError):
            invalid = True
            continue
        if d not in VALID_DENOMINATIONS or n < 0:
            invalid = True
            continue
        counts[d] += n
    return +counts, invalid


def _counts_b(raw: dict) -> tuple[Counter, bool]:
    counts: Counter = Counter()
    invalid = False
    for piece in raw.get("pieces") or []:
        try:
            d = int(piece)
        except (TypeError, ValueError):
            invalid = True
            continue
        if d not in VALID_DENOMINATIONS:
            invalid = True
            continue
        counts[d] += 1
    return counts, invalid


def merge_cash(raw_a: dict, raw_b: dict) -> CashRead:
    ca, bad_a = _counts_a(raw_a)
    cb, bad_b = _counts_b(raw_b)
    overlapping = bool(raw_a.get("overlapping")) or bool(raw_b.get("overlapping"))
    conf = min(float(raw_a.get("confidence") or 0.0), float(raw_b.get("confidence") or 0.0))
    agreed = bool(ca) and ca == cb and not (bad_a or bad_b) and not overlapping
    if overlapping:
        hint = "cash.spread_notes"
    elif bad_a or bad_b:
        hint = "cash.unsupported_note"
    elif not agreed:
        hint = "cash.show_again"
    else:
        hint = ""
    notes = [{"denomination": d, "count": n} for d, n in sorted(ca.items(), reverse=True)]
    total = sum(d * n for d, n in ca.items()) if agreed else None
    return CashRead(notes=notes, total=total, agreed=agreed,
                    confidence=round(conf if agreed else conf * 0.5, 3), hint_key=hint)
```

- [ ] **Step 4: Write `read_cash`**

```python
# services/live/aira_live/vision/cash.py
"""Read cash in Murugan's hand twice with different output shapes; code agrees and totals."""
from __future__ import annotations

import asyncio

from aira_live.frames import Frame
from aira_live.vision.cash_merge import VALID_DENOMINATIONS, merge_cash
from aira_live.vision.cash_models import CashRead
from aira_live.vision.gemini import generate_json, image_part

__all__ = ["CashRead", "read_cash", "VALID_DENOMINATIONS"]

SCHEMA_A = ('{"items": [{"denomination": integer, "count": integer, "kind": "note"|"coin"}], '
            '"overlapping": boolean, "confidence": number 0-1}')
SCHEMA_B = '{"pieces": [integer, ...], "overlapping": boolean, "confidence": number 0-1}'
PROMPT_A = (
    "Count the Indian rupee banknotes and coins visible in this photo. Group them by denomination. "
    "Indian notes: 10, 20, 50, 100, 200, 500, 2000. Coins: 1, 2, 5, 10, 20. Report every note even if it is "
    "a 2000 note. Set overlapping=true if any notes cover each other so a denomination is hidden. "
    "Do not judge whether money is genuine. Return JSON only: " + SCHEMA_A
)
PROMPT_B = (
    "List every individual piece of Indian money in this image, one entry per note or coin, giving only its "
    "face value in rupees. If two notes overlap so you cannot see one clearly, set overlapping=true. "
    "Never guess a hidden value and never comment on authenticity. Return JSON only: " + SCHEMA_B
)


async def _safe(coro) -> dict | None:
    try:
        return await coro
    except Exception:
        return None


async def read_cash(frames: list[Frame]) -> CashRead:
    if not frames:
        return CashRead(notes=[], total=None, agreed=False, confidence=0.0, hint_key="cash.show_again")
    f = max(frames, key=lambda fr: (fr.w * fr.h, fr.ts))
    raw_a, raw_b = await asyncio.gather(
        _safe(generate_json("read", [PROMPT_A, image_part(f.jpeg_b64)], SCHEMA_A, thinking="medium", timeout_s=6.0)),
        _safe(generate_json("verify", [PROMPT_B, image_part(f.jpeg_b64)], SCHEMA_B, thinking="medium", timeout_s=6.0)),
    )
    if raw_a is None or raw_b is None:
        partial = merge_cash(raw_a or {}, raw_b or {})
        return partial.model_copy(update={"agreed": False, "total": None, "hint_key": "cash.show_again"})
    return merge_cash(raw_a, raw_b)
```

- [ ] **Step 5: Merge the i18n keys (creates the files if the foundation hasn't yet)**

Run from `services/live`:

```bash
uv run python - <<'PY'
import json, pathlib
keys = {
  "en": {"cash.spread_notes": "Please spread the notes apart so none overlap.",
         "cash.unsupported_note": "I can't count one of these notes. Please check it by hand.",
         "cash.show_again": "I'm not sure. Please show the cash again."},
  "ta": {"cash.spread_notes": "நோட்டுகள் ஒன்றின் மேல் ஒன்று இல்லாமல் பிரித்து வையுங்கள்.",
         "cash.unsupported_note": "இந்த நோட்டுகளில் ஒன்றை என்னால் எண்ண முடியவில்லை. கையால் சரிபாருங்கள்.",
         "cash.show_again": "உறுதியாகத் தெரியவில்லை. பணத்தை மீண்டும் காட்டுங்கள்."},
  "hi": {"cash.spread_notes": "कृपया नोट अलग-अलग फैलाइए ताकि कोई एक-दूसरे पर न हो।",
         "cash.unsupported_note": "इनमें से एक नोट मैं नहीं गिन सकता। कृपया हाथ से जाँचें।",
         "cash.show_again": "मुझे पक्का नहीं पता। कृपया पैसे फिर से दिखाइए।"},
}
d = pathlib.Path("aira_live/i18n"); d.mkdir(parents=True, exist_ok=True)
for lang, kv in keys.items():
    p = d / f"{lang}.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    data.update(kv)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("ok")
PY
```

Expected: `ok`. Existing keys are kept, and the three `cash.*` keys exist in every language.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest -q tests/test_vision_cash.py`
Expected: `9 passed`.

- [ ] **Step 7: Commit**

```bash
git add services/live/aira_live/vision/cash_models.py services/live/aira_live/vision/cash_merge.py services/live/aira_live/vision/cash.py services/live/aira_live/i18n services/live/tests/test_vision_cash.py
git commit -m "feat(vision): read_cash with two reads, INR denominations, totals in code, spread-notes hint"
```

---

### Task 5: Benchmark metrics and scorers (`bench/score_invoice.py`, `bench/score_cash.py`)

**Files:**
- Create: `services/live/aira_live/vision/metrics.py`, `bench/score_invoice.py`, `bench/score_cash.py`, `bench/data/invoice/labels.example.json`, `bench/data/cash/labels.example.json`
- Test: `services/live/tests/test_vision_metrics.py`

**Interfaces:**
- Consumes: `read_invoice`, `read_cash` (Tasks 3–4), `Frame`.
- Produces:
  - `invoice_metrics(labels: list[dict], preds: dict[str, dict]) -> dict`, with keys `n`, `missing`, `catch_rate`, `false_alarm_rate`, `clean_total_accuracy`
  - `cash_metrics(labels: list[dict], preds: dict[str, dict]) -> dict`, with keys `n`, `missing`, `total_accuracy`, `abstain_rate`, `wrong_when_confident`
  - CLI JSON results that Ishwarya's bench runner collects for the Benchmarking slide

**Label formats:**
- `bench/data/invoice/labels.json`: `[{"file": "inv_001.jpg", "supplier": "Sakthi Dairy", "total": 1470, "has_error": false, "error_fields": [], "lines": [{"name": "...", "qty": 30, "unit": "L", "unit_price": 33, "line_total": 990}]}]`
  - `has_error=true` marks bills you deliberately printed or hand-wrote with a wrong line total, a wrong grand total, or a quantity that disagrees with the delivery.
  - `error_fields` describes them, e.g. `["line 1 line_total"]`.
- `bench/data/cash/labels.json`: `[{"file": "cash_001.jpg", "notes": [{"denomination": 500, "count": 2}], "total": 1000, "overlapping": false}]`

**Data to collect** (images live next to `labels.json`, under 1 MB each, and contain no faces):
- **30 invoices:** 20 clean (printed and handwritten, real Sakthi/Aavin-style formats) and 10 with errors.
- **40 cash photos:** single notes, mixed notes + coins, low light, and 8 with overlapping notes.

- [ ] **Step 1: Write the failing test**

```python
# services/live/tests/test_vision_metrics.py
from aira_live.vision.metrics import cash_metrics, invoice_metrics

INV_LABELS = [
    {"file": "a.jpg", "total": 990, "has_error": False},
    {"file": "b.jpg", "total": 1470, "has_error": False},
    {"file": "c.jpg", "total": 1000, "has_error": True},
    {"file": "d.jpg", "total": 500, "has_error": True},
]


def test_invoice_metrics():
    preds = {
        "a.jpg": {"agreed": True, "total": 990, "disagreements": []},
        "b.jpg": {"agreed": False, "total": 1470, "disagreements": ["date: x vs y"]},   # false alarm
        "c.jpg": {"agreed": False, "total": 1000, "disagreements": ["arith: line 1"]},  # caught
        "d.jpg": {"agreed": True, "total": 500, "disagreements": []},                    # missed
    }
    m = invoice_metrics(INV_LABELS, preds)
    assert m == {"n": 4, "missing": 0, "catch_rate": 0.5, "false_alarm_rate": 0.5, "clean_total_accuracy": 1.0}


def test_invoice_metrics_missing_prediction_counts_as_not_flagged():
    m = invoice_metrics(INV_LABELS, {"a.jpg": {"agreed": True, "total": 990, "disagreements": []}})
    assert m["missing"] == 3
    assert m["catch_rate"] == 0.0


CASH_LABELS = [{"file": "x.jpg", "total": 1000}, {"file": "y.jpg", "total": 170},
               {"file": "z.jpg", "total": 600}, {"file": "w.jpg", "total": 50}]


def test_cash_metrics():
    preds = {
        "x.jpg": {"agreed": True, "total": 1000},   # right
        "y.jpg": {"agreed": True, "total": 160},    # wrong but confident
        "z.jpg": {"agreed": False, "total": None},  # abstained
        "w.jpg": {"agreed": True, "total": 50},     # right
    }
    m = cash_metrics(CASH_LABELS, preds)
    assert m == {"n": 4, "missing": 0, "total_accuracy": 0.5, "abstain_rate": 0.25, "wrong_when_confident": 0.25}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/live && uv run pytest -q tests/test_vision_metrics.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.vision.metrics'`.

- [ ] **Step 3: Write the metrics**

```python
# services/live/aira_live/vision/metrics.py
"""Pure benchmark metrics for invoice and cash reading (Benchmarking slide)."""


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def invoice_metrics(labels: list[dict], preds: dict[str, dict]) -> dict:
    missing = sum(1 for l in labels if l["file"] not in preds)

    def flagged(l: dict) -> bool:
        p = preds.get(l["file"])
        return p is not None and not p.get("agreed", False)

    errs = [l for l in labels if l.get("has_error")]
    clean = [l for l in labels if not l.get("has_error")]
    clean_agreed = [l for l in clean if l["file"] in preds and preds[l["file"]].get("agreed")]
    return {
        "n": len(labels),
        "missing": missing,
        "catch_rate": _rate(sum(flagged(l) for l in errs), len(errs)),
        "false_alarm_rate": _rate(sum(flagged(l) for l in clean), len(clean)),
        "clean_total_accuracy": _rate(sum(preds[l["file"]].get("total") == l["total"] for l in clean_agreed),
                                      len(clean_agreed)),
    }


def cash_metrics(labels: list[dict], preds: dict[str, dict]) -> dict:
    present = [l for l in labels if l["file"] in preds]
    right = sum(1 for l in present if preds[l["file"]].get("agreed") and preds[l["file"]].get("total") == l["total"])
    abstain = sum(1 for l in present if not preds[l["file"]].get("agreed"))
    wrong = sum(1 for l in present if preds[l["file"]].get("agreed") and preds[l["file"]].get("total") != l["total"])
    n = len(labels)
    return {
        "n": n,
        "missing": n - len(present),
        "total_accuracy": _rate(right, n),
        "abstain_rate": _rate(abstain, n),
        "wrong_when_confident": _rate(wrong, n),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd services/live && uv run pytest -q tests/test_vision_metrics.py`
Expected: `3 passed`.

- [ ] **Step 5: Write the scorer CLIs and example labels**

```python
# bench/score_invoice.py
"""Invoice benchmark. Run from services/live:
  uv run python ../../bench/score_invoice.py --data ../../bench/data/invoice --out ../../bench/results/invoice.json
  (live model calls need GEMINI_API_KEY; or pass --predictions preds.json to re-score saved predictions)"""
import argparse
import asyncio
import base64
import io
import json
import time
from pathlib import Path

from PIL import Image

from aira_live.frames import Frame
from aira_live.vision.invoice import read_invoice
from aira_live.vision.metrics import invoice_metrics


async def predict(data_dir: Path, labels: list[dict]) -> tuple[dict, list[float]]:
    preds, latencies = {}, []
    for lab in labels:
        raw = (data_dir / lab["file"]).read_bytes()
        w, h = Image.open(io.BytesIO(raw)).size
        frame = Frame(id=lab["file"], jpeg_b64=base64.b64encode(raw).decode(), w=w, h=h, ts=time.time(), purpose="invoice")
        t0 = time.perf_counter()
        result = await read_invoice([frame])
        latencies.append(time.perf_counter() - t0)
        preds[lab["file"]] = result.model_dump()
    return preds, latencies


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--predictions", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    labels = json.loads((args.data / "labels.json").read_text())
    latencies: list[float] = []
    if args.predictions:
        preds = json.loads(args.predictions.read_text())
    else:
        preds, latencies = asyncio.run(predict(args.data, labels))
    metrics = invoice_metrics(labels, preds)
    if latencies:
        s = sorted(latencies)
        metrics["latency_p50_s"] = round(s[len(s) // 2], 3)
        metrics["latency_p95_s"] = round(s[min(len(s) - 1, int(len(s) * 0.95))], 3)
    print(json.dumps(metrics, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"metrics": metrics, "predictions": preds}, indent=2))


if __name__ == "__main__":
    main()
```

```python
# bench/score_cash.py
"""Cash benchmark. Run from services/live:
  uv run python ../../bench/score_cash.py --data ../../bench/data/cash --out ../../bench/results/cash.json"""
import argparse
import asyncio
import base64
import io
import json
import time
from pathlib import Path

from PIL import Image

from aira_live.frames import Frame
from aira_live.vision.cash import read_cash
from aira_live.vision.metrics import cash_metrics


async def predict(data_dir: Path, labels: list[dict]) -> tuple[dict, list[float]]:
    preds, latencies = {}, []
    for lab in labels:
        raw = (data_dir / lab["file"]).read_bytes()
        w, h = Image.open(io.BytesIO(raw)).size
        frame = Frame(id=lab["file"], jpeg_b64=base64.b64encode(raw).decode(), w=w, h=h, ts=time.time(), purpose="cash")
        t0 = time.perf_counter()
        result = await read_cash([frame])
        latencies.append(time.perf_counter() - t0)
        preds[lab["file"]] = result.model_dump()
    return preds, latencies


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--predictions", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    labels = json.loads((args.data / "labels.json").read_text())
    latencies: list[float] = []
    if args.predictions:
        preds = json.loads(args.predictions.read_text())
    else:
        preds, latencies = asyncio.run(predict(args.data, labels))
    metrics = cash_metrics(labels, preds)
    if latencies:
        s = sorted(latencies)
        metrics["latency_p50_s"] = round(s[len(s) // 2], 3)
        metrics["latency_p95_s"] = round(s[min(len(s) - 1, int(len(s) * 0.95))], 3)
    print(json.dumps(metrics, indent=2))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"metrics": metrics, "predictions": preds}, indent=2))


if __name__ == "__main__":
    main()
```

```json
// bench/data/invoice/labels.example.json  (copy to labels.json and replace with your 30 real entries)
[
  {"file": "inv_001.jpg", "supplier": "Sakthi Dairy", "total": 990, "has_error": false, "error_fields": [],
   "lines": [{"name": "Sakthi Curd 1L", "qty": 30, "unit": "L", "unit_price": 33, "line_total": 990}]},
  {"file": "inv_021.jpg", "supplier": "Sakthi Dairy", "total": 1000, "has_error": true, "error_fields": ["line 1 line_total"],
   "lines": [{"name": "Sakthi Curd 1L", "qty": 30, "unit": "L", "unit_price": 33, "line_total": 1000}]}
]
```

```json
// bench/data/cash/labels.example.json  (copy to labels.json and replace with your 40 real entries)
[
  {"file": "cash_001.jpg", "notes": [{"denomination": 500, "count": 2}], "total": 1000, "overlapping": false},
  {"file": "cash_002.jpg", "notes": [{"denomination": 100, "count": 1}, {"denomination": 50, "count": 1}, {"denomination": 10, "count": 2}], "total": 170, "overlapping": false}
]
```

Write the two `.example.json` files **without** the `//` comment lines, because JSON has no comments. The comment lines above only say where each file goes.

- [ ] **Step 6: Smoke-test the scorers offline with saved predictions**

Run from `services/live`:

```bash
mkdir -p /tmp/aira_bench_inv && cp ../../bench/data/invoice/labels.example.json /tmp/aira_bench_inv/labels.json && \
printf '{"inv_001.jpg":{"agreed":true,"total":990,"disagreements":[]},"inv_021.jpg":{"agreed":false,"total":1000,"disagreements":["arith: line 1"]}}' > /tmp/aira_bench_inv/preds.json && \
uv run python ../../bench/score_invoice.py --data /tmp/aira_bench_inv --predictions /tmp/aira_bench_inv/preds.json
```

Expected output:

```json
{
  "n": 2,
  "missing": 0,
  "catch_rate": 1.0,
  "false_alarm_rate": 0.0,
  "clean_total_accuracy": 1.0
}
```

- [ ] **Step 7: Run the whole vision suite**

Run: `cd services/live && uv run pytest -q tests/test_vision_numbers.py tests/test_vision_invoice_merge.py tests/test_vision_invoice.py tests/test_vision_cash.py tests/test_vision_metrics.py`
Expected: `32 passed`.

- [ ] **Step 8: Commit**

```bash
git add services/live/aira_live/vision/metrics.py services/live/tests/test_vision_metrics.py bench/score_invoice.py bench/score_cash.py bench/data/invoice/labels.example.json bench/data/cash/labels.example.json
git commit -m "feat(bench): invoice + cash metrics and scorers (catch rate, false alarms, total accuracy, latency)"
```

---

## Self-review

- **Spec coverage:**
  - Two independent reads, agreement in code, arithmetic in code (§3.1, §6) → Tasks 2–3.
  - Handwritten read-back (§2 step 3) → Task 2.
  - Cash two reads, INR only, change via Kanish's `money.change_due` using `CashRead.total`, no counterfeit claims (§3.6) → Task 4.
  - Benchmarks for invoices (30, 10 with errors) and cash (40) (§7) → Task 5.
- **Interface fidelity:**
  - `read_invoice(frames) -> InvoiceRead` and `read_cash(frames) -> CashRead` match the registry.
  - The single additive field (`CashRead.hint_key`, default `""`) is flagged for Saravana and Kanish.
- **Offline tests:** no network is used; `generate_json` / `image_part` are monkeypatched on the module that imports them.
