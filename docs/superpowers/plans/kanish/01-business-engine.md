# AIRA — Deterministic Business Engine Implementation Plan (Kanish · 01)

> **Addendum (2026-10-10): full dairy range + unit conversion.** Catalog aliases and tests cover ALL SKUs:
> `aavin_milk_500ml`, `aavin_milk_1l`, `sakthi_curd_500ml`, `sakthi_curd_1l`, `aavin_buttermilk_200ml`, `aavin_ghee_200ml`, `aavin_ghee_500ml`, `aavin_paneer_200g`, `aavin_butter_100g`, `arun_icecream_box`.
> - **Add** `catalog.to_selling_units(product_doc: dict, qty: float, unit: str) -> tuple[float, str]`, a pure function with tests. This signature is used by `kanish/02`:
>   - "5 kg paneer" → `(25, "pack")`
>   - "2 litres milk" → `(2, "L")`
>   - "3 ghee" → `(3, "jar")`
>   - **counted packs of a litre-unit product:** `unit="pack"`, e.g. 2 × `sakthi_curd_500ml` → `(1.0, "L")`
> - **Read-back** states both forms: "25 packs of 200 g = 5 kg".
> - **Ledger** always stores the selling unit.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| | |
|---|---|
| **Goal** | Build `services/live/aira_live/business/`, the deterministic engine that owns stock, sales, payments, money, soundbox parsing, reconciliation, expiry, audit, product/supplier resolution and the i18n keys for every business hint. The model only proposes; this code validates and commits, and a retried tool call can never deduct stock twice |
| **Architecture** | Pure-Python modules over a small `Store` abstraction. `MemoryStore` serves pure unit tests; `FirestoreStore` wraps the Firestore AsyncClient and its transactions, and is tested against the Firestore emulator. Every money and stock mutation runs in one transaction, all reads before all writes, guarded by an idempotency doc `idempotency/{sha1(idemKey)}`. Amounts are integer rupees. Quantities are SKU units (packs/boxes) as floats; litres derive from `packSizeL`. The engine never builds sentences: it returns codes, and tools speak `business.hints.say(code, lang, **kw)` → `aira_live.i18n.t(key, lang, **kw)`. After every successful commit (never inside a transaction), `business.outbox` publishes analytics events through `aira_live.analytics.events.publish`. The publisher is swappable, so tests use a fake |
| **Tech stack** | Python 3.12, pydantic v2, `google-cloud-firestore` (AsyncClient + `async_transactional`), `difflib`, pytest + pytest-asyncio, Firestore emulator (`firebase-tools` or `gcloud`) |
| **Spec** | `docs/superpowers/specs/2026-10-10-aira-design.md` (§2 core loop, §3 principles, §5 data model + states, §6 safety). Interfaces: `docs/superpowers/plans/2026-10-10-00-interfaces.md` (incl. "Fixed demo data and spoken-hint rule") |
| **Prereq** | Foundation plan merged: `services/live` uv project, `aira_live/db.py`, `aira_live/ids.py`, `aira_live/i18n/` with `t()` and `{en,ta,hi}.json`, pytest `asyncio_mode = "auto"` |
| **Branch** | `kanish/business-engine` · **Days:** D0–D2 (Oct 10–12) |

## Global Constraints

- **Fixed demo IDs**, used everywhere (catalog, tests, prompts):
  - SKUs `aavin_milk_500ml`, `aavin_milk_1l`, `sakthi_curd_500ml`, `sakthi_curd_1l`, `arun_icecream_box`
  - shop `murugan_dairy`
  - suppliers `aavin_vendor`, `sakthi_vendor`, `arun_distributor`
  - Supplier phone numbers come from `.env` (`SUPPLIER_PHONE_<SUPPLIER_ID_UPPER>`) through the seed script. Never commit them.
- **Spoken hints use one mechanism only:** `aira_live.i18n.t(key, lang, **kw)`. Engine keys are namespaced `biz.*` and live in `aira_live/i18n/{en,ta,hi}.json`. No hard-coded user-facing sentences in the engine.
- **Firestore paths** exactly as spec §5:
  - `shops/{shopId}/products/{sku}`, `suppliers/{id}`, `inventory/{sku}`, `ledger/{txnId}`, `sales/{id}`, `payments/{id}`, `auditLogs/{id}`.
  - Top-level `idempotency/{key}` (doc ID = SHA-1 of the raw idem key; the raw key is stored in `key`).
- **Ledger reasons:** exactly `"DELIVERY" | "SALE" | "RETURN" | "ADJUST" | "EXPIRED"`. **States:** exactly `ORDER_STATES`, `SALE_STATES`, `PAYMENT_STATES` from the interfaces.
- **Payments are `cash` or `upi` only.** Directions: `"in" | "out"`.
- **Money:**
  - Integer rupees.
  - INR denominations `500, 200, 100, 50, 20, 10, 5, 2, 1` (₹2000 is excluded from change plans).
  - No counterfeit claims.
  - Soundbox evidence confirms UPI only when the amount is equal (±₹0).
- **Public function signatures** are exactly those in the interfaces. Additions are optional keyword-only args only (`expiry_date`, `today`, `actor`), plus the new helper `catalog.resolve_supplier` (used by `kanish/02`).
- **Dates:** ISO `YYYY-MM-DD` strings, interpreted in `Asia/Kolkata`. Timestamps: ISO UTC strings.
- **Firestore transactions:** **all reads before any write.** Multi-line operations use `prepare_movement` (reads) and then `commit_movement` (writes).
- **Idempotency keys:** engine functions take `idem_key` as a parameter (unchanged). **Callers (the tools) pass `aira_live.ids.current_idem_key()`.** Tests and the scenario use explicit fixed keys.
- **Analytics events** (from `saravana/02`) are published **after commit, outside the transaction**, via `aira_live.analytics.events.publish`, and only when something new was committed (a replayed idempotent call publishes nothing):

  | Topic | Payload keys |
  |---|---|
  | `sales` | One message per sale line: `shop_id, sale_id, sku, qty, unit_price, ts` |
  | `inventory-movements` | `shop_id, txn_id, sku, delta, reason` |
  | `orders` | `shop_id, order_id, state` (helper `outbox.order_state`, called by `kanish/02` after each order transition) |
  | `discrepancies` | `shop_id, delivery_id, field, expected, actual` (helper `outbox.discrepancies`, called by `kanish/02` after saving a delivery) |

  A publish failure is logged and never undoes a committed business action.
- **Timestamps:** `sales.create` sets `createdAt` and `sales.complete` sets `completedAt`, both as **Firestore server timestamps** (`Store.server_ts()`: `firestore.SERVER_TIMESTAMP` in Firestore, a UTC `datetime` in `MemoryStore`).
- **`expiry.scan`** returns dicts with at least `sku, name, qty, daysLeft`. It also includes `batchId, expiryDate, status`.
- **The catalog JSON may include** an optional top-level `inventory` (starting stock, seeded through the ledger as `ADJUST`) and a per-product `demoDailyBase` (used by `saravana/02` forecasting). The engine stores `demoDailyBase` unchanged.

## Review Focus

1. **Retried sale completion** (same `idem_key`, or the same sale with a new key) leaves stock unchanged after the first deduction. Tests: `test_sale_complete_deducts_once_on_retry` (Task 5) and the Task 6 scenario.
2. **Soundbox transcripts** saying "sent/debited/failed", containing two different amounts, or containing paise → `None`. Test: `test_soundbox_rejects` (Task 2).
3. **Selling more than on hand** raises `InsufficientStock` and writes nothing. Tests: `test_negative_stock_raises` (Task 4) and `test_sale_create_insufficient_stock` (Task 5).
4. **Ambiguous product** (no default pack, no size hint), **unknown product**, or **unknown supplier** → `None`, so the tool asks using `biz.product.ambiguous` / `biz.product.unknown` / `biz.supplier.unknown`. Tests: `test_resolve_none` and `test_resolve_supplier` (Task 3).
5. **An invoice whose two reads disagree** (`agreed=False`) yields an `INVOICE_UNREADABLE` discrepancy, never a silent match, and every discrepancy code has an i18n key in all three languages. Tests: `test_compare_unreadable_invoice` and `test_every_hint_key_in_all_languages` (Task 6).

---

## File Structure

```
content/catalog/murugan_dairy.json                 seed: shop, products (aliases ta/hi/en), suppliers (aliases, phoneEnv)
.env.example                                       + SUPPLIER_PHONE_AAVIN_VENDOR / _SAKTHI_VENDOR / _ARUN_DISTRIBUTOR
services/live/aira_live/business/
  __init__.py
  clock.py        today_ist(), now_iso()
  paths.py        Firestore path builders (single place for paths)
  store.py        Txn / Store protocols, MemoryStore, FirestoreStore, get_store/set_store
  _ids.py         doc_id_for(prefix, idem_key)
  text.py         tokens(), normalize_digits() — Indic-safe tokenisation
  states.py       ORDER_STATES / SALE_STATES / PAYMENT_STATES, transition(), InvalidTransition
  money.py        change_due(), notes_for(), total_of(), InsufficientTender
  numwords.py     amounts_in_words(text) — en / hi (Devanagari) / ta number words → ints
  soundbox.py     SoundboxEvent, parse()
  catalog.py      resolve() spoken → ProductRef ; resolve_supplier() spoken → supplier dict
  seed.py         load_catalog(), seed_into(), seed_inventory(), CLI (phones from env)
  outbox.py       post-commit analytics publishing (inventory_movement, sale_lines, order_state, discrepancies)
  ledger.py       apply(), on_hand(), prepare_movement(), commit_movement(), InsufficientStock
  expiry.py       scan()
  audit.py        log(), log_in_txn()
  payments.py     record(), confirm(), fail(), PaymentMismatch
  sales.py        create(), complete(), void(), SaleError
  reconcile.py    ReconcileResult, compare(), payable()
  hints.py        HINTS code → i18n key, key_for(), say() → aira_live.i18n.t
services/live/aira_live/i18n/{en,ta,hi}.json       + biz.* keys (merge into the existing objects)
services/live/aira_live/vision/identify.py         (ONLY if absent: ProductRef/IdentifyResult models — Sarmitha owns)
services/live/aira_live/vision/invoice.py          (ONLY if absent: InvoiceLine/InvoiceRead models — Sarmitha owns)
services/live/tests/business/
  __init__.py conftest.py test_states_store.py test_money_soundbox.py test_catalog.py
  test_ledger_expiry_audit.py test_payments_sales.py test_reconcile_scenario.py test_hints.py
```

---

### Task 1: Store abstraction, paths, ids, clock, text utils and the state machine

**Files:**
- Create: `services/live/aira_live/business/__init__.py`, `clock.py`, `paths.py`, `store.py`, `_ids.py`, `text.py`, `states.py`, `outbox.py`
- Test: `services/live/tests/business/__init__.py`, `services/live/tests/business/test_states_store.py`

**Interfaces:**
- Consumes:
  - `aira_live.db.db()` (foundation), inside `FirestoreStore` only.
  - `aira_live.analytics.events.publish(topic, payload)` (`saravana/02`), imported lazily inside `outbox`.
- Produces:
  - `states.transition(entity, current, target) -> str`, `states.InvalidTransition`, `ORDER_STATES`, `SALE_STATES`, `PAYMENT_STATES`.
  - `Store.run(fn)`, `Store.get(path)`, `Store.set(path, data)`, `Store.list(collection_path)`, `Store.server_ts()`; `MemoryStore`, `FirestoreStore`, `get_store()`, `set_store()`.
  - `paths.*`, `doc_id_for(prefix, idem_key)`, `text.tokens(s)`, `text.normalize_digits(s)`, `clock.today_ist()`, `clock.now_iso()`.
  - `outbox.set_publisher(fn | None)`, `outbox.inventory_movement(shop_id, txn_id, sku, delta, reason)`, `outbox.sale_lines(shop_id, sale_id, items)`, `outbox.order_state(shop_id, order_id, state)`, `outbox.discrepancies(shop_id, delivery_id, result)`.

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/business/test_states_store.py
import pytest
from aira_live.business import states
from aira_live.business.states import InvalidTransition, transition
from aira_live.business.store import MemoryStore
from aira_live.business._ids import doc_id_for


def test_order_happy_path_transitions():
    path = ["DRAFT", "ORDER_PLACED", "DELIVERY_PENDING", "DISCREPANCY",
            "PAYMENT_PENDING", "PAYMENT_CONFIRMED", "CLOSED"]
    for cur, nxt in zip(path, path[1:]):
        assert transition("order", cur, nxt) == nxt
    assert transition("order", "DELIVERY_PENDING", "DELIVERY_RECONCILED") == "DELIVERY_RECONCILED"
    assert transition("order", "DELIVERY_RECONCILED", "PAYMENT_PENDING") == "PAYMENT_PENDING"


def test_order_invalid_transition_raises():
    with pytest.raises(InvalidTransition):
        transition("order", "ORDER_PLACED", "PAYMENT_CONFIRMED")
    with pytest.raises(InvalidTransition):
        transition("order", "CLOSED", "DRAFT")
    with pytest.raises(InvalidTransition):
        transition("order", "NOT_A_STATE", "DRAFT")


def test_sale_and_payment_terminal_states():
    assert transition("sale", "SALE_PENDING", "SALE_COMPLETED") == "SALE_COMPLETED"
    assert transition("sale", "SALE_PENDING", "SALE_VOID") == "SALE_VOID"
    assert transition("payment", "PENDING", "CONFIRMED") == "CONFIRMED"
    assert transition("payment", "PENDING", "FAILED") == "FAILED"
    with pytest.raises(InvalidTransition):
        transition("sale", "SALE_COMPLETED", "SALE_VOID")
    with pytest.raises(InvalidTransition):
        transition("payment", "CONFIRMED", "FAILED")
    assert states.SALE_STATES == ["SALE_PENDING", "SALE_COMPLETED", "SALE_VOID"]
    assert states.PAYMENT_STATES == ["PENDING", "CONFIRMED", "FAILED"]


def test_unknown_entity_raises():
    with pytest.raises(InvalidTransition):
        transition("refund", "A", "B")


async def test_memory_store_txn_atomic_on_error():
    s = MemoryStore()
    await s.set("shops/x/inventory/a", {"onHand": 1.0})

    async def boom(txn):
        txn.set("shops/x/inventory/a", {"onHand": 99.0})
        raise RuntimeError("fail mid-transaction")

    with pytest.raises(RuntimeError):
        await s.run(boom)
    assert (await s.get("shops/x/inventory/a")) == {"onHand": 1.0}


async def test_memory_store_list_children_only():
    s = MemoryStore()
    await s.set("shops/x/products/a", {"n": 1})
    await s.set("shops/x/products/b", {"n": 2})
    await s.set("shops/x/products/b/sub/c", {"n": 3})
    await s.set("shops/y/products/z", {"n": 4})
    assert await s.list("shops/x/products") == [("a", {"n": 1}), ("b", {"n": 2})]


def test_doc_id_for_deterministic():
    assert doc_id_for("s", "sess1:call9") == doc_id_for("s", "sess1:call9")
    assert doc_id_for("s", "sess1:call9") != doc_id_for("s", "sess1:call10")
    assert doc_id_for("s", "k").startswith("s_") and len(doc_id_for("s", "k")) == 18
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest tests/business/test_states_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.business'`.

- [ ] **Step 3: Implement the modules**

```python
# services/live/aira_live/business/__init__.py
"""AIRA deterministic engine. The model proposes; this package validates and commits."""
```

```python
# services/live/aira_live/business/clock.py
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def today_ist() -> date:
    return datetime.now(IST).date()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```

```python
# services/live/aira_live/business/_ids.py
import hashlib


def doc_id_for(prefix: str, idem_key: str) -> str:
    """Deterministic document id so a retried call writes the same document."""
    return f"{prefix}_{hashlib.sha1(idem_key.encode('utf-8')).hexdigest()[:16]}"
```

```python
# services/live/aira_live/business/paths.py
"""Single source of Firestore paths (spec §5)."""
import hashlib


def shop(shop_id: str) -> str: return f"shops/{shop_id}"
def product(shop_id: str, sku: str) -> str: return f"shops/{shop_id}/products/{sku}"
def products(shop_id: str) -> str: return f"shops/{shop_id}/products"
def supplier(shop_id: str, supplier_id: str) -> str: return f"shops/{shop_id}/suppliers/{supplier_id}"
def suppliers(shop_id: str) -> str: return f"shops/{shop_id}/suppliers"
def inventory(shop_id: str, sku: str) -> str: return f"shops/{shop_id}/inventory/{sku}"
def inventories(shop_id: str) -> str: return f"shops/{shop_id}/inventory"
def ledger(shop_id: str, txn_id: str) -> str: return f"shops/{shop_id}/ledger/{txn_id}"
def sale(shop_id: str, sale_id: str) -> str: return f"shops/{shop_id}/sales/{sale_id}"
def payment(shop_id: str, payment_id: str) -> str: return f"shops/{shop_id}/payments/{payment_id}"
def audit(shop_id: str, audit_id: str) -> str: return f"shops/{shop_id}/auditLogs/{audit_id}"


def idem(key: str) -> str:
    return "idempotency/" + hashlib.sha1(key.encode("utf-8")).hexdigest()
```

```python
# services/live/aira_live/business/store.py
"""Tiny transactional store. MemoryStore for unit tests, FirestoreStore for production/emulator."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol, TypeVar

T = TypeVar("T")


class Txn(Protocol):
    async def get(self, path: str) -> dict | None: ...
    def set(self, path: str, data: dict) -> None: ...


class Store(Protocol):
    async def run(self, fn: Callable[[Txn], Awaitable[T]]) -> T: ...
    async def get(self, path: str) -> dict | None: ...
    async def set(self, path: str, data: dict) -> None: ...
    async def list(self, collection_path: str) -> list[tuple[str, dict]]: ...
    def server_ts(self) -> object: ...


class _MemTxn:
    def __init__(self, data: dict[str, dict]):
        self._data = data
        self.writes: dict[str, dict] = {}

    async def get(self, path: str) -> dict | None:
        if path in self.writes:
            return copy.deepcopy(self.writes[path])
        value = self._data.get(path)
        return copy.deepcopy(value) if value is not None else None

    def set(self, path: str, data: dict) -> None:
        self.writes[path] = copy.deepcopy(data)


class MemoryStore:
    """Serialised, all-or-nothing transactions over a dict of path -> document."""

    def __init__(self) -> None:
        self.data: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    async def run(self, fn):
        async with self._lock:
            txn = _MemTxn(self.data)
            result = await fn(txn)          # an exception discards txn.writes
            self.data.update(txn.writes)
            return result

    async def get(self, path: str) -> dict | None:
        value = self.data.get(path)
        return copy.deepcopy(value) if value is not None else None

    async def set(self, path: str, data: dict) -> None:
        self.data[path] = copy.deepcopy(data)

    async def list(self, collection_path: str) -> list[tuple[str, dict]]:
        prefix = collection_path.rstrip("/") + "/"
        out = []
        for path, doc in self.data.items():
            rest = path[len(prefix):] if path.startswith(prefix) else None
            if rest and "/" not in rest:
                out.append((rest, copy.deepcopy(doc)))
        return sorted(out, key=lambda x: x[0])

    def server_ts(self) -> object:
        return datetime.now(timezone.utc)


class _FsTxn:
    def __init__(self, client, transaction):
        self._c = client
        self._t = transaction

    async def get(self, path: str) -> dict | None:
        snap = await self._c.document(path).get(transaction=self._t)
        return snap.to_dict() if snap.exists else None

    def set(self, path: str, data: dict) -> None:
        self._t.set(self._c.document(path), data)


class FirestoreStore:
    """Firestore AsyncClient store. Verify the transaction API against
    https://cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.async_transaction"""

    def __init__(self, client=None):
        if client is None:
            from aira_live.db import db
            client = db()
        self._c = client

    async def run(self, fn):
        from google.cloud.firestore import async_transactional

        transaction = self._c.transaction()

        @async_transactional
        async def _inner(t):
            return await fn(_FsTxn(self._c, t))

        return await _inner(transaction)

    async def get(self, path: str) -> dict | None:
        snap = await self._c.document(path).get()
        return snap.to_dict() if snap.exists else None

    async def set(self, path: str, data: dict) -> None:
        await self._c.document(path).set(data)

    async def list(self, collection_path: str) -> list[tuple[str, dict]]:
        docs = [(d.id, d.to_dict()) async for d in self._c.collection(collection_path).stream()]
        return sorted(docs, key=lambda x: x[0])

    def server_ts(self) -> object:
        from google.cloud.firestore import SERVER_TIMESTAMP
        return SERVER_TIMESTAMP


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = FirestoreStore()
    return _store


def set_store(store: Store | None) -> None:
    global _store
    _store = store
```

```python
# services/live/aira_live/business/text.py
"""Indic-safe text helpers. Python's \\w drops Tamil/Devanagari vowel signs, so we split on separators instead."""
import re

_SPLIT = re.compile(r"[\s,.;:!?()\[\]{}\"'“”‘’/|\\+*=<>@#&%-]+")
_DIGITS = str.maketrans("௦௧௨௩௪௫௬௭௮௯०१२३४५६७८९", "01234567890123456789")


def normalize_digits(s: str) -> str:
    return s.translate(_DIGITS)


def tokens(s: str) -> list[str]:
    return [t for t in _SPLIT.split(normalize_digits(s).lower().replace("₹", " ₹ ")) if t]
```

```python
# services/live/aira_live/business/states.py
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
    "DELIVERY_RECONCILED": {"PAYMENT_PENDING"},
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
    return set(_TABLES[entity].get(current, set()))
```

```python
# services/live/aira_live/business/outbox.py
"""Post-commit analytics events (saravana/02 contracts). Never called inside a transaction.
A publish failure is logged and never undoes a committed business action."""
import logging
from typing import Awaitable, Callable

from aira_live.business.clock import now_iso

log = logging.getLogger(__name__)
Publisher = Callable[[str, dict], Awaitable[str]]
_publisher: Publisher | None = None


def set_publisher(fn: Publisher | None) -> None:
    """Tests inject a fake; None restores aira_live.analytics.events.publish."""
    global _publisher
    _publisher = fn


async def _publish(topic: str, payload: dict) -> None:
    pub = _publisher
    if pub is None:
        from aira_live.analytics.events import publish as pub
    try:
        await pub(topic, payload)
    except Exception:
        log.exception("analytics publish failed topic=%s payload=%s", topic, payload)


async def inventory_movement(shop_id: str, txn_id: str, sku: str, delta: float, reason: str) -> None:
    await _publish("inventory-movements",
                   {"shop_id": shop_id, "txn_id": txn_id, "sku": sku, "delta": float(delta), "reason": reason})


async def sale_lines(shop_id: str, sale_id: str, items: list[dict]) -> None:
    ts = now_iso()
    for it in items:
        await _publish("sales", {"shop_id": shop_id, "sale_id": sale_id, "sku": it["sku"], "qty": float(it["qty"]),
                                 "unit_price": int(it["unitPrice"]), "ts": ts})


async def order_state(shop_id: str, order_id: str, state: str) -> None:
    await _publish("orders", {"shop_id": shop_id, "order_id": order_id, "state": state})


async def discrepancies(shop_id: str, delivery_id: str, result) -> None:
    """result: reconcile.ReconcileResult (duck-typed: .discrepancies list of dicts)."""
    for d in result.discrepancies:
        field = d["kind"] if "sku" not in d else f"{d['kind']}:{d['sku']}"
        await _publish("discrepancies", {"shop_id": shop_id, "delivery_id": delivery_id, "field": field,
                                         "expected": d.get("expected"), "actual": d.get("actual")})
```

Also create the empty file `services/live/tests/business/__init__.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest tests/business/test_states_store.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git checkout -b kanish/business-engine
git add services/live/aira_live/business services/live/tests/business
git commit -m "feat(business): store abstraction, paths, ids, text utils, state machine, post-commit outbox"
```

---

### Task 2: Money, number words and the soundbox parser

**Files:**
- Create: `services/live/aira_live/business/money.py`, `numwords.py`, `soundbox.py`
- Test: `services/live/tests/business/test_money_soundbox.py`

**Interfaces:**
- Consumes: `text.tokens`, `text.normalize_digits` (Task 1).
- Produces:
  - `money.change_due(total: int, tendered: int) -> int`, `money.notes_for(amount: int) -> list[dict]`, `money.total_of(notes) -> int`, `money.InsufficientTender`, `money.DENOMINATIONS`.
  - `numwords.amounts_in_words(text: str) -> list[int]`.
  - `soundbox.SoundboxEvent(amount: int, provider: str | None)` and `soundbox.parse(transcript: str) -> SoundboxEvent | None`.

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/business/test_money_soundbox.py
import pytest
from aira_live.business import money, numwords, soundbox


def test_change_due_basic():
    assert money.change_due(120, 200) == 80
    assert money.change_due(120, 120) == 0


def test_change_due_insufficient():
    with pytest.raises(money.InsufficientTender):
        money.change_due(120, 100)


def test_notes_for_80():
    assert money.notes_for(80) == [{"denomination": 50, "count": 1}, {"denomination": 20, "count": 1},
                                   {"denomination": 10, "count": 1}]


def test_notes_for_zero_and_large():
    assert money.notes_for(0) == []
    assert money.notes_for(1400) == [{"denomination": 500, "count": 2}, {"denomination": 200, "count": 2}]


def test_total_of():
    assert money.total_of([{"denomination": 500, "count": 2}, {"denomination": 100, "count": 4}]) == 1400


@pytest.mark.parametrize("text,expected", [
    ("received rupees sixty", [60]),
    ("one thousand two hundred fifty", [1250]),
    ("two lakh", [200000]),
    ("साठ रुपये", [60]),
    ("एक सौ बीस", [120]),
    ("दो हज़ार पांच सौ", [2500]),
    ("அறுபது ரூபாய்", [60]),
    ("இருபத்தி ஐந்து", [25]),
    ("நூற்றி இருபது", [120]),
    ("இரண்டாயிரத்து ஐநூறு", [2500]),
])
def test_amounts_in_words(text, expected):
    assert numwords.amounts_in_words(text) == expected


@pytest.mark.parametrize("transcript,amount,provider", [
    ("Received rupees sixty", 60, None),
    ("₹1,250 received on Paytm", 1250, "Paytm"),
    ("PhonePe par ₹60 prapt hue", 60, "PhonePe"),
    ("पेटीएम पर साठ रुपये प्राप्त हुए", 60, "Paytm"),
    ("Paytm पर ₹60 प्राप्त हुए", 60, "Paytm"),
    ("அறுபது ரூபாய் பெறப்பட்டது", 60, None),
    ("போன்பே மூலம் ₹120 பெறப்பட்டது", 120, "PhonePe"),
])
def test_soundbox_parses(transcript, amount, provider):
    ev = soundbox.parse(transcript)
    assert ev is not None and ev.amount == amount and ev.provider == provider


@pytest.mark.parametrize("transcript", [
    "₹60 sent to Kumar",
    "received 60 and 80",
    "hello how are you",
    "₹60.50 received",
    "payment of ₹60 failed",
])
def test_soundbox_rejects(transcript):
    assert soundbox.parse(transcript) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest tests/business/test_money_soundbox.py -q`
Expected: FAIL with `ImportError: cannot import name 'money'`.

- [ ] **Step 3: Implement**

```python
# services/live/aira_live/business/money.py
"""Exact rupee arithmetic. Integer rupees only; the ₹2000 note is excluded from change plans."""

DENOMINATIONS = (500, 200, 100, 50, 20, 10, 5, 2, 1)


class InsufficientTender(ValueError):
    pass


def change_due(total: int, tendered: int) -> int:
    if total < 0 or tendered < 0:
        raise ValueError("amounts must be non-negative")
    if tendered < total:
        raise InsufficientTender(f"short by {total - tendered}")
    return tendered - total


def notes_for(amount: int) -> list[dict]:
    if amount < 0:
        raise ValueError("amount must be non-negative")
    out, rem = [], int(amount)
    for d in DENOMINATIONS:
        count, rem = divmod(rem, d)
        if count:
            out.append({"denomination": d, "count": count})
    return out


def total_of(notes: list[dict]) -> int:
    return sum(int(n["denomination"]) * int(n["count"]) for n in notes)
```

```python
# services/live/aira_live/business/numwords.py
"""Number words → integers for en / hi (Devanagari) / ta. Returns every maximal number-word run as an int."""
from aira_live.business.text import tokens

_EN_SMALL = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
            "ninety": 90}
_EN_MULT = {"hundred": 100, "thousand": 1000, "lakh": 100000, "lakhs": 100000}

_HI = {
    "शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "छे": 6, "सात": 7,
    "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15,
    "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20, "इक्कीस": 21, "बाईस": 22, "तेईस": 23,
    "चौबीस": 24, "पच्चीस": 25, "छब्बीस": 26, "सत्ताईस": 27, "अट्ठाईस": 28, "उनतीस": 29, "तीस": 30,
    "इकतीस": 31, "बत्तीस": 32, "तैंतीस": 33, "चौंतीस": 34, "पैंतीस": 35, "छत्तीस": 36, "सैंतीस": 37,
    "अड़तीस": 38, "उनतालीस": 39, "चालीस": 40, "इकतालीस": 41, "बयालीस": 42, "तैंतालीस": 43, "चवालीस": 44,
    "पैंतालीस": 45, "छियालीस": 46, "सैंतालीस": 47, "अड़तालीस": 48, "उनचास": 49, "पचास": 50, "इक्यावन": 51,
    "बावन": 52, "तिरपन": 53, "चौवन": 54, "पचपन": 55, "छप्पन": 56, "सत्तावन": 57, "अट्ठावन": 58, "उनसठ": 59,
    "साठ": 60, "इकसठ": 61, "बासठ": 62, "तिरसठ": 63, "चौंसठ": 64, "पैंसठ": 65, "छियासठ": 66, "सड़सठ": 67,
    "अड़सठ": 68, "उनहत्तर": 69, "सत्तर": 70, "इकहत्तर": 71, "बहत्तर": 72, "तिहत्तर": 73, "चौहत्तर": 74,
    "पचहत्तर": 75, "छिहत्तर": 76, "सतहत्तर": 77, "अठहत्तर": 78, "उन्यासी": 79, "अस्सी": 80, "इक्यासी": 81,
    "बयासी": 82, "तिरासी": 83, "चौरासी": 84, "पचासी": 85, "छियासी": 86, "सत्तासी": 87, "अट्ठासी": 88,
    "नवासी": 89, "नब्बे": 90, "इक्यानवे": 91, "बानवे": 92, "तिरानवे": 93, "चौरानवे": 94, "पचानवे": 95,
    "छियानवे": 96, "सत्तानवे": 97, "अट्ठानवे": 98, "निन्यानवे": 99,
}
_HI_MULT = {"सौ": 100, "हज़ार": 1000, "हजार": 1000, "लाख": 100000}

# Tamil is additive: compound/combining forms carry their own value ("இருபத்தி ஐந்து" = 20 + 5).
_TA = {
    "ஒன்று": 1, "ஒரு": 1, "இரண்டு": 2, "ரெண்டு": 2, "மூன்று": 3, "நான்கு": 4, "நாலு": 4, "ஐந்து": 5,
    "அஞ்சு": 5, "ஆறு": 6, "ஏழு": 7, "எட்டு": 8, "ஒன்பது": 9, "பத்து": 10, "பதினொன்று": 11, "பன்னிரண்டு": 12,
    "பதிமூன்று": 13, "பதினான்கு": 14, "பதினைந்து": 15, "பதினாறு": 16, "பதினேழு": 17, "பதினெட்டு": 18,
    "பத்தொன்பது": 19, "இருபது": 20, "முப்பது": 30, "நாற்பது": 40, "ஐம்பது": 50, "அறுபது": 60, "எழுபது": 70,
    "எண்பது": 80, "தொண்ணூறு": 90,
    "இருபத்தி": 20, "இருபத்து": 20, "முப்பத்தி": 30, "முப்பத்து": 30, "நாற்பத்தி": 40, "நாற்பத்து": 40,
    "ஐம்பத்தி": 50, "ஐம்பத்து": 50, "அறுபத்தி": 60, "அறுபத்து": 60, "எழுபத்தி": 70, "எழுபத்து": 70,
    "எண்பத்தி": 80, "எண்பத்து": 80, "தொண்ணூற்றி": 90, "தொண்ணூற்று": 90,
    "நூறு": 100, "நூற்றி": 100, "நூற்று": 100, "இருநூறு": 200, "இருநூற்றி": 200, "இருநூற்று": 200,
    "முந்நூறு": 300, "முன்னூறு": 300, "முந்நூற்றி": 300, "முன்னூற்றி": 300, "நானூறு": 400, "நானூற்றி": 400,
    "ஐநூறு": 500, "ஐநூற்றி": 500, "அறுநூறு": 600, "அறுநூற்றி": 600, "எழுநூறு": 700, "எழுநூற்றி": 700,
    "எண்ணூறு": 800, "எண்ணூற்றி": 800, "தொள்ளாயிரம்": 900, "தொள்ளாயிரத்து": 900,
    "ஆயிரம்": 1000, "ஆயிரத்து": 1000, "இரண்டாயிரம்": 2000, "இரண்டாயிரத்து": 2000, "மூவாயிரம்": 3000,
    "மூவாயிரத்து": 3000, "நான்காயிரம்": 4000, "நான்காயிரத்து": 4000, "ஐயாயிரம்": 5000, "ஐயாயிரத்து": 5000,
    "ஆறாயிரம்": 6000, "ஆறாயிரத்து": 6000, "ஏழாயிரம்": 7000, "ஏழாயிரத்து": 7000, "எட்டாயிரம்": 8000,
    "எட்டாயிரத்து": 8000, "ஒன்பதாயிரம்": 9000, "ஒன்பதாயிரத்து": 9000,
}


def _kind(tok: str) -> str | None:
    if tok in _EN_SMALL or tok in _EN_TENS or tok in _EN_MULT or tok == "and":
        return "en"
    if tok in _HI or tok in _HI_MULT:
        return "hi"
    if tok in _TA:
        return "ta"
    return None


def _eval_multiplicative(run: list[str], small: dict, mult: dict) -> int:
    total, current = 0, 0
    for tok in run:
        if tok in mult:
            m = mult[tok]
            if m == 100:
                current = (current or 1) * 100
            else:
                total += (current or 1) * m
                current = 0
        else:
            current += small[tok]
    return total + current


def amounts_in_words(text: str) -> list[int]:
    out: list[int] = []
    run: list[str] = []
    run_kind: str | None = None

    def flush():
        nonlocal run, run_kind
        words = [t for t in run if t != "and"]
        if words:
            if run_kind == "en":
                out.append(_eval_multiplicative(words, {**_EN_SMALL, **_EN_TENS}, _EN_MULT))
            elif run_kind == "hi":
                out.append(_eval_multiplicative(words, _HI, _HI_MULT))
            elif run_kind == "ta":
                out.append(sum(_TA[t] for t in words))
        run, run_kind = [], None

    for tok in tokens(text):
        kind = _kind(tok)
        if kind is None or (run_kind is not None and kind != run_kind):
            flush()
            if kind is None or (kind == "en" and tok == "and"):
                continue
        run.append(tok)
        run_kind = kind
    flush()
    return out
```

```python
# services/live/aira_live/business/soundbox.py
"""Parse a payment-soundbox announcement heard in the Live input transcript (en / hi / ta)."""
import re
from decimal import Decimal

from pydantic import BaseModel

from aira_live.business.numwords import amounts_in_words
from aira_live.business.text import normalize_digits, tokens


class SoundboxEvent(BaseModel):
    amount: int
    provider: str | None


_RECEIVED_EN = {"received", "credited", "prapt"}
_RECEIVED_SUB = ["प्राप्त", "मिले", "जमा", "பெறப்பட்டது", "பெற்றுள்ளீர்கள்", "வரவு", "கிடைத்தது"]
_NEGATIVE_EN = {"sent", "debited", "failed", "declined", "refund", "refunded", "pending", "paid"}
_NEGATIVE_SUB = ["भेजे", "कटे", "असफल", "विफल", "लंबित", "அனுப்பப்பட்டது", "தோல்வி", "நிலுவை"]
_PROVIDERS = [("phonepe", "PhonePe"), ("phone pe", "PhonePe"), ("paytm", "Paytm"), ("gpay", "Google Pay"),
              ("google pay", "Google Pay"), ("bharatpe", "BharatPe"), ("पेटीएम", "Paytm"), ("फोनपे", "PhonePe"),
              ("போன்பே", "PhonePe"), ("பேடிஎம்", "Paytm")]
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _digit_amounts(text: str) -> list[Decimal]:
    return [Decimal(m.group().replace(",", "")) for m in _NUM.finditer(text)]


def parse(transcript: str) -> SoundboxEvent | None:
    if not transcript or not transcript.strip():
        return None
    text = normalize_digits(transcript).lower()
    toks = set(tokens(text))
    if toks & _NEGATIVE_EN or any(s in text for s in _NEGATIVE_SUB):
        return None
    if not (toks & _RECEIVED_EN or any(s in text for s in _RECEIVED_SUB)):
        return None
    digits = _digit_amounts(text)
    if any(d != d.to_integral_value() for d in digits):
        return None                                     # paise present → cannot match exactly
    amounts = {int(d) for d in digits} | set(amounts_in_words(text))
    amounts.discard(0)
    if len(amounts) != 1:
        return None
    provider = next((name for key, name in _PROVIDERS if key in text), None)
    return SoundboxEvent(amount=amounts.pop(), provider=provider)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest tests/business/test_money_soundbox.py -q`
Expected: `27 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/live/aira_live/business/money.py services/live/aira_live/business/numwords.py \
        services/live/aira_live/business/soundbox.py services/live/tests/business/test_money_soundbox.py
git commit -m "feat(business): rupee change maths, ta/hi/en number words, soundbox payment parser"
```

---

### Task 3: Seed catalog, product and supplier resolution (ta/hi/en aliases), and the seeder

**Files:**
- Create or overwrite: `content/catalog/murugan_dairy.json`
- Modify: `.env.example` (append three supplier-phone lines)
- Create: `services/live/aira_live/business/catalog.py`, `services/live/aira_live/business/seed.py`
- Create **only if absent**: `services/live/aira_live/vision/__init__.py`, `services/live/aira_live/vision/identify.py` (models only; Sarmitha's plan adds the functions)
- Test: `services/live/tests/business/conftest.py`, `services/live/tests/business/test_catalog.py`

**Interfaces:**
- Consumes: `vision.identify.ProductRef(sku, name, brand, pack)` (interfaces); `get_store()`.
- Produces:
  - `catalog.resolve(shop_id: str, spoken: str, lang: Lang) -> ProductRef | None`.
  - `catalog.resolve_supplier(shop_id: str, spoken: str, lang: Lang) -> dict | None`, returning `{id, name, phone, channel, products, aliases}`. **New helper for `kanish/02`'s `order_create`.**
  - `seed.load_catalog(path) -> dict`, `seed.seed_into(store, catalog) -> None`.
  - CLI: `uv run python -m aira_live.business.seed --catalog ../../content/catalog/murugan_dairy.json`.

- [ ] **Step 1: Write the seed catalog**

Prices are demo values: verify them against the local MRP before filming. Phones are not stored here; each supplier names the env var that holds its phone. The optional top-level `inventory` is the demo starting stock; `sakthi_curd_1l` starts at 4, so the low-stock/reorder moment triggers after two sales. It is seeded through the ledger in Task 4 (`seed_inventory`); `seed_into` writes only the shop, products and suppliers. The per-product `demoDailyBase` is kept as-is for `saravana/02` forecasting.

```json
{
  "shop": {"id": "murugan_dairy", "name": "Murugan Dairy", "city": "Chennai", "currency": "INR", "timezone": "Asia/Kolkata"},
  "demoPrices": true,
  "products": [
    {"sku": "aavin_milk_500ml", "name": "Aavin Toned Milk 500 ml", "brand": "Aavin", "pack": "500 ml pouch",
     "unit": "pack", "packSizeL": 0.5, "price": 24, "costPrice": 22, "reorderThreshold": 10, "shelfLifeDays": 2,
     "defaultPack": true, "demoDailyBase": 40,
     "aliases": {"en": ["aavin milk", "toned milk", "milk"], "ta": ["ஆவின் பால்", "பால்", "paal"], "hi": ["आविन दूध", "दूध", "doodh"]}},
    {"sku": "aavin_milk_1l", "name": "Aavin Toned Milk 1 L", "brand": "Aavin", "pack": "1 L pouch",
     "unit": "pack", "packSizeL": 1.0, "price": 46, "costPrice": 42, "reorderThreshold": 5, "shelfLifeDays": 2,
     "defaultPack": false, "demoDailyBase": 12,
     "aliases": {"en": ["aavin milk", "toned milk", "milk"], "ta": ["ஆவின் பால்", "பால்", "paal"], "hi": ["आविन दूध", "दूध", "doodh"]}},
    {"sku": "sakthi_curd_1l", "name": "Sakthi Curd 1 L", "brand": "Sakthi", "pack": "1 L pouch",
     "unit": "pack", "packSizeL": 1.0, "price": 60, "costPrice": 50, "reorderThreshold": 5, "shelfLifeDays": 4,
     "defaultPack": true, "demoDailyBase": 26,
     "aliases": {"en": ["sakthi curd", "shakti curd", "curd"], "ta": ["சக்தி தயிர்", "தயிர்", "thayir"], "hi": ["शक्ति दही", "दही", "dahi"]}},
    {"sku": "sakthi_curd_500ml", "name": "Sakthi Curd 500 ml", "brand": "Sakthi", "pack": "500 ml pouch",
     "unit": "pack", "packSizeL": 0.5, "price": 32, "costPrice": 27, "reorderThreshold": 6, "shelfLifeDays": 4,
     "defaultPack": false, "demoDailyBase": 10,
     "aliases": {"en": ["sakthi curd", "shakti curd", "curd"], "ta": ["சக்தி தயிர்", "தயிர்", "thayir"], "hi": ["शक्ति दही", "दही", "dahi"]}},
    {"sku": "arun_icecream_box", "name": "Arun Ice Cream Family Box", "brand": "Arun", "pack": "family box",
     "unit": "box", "packSizeL": null, "price": 150, "costPrice": 125, "reorderThreshold": 3, "shelfLifeDays": 180,
     "defaultPack": true, "demoDailyBase": 3,
     "aliases": {"en": ["arun ice cream", "ice cream", "icecream box"], "ta": ["அருண் ஐஸ்கிரீம்", "ஐஸ்கிரீம்"], "hi": ["अरुण आइसक्रीम", "आइसक्रीम"]}}
  ],
  "inventory": [
    {"sku": "aavin_milk_500ml", "qty": 20},
    {"sku": "aavin_milk_1l", "qty": 6},
    {"sku": "sakthi_curd_1l", "qty": 4},
    {"sku": "sakthi_curd_500ml", "qty": 8},
    {"sku": "arun_icecream_box", "qty": 5}
  ],
  "suppliers": [
    {"id": "aavin_vendor", "name": "Aavin milk vendor", "phoneEnv": "SUPPLIER_PHONE_AAVIN_VENDOR", "channel": "whatsapp",
     "products": ["aavin_milk_500ml", "aavin_milk_1l"],
     "aliases": {"en": ["aavin vendor", "milk vendor", "milkman", "aavin agent"], "ta": ["ஆவின் வியாபாரி", "பால் வியாபாரி", "பால்காரர்"], "hi": ["आविन वाले", "दूध वाले"]}},
    {"id": "sakthi_vendor", "name": "Sakthi curd vendor", "phoneEnv": "SUPPLIER_PHONE_SAKTHI_VENDOR", "channel": "whatsapp",
     "products": ["sakthi_curd_1l", "sakthi_curd_500ml"],
     "aliases": {"en": ["sakthi vendor", "curd vendor", "sakthi agent"], "ta": ["சக்தி வியாபாரி", "தயிர் வியாபாரி"], "hi": ["शक्ति वाले", "दही वाले"]}},
    {"id": "arun_distributor", "name": "Arun ice cream distributor", "phoneEnv": "SUPPLIER_PHONE_ARUN_DISTRIBUTOR", "channel": "call",
     "products": ["arun_icecream_box"],
     "aliases": {"en": ["arun distributor", "ice cream distributor", "arun agent"], "ta": ["அருண் விநியோகஸ்தர்", "ஐஸ்கிரீம் விநியோகஸ்தர்"], "hi": ["अरुण डिस्ट्रीब्यूटर", "आइसक्रीम वाले"]}}
  ]
}
```

- [ ] **Step 2: Append the supplier phone variables to `.env.example`**

These are the WhatsApp test-recipient numbers in E.164 format; keep the real values in `.env` only.

```
SUPPLIER_PHONE_AAVIN_VENDOR=
SUPPLIER_PHONE_SAKTHI_VENDOR=
SUPPLIER_PHONE_ARUN_DISTRIBUTOR=
```

- [ ] **Step 3: If `services/live/aira_live/vision/identify.py` does not exist yet, create the shared models**

Copy these exactly from the interfaces file. Sarmitha's `sarmitha/01-vision-er2.md` extends this file, and they reconcile in review.

```python
# services/live/aira_live/vision/identify.py  — OWNER: Sarmitha. Models only until sarmitha/01 lands.
from pydantic import BaseModel


class ProductRef(BaseModel):
    sku: str
    name: str
    brand: str
    pack: str


class IdentifyResult(BaseModel):
    sku: str | None
    confidence: float
    alternatives: list[str]
```

Create `services/live/aira_live/vision/__init__.py` (empty) if it is missing.

- [ ] **Step 4: Write the shared fixture and the failing tests**

```python
# services/live/tests/business/conftest.py
from pathlib import Path

import pytest

from aira_live.business import seed
from aira_live.business.store import MemoryStore, set_store

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "content" / "catalog" / "murugan_dairy.json"


def pytest_configure(config):
    config.addinivalue_line("markers", "emulator: needs FIRESTORE_EMULATOR_HOST")


@pytest.fixture(autouse=True)
def published():
    """Fake analytics publisher: no test ever reaches aira_live.analytics. Yields [(topic, payload), ...]."""
    from aira_live.business import outbox

    sent: list[tuple[str, dict]] = []

    async def fake(topic: str, payload: dict) -> str:
        sent.append((topic, payload))
        return f"fake-{len(sent)}"

    outbox.set_publisher(fake)
    yield sent
    outbox.set_publisher(None)


@pytest.fixture
async def store():
    s = MemoryStore()
    set_store(s)
    await seed.seed_into(s, seed.load_catalog(CATALOG))
    yield s
    set_store(None)
```

```python
# services/live/tests/business/test_catalog.py
from pathlib import Path

import pytest

from aira_live.business import catalog, seed
from aira_live.business.store import MemoryStore

SHOP = "murugan_dairy"
CATALOG = Path(__file__).resolve().parents[4] / "content" / "catalog" / "murugan_dairy.json"


@pytest.mark.parametrize("spoken,lang,sku", [
    ("curd", "en", "sakthi_curd_1l"),
    ("half litre sakthi curd", "en", "sakthi_curd_500ml"),
    ("aavin milk", "en", "aavin_milk_500ml"),
    ("one litre aavin milk", "en", "aavin_milk_1l"),
    ("give me two litres of curd please", "en", "sakthi_curd_1l"),
    ("சக்தி தயிர் அரை லிட்டர்", "ta", "sakthi_curd_500ml"),
    ("दही", "hi", "sakthi_curd_1l"),
    ("ice cream", "en", "arun_icecream_box"),
    ("shakti curd", "en", "sakthi_curd_1l"),
])
async def test_resolve(store, spoken, lang, sku):
    ref = await catalog.resolve(SHOP, spoken, lang)
    assert ref is not None and ref.sku == sku


@pytest.mark.parametrize("spoken", ["paneer", ""])
async def test_resolve_none(store, spoken):
    assert await catalog.resolve(SHOP, spoken, "en") is None


async def test_seed_writes_products_and_suppliers(store):
    assert (await store.get(f"shops/{SHOP}"))["name"] == "Murugan Dairy"
    assert (await store.get(f"shops/{SHOP}/products/sakthi_curd_1l"))["price"] == 60
    assert (await store.get(f"shops/{SHOP}/suppliers/sakthi_vendor"))["channel"] == "whatsapp"
    assert (await store.get(f"shops/{SHOP}/suppliers/arun_distributor"))["channel"] == "call"


async def test_seed_reads_supplier_phones_from_env(monkeypatch):
    monkeypatch.setenv("SUPPLIER_PHONE_SAKTHI_VENDOR", "+919876543210")
    monkeypatch.delenv("SUPPLIER_PHONE_AAVIN_VENDOR", raising=False)
    s = MemoryStore()
    await seed.seed_into(s, seed.load_catalog(CATALOG))
    assert (await s.get(f"shops/{SHOP}/suppliers/sakthi_vendor"))["phone"] == "+919876543210"
    assert (await s.get(f"shops/{SHOP}/suppliers/aavin_vendor"))["phone"] == ""
    assert "phoneEnv" not in (await s.get(f"shops/{SHOP}/suppliers/sakthi_vendor"))


@pytest.mark.parametrize("spoken,supplier_id", [
    ("sakthi vendor", "sakthi_vendor"),
    ("milk vendor", "aavin_vendor"),
    ("ice cream", "arun_distributor"),
    ("paneer guy", None),
])
async def test_resolve_supplier(store, spoken, supplier_id):
    sup = await catalog.resolve_supplier(SHOP, spoken, "en")
    assert (sup["id"] if sup else None) == supplier_id
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest tests/business/test_catalog.py -q`
Expected: FAIL with `ImportError: cannot import name 'seed'`.

- [ ] **Step 6: Implement `catalog.py` and `seed.py`**

```python
# services/live/aira_live/business/catalog.py
"""Spoken product/supplier name (ta/hi/en, ASR-noisy) → ref. Ambiguity returns None so the tool asks
(tools speak hints.say("PRODUCT_AMBIGUOUS" | "PRODUCT_UNKNOWN" | "SUPPLIER_UNKNOWN", lang))."""
from difflib import SequenceMatcher

from aira_live.business import paths
from aira_live.business.store import get_store
from aira_live.business.text import tokens
from aira_live.vision.identify import ProductRef

_SIZE_HINTS: list[tuple[float, list[str]]] = [
    (0.5, ["half", "500", "500ml", "அரை", "आधा", "आधे"]),
    (1.0, ["1l", "one litre", "one liter", "1 litre", "1 liter", "ஒரு லிட்டர்", "एक लीटर"]),
]
_FILLER = {"litre", "liter", "litres", "liters", "l", "ml", "packet", "packets", "pack", "pouch", "box",
           "லிட்டர்", "பாக்கெட்", "लीटर", "पैकेट"}


def _size_hint(spoken: str) -> float | None:
    low = spoken.lower()
    toks = set(tokens(spoken))
    for size, hints in _SIZE_HINTS:
        for h in hints:
            if (" " in h and h in low) or h in toks:
                return size
    return None


def _token_hit(alias_tok: str, spoken_toks: list[str]) -> bool:
    return any(alias_tok == s or SequenceMatcher(None, alias_tok, s).ratio() >= 0.8 for s in spoken_toks)


def _alias_match(alias: str, spoken_toks: list[str]) -> int:
    """Return the alias length (in tokens) if every alias token is present, else 0."""
    a_toks = [t for t in tokens(alias) if t not in _FILLER]
    if not a_toks:
        return 0
    return len(a_toks) if all(_token_hit(t, spoken_toks) for t in a_toks) else 0


def _best(doc: dict, spoken_toks: list[str]) -> int:
    aliases = [doc["name"]] + [a for langs in doc.get("aliases", {}).values() for a in langs]
    return max((_alias_match(a, spoken_toks) for a in aliases), default=0)


def _spoken_tokens(spoken: str) -> list[str]:
    return [t for t in tokens(spoken or "") if t not in _FILLER]


async def resolve(shop_id: str, spoken: str, lang: str) -> ProductRef | None:
    spoken_toks = _spoken_tokens(spoken)
    if not spoken_toks:
        return None
    products = await get_store().list(paths.products(shop_id))
    scored = [(score, sku, p) for sku, p in products if (score := _best(p, spoken_toks))]
    if not scored:
        return None
    top = max(s[0] for s in scored)
    tied = [s for s in scored if s[0] == top]
    size = _size_hint(spoken)
    if size is not None:
        tied = [s for s in tied if s[2].get("packSizeL") == size] or tied
    if len(tied) > 1:
        defaults = [s for s in tied if s[2].get("defaultPack")]
        if size is None and len(defaults) == 1:
            tied = defaults
        else:
            return None
    _, sku, p = tied[0]
    return ProductRef(sku=sku, name=p["name"], brand=p["brand"], pack=p["pack"])


async def resolve_supplier(shop_id: str, spoken: str, lang: str) -> dict | None:
    spoken_toks = _spoken_tokens(spoken)
    if not spoken_toks:
        return None
    suppliers = await get_store().list(paths.suppliers(shop_id))
    scored = [(score, sid, s) for sid, s in suppliers if (score := _best(s, spoken_toks))]
    if scored:
        top = max(x[0] for x in scored)
        tied = [x for x in scored if x[0] == top]
        if len(tied) == 1:
            return {"id": tied[0][1], **tied[0][2]}
    ref = await resolve(shop_id, spoken, lang)               # "order ice cream" → the supplier of that product
    if ref is not None:
        matches = [(sid, s) for sid, s in suppliers if ref.sku in s.get("products", [])]
        if len(matches) == 1:
            return {"id": matches[0][0], **matches[0][1]}
    return None
```

```python
# services/live/aira_live/business/seed.py
"""Seed a shop (products + suppliers) into the store. Supplier phones come from env (never committed).
CLI writes to Firestore (the emulator if FIRESTORE_EMULATOR_HOST is set)."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from aira_live.business import paths
from aira_live.business.store import FirestoreStore, Store


def load_catalog(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def seed_into(store: Store, catalog: dict) -> None:
    shop = catalog["shop"]
    sid = shop["id"]
    await store.set(paths.shop(sid), {k: v for k, v in shop.items() if k != "id"})
    for p in catalog["products"]:
        await store.set(paths.product(sid, p["sku"]), {k: v for k, v in p.items() if k != "sku"})
    for s in catalog["suppliers"]:
        doc = {k: v for k, v in s.items() if k not in ("id", "phoneEnv")}
        doc["phone"] = os.getenv(s.get("phoneEnv", ""), "")
        await store.set(paths.supplier(sid, s["id"]), doc)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    args = ap.parse_args()
    asyncio.run(seed_into(FirestoreStore(), load_catalog(args.catalog)))
    print("seeded", args.catalog)


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest tests/business/test_catalog.py -q`
Expected: `17 passed`.

- [ ] **Step 8: Commit**

```bash
git add content/catalog/murugan_dairy.json .env.example services/live/aira_live/business/catalog.py \
        services/live/aira_live/business/seed.py services/live/tests/business/conftest.py \
        services/live/tests/business/test_catalog.py services/live/aira_live/vision
git commit -m "feat(business): Murugan Dairy seed catalog, ta/hi/en product + supplier resolution, env-based seeder"
```

---

### Task 4: Inventory ledger (idempotent, FIFO batches), expiry scan and audit log

**Files:**
- Create: `services/live/aira_live/business/ledger.py`, `expiry.py`, `audit.py`
- Modify: `services/live/aira_live/business/seed.py` (add `seed_inventory` and call it from the CLI)
- Test: `services/live/tests/business/test_ledger_expiry_audit.py`

**Interfaces:**
- Consumes: `paths`, `get_store`, `doc_id_for`, `clock`, `outbox.inventory_movement` (Task 1).
- Produces:
  - `ledger.apply(shop_id, sku, delta, reason, ref_type, ref_id, idem_key, *, expiry_date=None, today=None, actor="aira") -> dict` (the inventory document). After a **new** commit it publishes one `inventory-movements` event; a replay publishes nothing.
  - `ledger.on_hand(shop_id, sku) -> float`.
  - `ledger.prepare_movement(txn, ...) -> dict` and `ledger.commit_movement(txn, prep) -> dict`.
  - `ledger.InsufficientStock`.
  - `expiry.scan(shop_id, today) -> list[dict]`, with items `{sku, name, qty, daysLeft, batchId, expiryDate, status: "expired"|"expires_today"|"expires_tomorrow"}`.
  - `audit.log(...) -> str` and `audit.log_in_txn(txn, ...) -> str`.
  - `seed.seed_inventory(store, catalog, *, today=None) -> None`: seeds the catalog's optional `inventory` through the ledger (`ADJUST`, idem key `seed:{shop}:{sku}`); idempotent; publishes nothing.

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/business/test_ledger_expiry_audit.py
import asyncio
import os
from datetime import date
from pathlib import Path

import pytest

from aira_live.business import audit, expiry, ledger
from aira_live.business.ledger import InsufficientStock

SHOP = "murugan_dairy"
D0 = date(2026, 10, 12)
CATALOG = Path(__file__).resolve().parents[4] / "content" / "catalog" / "murugan_dairy.json"


async def test_delivery_adds_batch_with_shelf_life(store):
    inv = await ledger.apply(SHOP, "sakthi_curd_1l", 28, "DELIVERY", "delivery", "d1", "k1", today=D0)
    assert inv["onHand"] == 28
    assert inv["batches"][0]["expiryDate"] == "2026-10-16"            # shelfLifeDays 4
    led = [d for p, d in store.data.items() if p.startswith(f"shops/{SHOP}/ledger/")]
    assert len(led) == 1 and led[0]["reason"] == "DELIVERY" and led[0]["delta"] == 28


async def test_sale_consumes_fifo(store):
    await ledger.apply(SHOP, "sakthi_curd_1l", 5, "DELIVERY", "delivery", "d1", "k1",
                       expiry_date=date(2026, 10, 13), today=D0)
    await ledger.apply(SHOP, "sakthi_curd_1l", 5, "DELIVERY", "delivery", "d2", "k2",
                       expiry_date=date(2026, 10, 15), today=D0)
    inv = await ledger.apply(SHOP, "sakthi_curd_1l", -7, "SALE", "sale", "s1", "k3", today=D0)
    assert inv["onHand"] == 3
    assert [(b["qty"], b["expiryDate"]) for b in inv["batches"]] == [(3.0, "2026-10-15")]


async def test_negative_stock_raises(store):
    await ledger.apply(SHOP, "sakthi_curd_1l", 2, "DELIVERY", "delivery", "d1", "k1", today=D0)
    with pytest.raises(InsufficientStock):
        await ledger.apply(SHOP, "sakthi_curd_1l", -3, "SALE", "sale", "s1", "k2", today=D0)
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 2


async def test_idempotent_apply_returns_same_and_no_double(store):
    a = await ledger.apply(SHOP, "aavin_milk_500ml", 20, "DELIVERY", "delivery", "d1", "same", today=D0)
    b = await ledger.apply(SHOP, "aavin_milk_500ml", 20, "DELIVERY", "delivery", "d1", "same", today=D0)
    assert a == b and await ledger.on_hand(SHOP, "aavin_milk_500ml") == 20


async def test_invalid_reason_sign_raises(store):
    with pytest.raises(ValueError):
        await ledger.apply(SHOP, "aavin_milk_500ml", 5, "SALE", "sale", "s1", "k1", today=D0)
    with pytest.raises(ValueError):
        await ledger.apply(SHOP, "aavin_milk_500ml", -5, "DELIVERY", "delivery", "d1", "k2", today=D0)
    with pytest.raises(ValueError):
        await ledger.apply(SHOP, "aavin_milk_500ml", 5, "BOGUS", "x", "y", "k3", today=D0)


async def test_on_hand_unknown_sku_zero(store):
    assert await ledger.on_hand(SHOP, "never_stocked") == 0.0


async def test_expiry_scan_statuses(store):
    for i, d in enumerate([date(2026, 10, 11), date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 20)]):
        await ledger.apply(SHOP, "sakthi_curd_1l", 2, "DELIVERY", "delivery", f"d{i}", f"k{i}",
                           expiry_date=d, today=D0)
    alerts = await expiry.scan(SHOP, D0)
    assert [a["status"] for a in alerts] == ["expired", "expires_today", "expires_tomorrow"]
    assert [a["daysLeft"] for a in alerts] == [-1, 0, 1]
    assert all(a["sku"] == "sakthi_curd_1l" and a["name"] == "Sakthi Curd 1 L" and a["qty"] == 2 for a in alerts)


async def test_apply_publishes_inventory_movement_once(store, published):
    await ledger.apply(SHOP, "sakthi_curd_1l", 28, "DELIVERY", "delivery", "d1", "pub-k", today=D0)
    await ledger.apply(SHOP, "sakthi_curd_1l", 28, "DELIVERY", "delivery", "d1", "pub-k", today=D0)  # replay
    moves = [p for topic, p in published if topic == "inventory-movements"]
    assert len(moves) == 1
    assert moves[0]["shop_id"] == SHOP and moves[0]["sku"] == "sakthi_curd_1l" and moves[0]["delta"] == 28.0
    assert moves[0]["reason"] == "DELIVERY" and moves[0]["txn_id"].startswith("t_")


async def test_seed_inventory_idempotent(store, published):
    from aira_live.business.seed import load_catalog, seed_inventory
    catalog = load_catalog(CATALOG)
    await seed_inventory(store, catalog, today=D0)
    await seed_inventory(store, catalog, today=D0)
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 4
    assert await ledger.on_hand(SHOP, "aavin_milk_500ml") == 20
    assert published == []                                   # seeding is admin, not analytics


async def test_audit_log_writes(store):
    aid = await audit.log(SHOP, "owner", "order_confirmed", "order", "o1", "ok")
    doc = await store.get(f"shops/{SHOP}/auditLogs/{aid}")
    assert doc["actor"] == "owner" and doc["action"] == "order_confirmed" and doc["evidenceUri"] is None


@pytest.mark.emulator
@pytest.mark.skipif(not os.getenv("FIRESTORE_EMULATOR_HOST"), reason="needs Firestore emulator")
async def test_emulator_concurrent_same_idem_applies_once():
    from google.cloud import firestore
    from aira_live.business.seed import load_catalog, seed_into
    from aira_live.business.store import FirestoreStore, set_store

    fs = FirestoreStore(client=firestore.AsyncClient(project="demo-aira"))
    set_store(fs)
    await seed_into(fs, load_catalog(CATALOG))
    key = f"emu-{os.getpid()}"
    await asyncio.gather(*[
        ledger.apply(SHOP, "arun_icecream_box", 3, "DELIVERY", "delivery", "d-emu", key, today=D0)
        for _ in range(5)
    ])
    led = await fs.list(f"shops/{SHOP}/ledger")
    assert sum(1 for _, d in led if d["idemKey"] == key) == 1
    set_store(None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest tests/business/test_ledger_expiry_audit.py -q`
Expected: FAIL with `ImportError: cannot import name 'audit'`.

- [ ] **Step 3: Implement**

```python
# services/live/aira_live/business/ledger.py
"""Immutable stock ledger + inventory balance with FIFO batches. Idempotent; reads-before-writes."""
from datetime import date, timedelta
from typing import Literal

from aira_live.business import outbox, paths
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso, today_ist
from aira_live.business.store import Txn, get_store

Reason = Literal["DELIVERY", "SALE", "RETURN", "ADJUST", "EXPIRED"]
_REASONS = {"DELIVERY", "SALE", "RETURN", "ADJUST", "EXPIRED"}
_NEGATIVE_ONLY = {"SALE", "RETURN", "EXPIRED"}


class InsufficientStock(ValueError):
    pass


def _validate(delta: float, reason: str) -> None:
    if reason not in _REASONS:
        raise ValueError(f"unknown reason {reason!r}")
    if delta == 0:
        raise ValueError("delta must be non-zero")
    if reason in _NEGATIVE_ONLY and delta > 0:
        raise ValueError(f"{reason} must be negative")
    if reason == "DELIVERY" and delta < 0:
        raise ValueError("DELIVERY must be positive")


async def prepare_movement(txn: Txn, shop_id: str, sku: str, delta: float, reason: Reason, ref_type: str,
                           ref_id: str, idem_key: str, *, expiry_date: date | None = None,
                           today: date | None = None, actor: str = "aira") -> dict:
    """READS only. Returns a plan for commit_movement (or the cached result if already applied)."""
    _validate(delta, reason)
    seen = await txn.get(paths.idem(idem_key))
    if seen is not None:
        return {"done": True, "result": seen["result"]}
    product = await txn.get(paths.product(shop_id, sku))
    if product is None:
        raise KeyError(f"unknown sku {sku}")
    inv = await txn.get(paths.inventory(shop_id, sku)) or {"onHand": 0.0, "batches": []}
    today = today or today_ist()
    batches = [dict(b) for b in inv.get("batches", [])]
    if delta > 0:
        exp = expiry_date or (today + timedelta(days=int(product.get("shelfLifeDays") or 2)))
        batches.append({"batchId": doc_id_for("b", idem_key), "qty": float(delta), "expiryDate": exp.isoformat()})
    else:
        need = -float(delta)
        if need > float(inv.get("onHand", 0.0)) + 1e-9:
            raise InsufficientStock(f"{sku}: have {inv.get('onHand', 0.0)}, need {need}")
        remaining = []
        for b in sorted(batches, key=lambda x: x["expiryDate"]):
            if need <= 1e-9:
                remaining.append(b)
                continue
            take = min(float(b["qty"]), need)
            need = round(need - take, 3)
            left = round(float(b["qty"]) - take, 3)
            if left > 0:
                remaining.append({**b, "qty": left})
        batches = remaining
    at = now_iso()
    new_inv = {"onHand": round(sum(float(b["qty"]) for b in batches), 3), "batches": batches, "updatedAt": at}
    return {"done": False, "shop_id": shop_id, "sku": sku, "new_inv": new_inv,
            "ledger": {"sku": sku, "delta": float(delta), "reason": reason, "refType": ref_type, "refId": ref_id,
                       "idemKey": idem_key, "at": at, "actor": actor},
            "idem_key": idem_key}


def commit_movement(txn: Txn, prep: dict) -> dict:
    """WRITES only."""
    if prep["done"]:
        return prep["result"]
    txn.set(paths.inventory(prep["shop_id"], prep["sku"]), prep["new_inv"])
    txn.set(paths.ledger(prep["shop_id"], doc_id_for("t", prep["idem_key"])), prep["ledger"])
    txn.set(paths.idem(prep["idem_key"]),
            {"key": prep["idem_key"], "result": prep["new_inv"], "at": prep["ledger"]["at"]})
    return prep["new_inv"]


async def publish_movement(prep: dict) -> None:
    """Post-commit analytics for one movement; no-op for replays."""
    if prep["done"]:
        return
    led = prep["ledger"]
    await outbox.inventory_movement(prep["shop_id"], doc_id_for("t", prep["idem_key"]), led["sku"], led["delta"],
                                    led["reason"])


async def apply(shop_id: str, sku: str, delta: float, reason: Reason, ref_type: str, ref_id: str, idem_key: str,
                *, expiry_date: date | None = None, today: date | None = None, actor: str = "aira") -> dict:
    async def fn(txn: Txn) -> tuple[dict, dict]:
        prep = await prepare_movement(txn, shop_id, sku, delta, reason, ref_type, ref_id, idem_key,
                                      expiry_date=expiry_date, today=today, actor=actor)
        return prep, commit_movement(txn, prep)
    prep, result = await get_store().run(fn)
    await publish_movement(prep)                                 # after commit, outside the transaction
    return result


async def on_hand(shop_id: str, sku: str) -> float:
    inv = await get_store().get(paths.inventory(shop_id, sku))
    return float(inv["onHand"]) if inv else 0.0
```

```python
# services/live/aira_live/business/expiry.py
from datetime import date

from aira_live.business import paths
from aira_live.business.store import get_store


async def scan(shop_id: str, today: date) -> list[dict]:
    store = get_store()
    names = {sku: p.get("name", sku) for sku, p in await store.list(paths.products(shop_id))}
    alerts = []
    for sku, inv in await store.list(paths.inventories(shop_id)):
        for b in inv.get("batches", []):
            days = (date.fromisoformat(b["expiryDate"]) - today).days
            status = ("expired" if days < 0 else "expires_today" if days == 0
                      else "expires_tomorrow" if days == 1 else None)
            if status:
                alerts.append({"sku": sku, "name": names.get(sku, sku), "qty": float(b["qty"]), "daysLeft": days,
                               "batchId": b["batchId"], "expiryDate": b["expiryDate"], "status": status})
    return sorted(alerts, key=lambda a: (a["daysLeft"], a["sku"]))
```

```python
# services/live/aira_live/business/audit.py
import uuid
from typing import Literal

from aira_live.business import paths
from aira_live.business.clock import now_iso
from aira_live.business.store import Txn, get_store

Actor = Literal["owner", "aira", "system"]


def _doc(actor, action, ref_type, ref_id, outcome, evidence_uri):
    if actor not in ("owner", "aira", "system"):
        raise ValueError(f"bad actor {actor!r}")
    return {"actor": actor, "action": action, "refType": ref_type, "refId": ref_id, "outcome": outcome,
            "evidenceUri": evidence_uri, "at": now_iso()}


async def log(shop_id: str, actor: Actor, action: str, ref_type: str, ref_id: str, outcome: str,
              evidence_uri: str | None = None) -> str:
    aid = uuid.uuid4().hex
    await get_store().set(paths.audit(shop_id, aid), _doc(actor, action, ref_type, ref_id, outcome, evidence_uri))
    return aid


def log_in_txn(txn: Txn, shop_id: str, actor: Actor, action: str, ref_type: str, ref_id: str, outcome: str,
               evidence_uri: str | None = None) -> str:
    aid = uuid.uuid4().hex
    txn.set(paths.audit(shop_id, aid), _doc(actor, action, ref_type, ref_id, outcome, evidence_uri))
    return aid
```

Add `seed_inventory` to `seed.py`, and make the CLI seed the starting stock too:

```python
# services/live/aira_live/business/seed.py  — additions
from datetime import date

from aira_live.business import ledger


async def seed_inventory(store: Store, catalog: dict, *, today: date | None = None) -> None:
    """Starting stock through the ledger (ADJUST, idempotent per shop+sku). Admin action: no analytics events."""
    sid = catalog["shop"]["id"]
    for row in catalog.get("inventory", []):
        async def fn(txn, row=row):
            exp = date.fromisoformat(row["expiryDate"]) if row.get("expiryDate") else None
            prep = await ledger.prepare_movement(txn, sid, row["sku"], float(row["qty"]), "ADJUST", "seed", "seed",
                                                 f"seed:{sid}:{row['sku']}", expiry_date=exp, today=today,
                                                 actor="system")
            return ledger.commit_movement(txn, prep)
        await store.run(fn)
```

Then change `main()` so it calls both:

```python
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    args = ap.parse_args()
    store, catalog = FirestoreStore(), load_catalog(args.catalog)

    async def run():
        await seed_into(store, catalog)
        await seed_inventory(store, catalog)

    asyncio.run(run())
    print("seeded", args.catalog)
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `cd services/live && uv run pytest tests/business/test_ledger_expiry_audit.py -q`
Expected: `10 passed, 1 skipped`.

- [ ] **Step 5: Run the emulator test**

You need Java 11+ and `firebase-tools` (or the gcloud Firestore emulator).

```bash
# terminal 1
npx firebase-tools emulators:start --only firestore --project demo-aira
# terminal 2  (use the port printed by the emulator; default 8080)
cd services/live && FIRESTORE_EMULATOR_HOST=localhost:8080 uv run pytest tests/business -m emulator -q
```

Expected: `1 passed`. Five concurrent applies with one idem key produce exactly one ledger document. If the test fails with "reads after writes", `prepare_movement` is writing; keep every write in `commit_movement`.

- [ ] **Step 6: Commit**

```bash
git add services/live/aira_live/business/ledger.py services/live/aira_live/business/expiry.py \
        services/live/aira_live/business/audit.py services/live/aira_live/business/seed.py \
        services/live/tests/business/test_ledger_expiry_audit.py
git commit -m "feat(business): idempotent FIFO stock ledger with post-commit events, expiry scan, audit log, seeded stock"
```

---

### Task 5: Payments and sales (stock deducted exactly once)

**Files:**
- Create: `services/live/aira_live/business/payments.py`, `services/live/aira_live/business/sales.py`
- Test: `services/live/tests/business/test_payments_sales.py`

**Interfaces:**
- Consumes: `ledger.prepare_movement`/`commit_movement`, `states.transition`, `audit.log_in_txn`, `money.change_due`, `doc_id_for`.
- Produces:
  - `payments.record(shop_id, direction, method, amount, ref_type, ref_id, idem_key) -> dict` (`PENDING`, deterministic `id`).
  - `payments.confirm(shop_id, payment_id, evidence) -> dict`. Evidence `type` is one of `cash_count | soundbox | user_confirm`:
    - **soundbox:** `amount` must equal the payment amount, and the payment must be incoming UPI.
    - **cash_count:** an outgoing payment's `total` must equal the amount; an incoming payment's `total` (tendered) must be ≥ the amount. The stored evidence gets `change`.
  - `payments.fail(...)`, `payments.PaymentMismatch`.
  - `sales.create(shop_id, items, idem_key) -> dict` (`SALE_PENDING`; priced from `products/{sku}.price`; duplicate SKUs aggregated; `createdAt` = server timestamp).
  - `sales.complete(shop_id, sale_id, payment_id, idem_key) -> dict` (`SALE_COMPLETED`; deducts once; `completedAt` = server timestamp). After the **first** completion it publishes one `sales` event per line plus one `inventory-movements` event per line; retries publish nothing.
  - `sales.void(...)`, `sales.SaleError`.

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/business/test_payments_sales.py
from datetime import date

import pytest

from aira_live.business import ledger, payments, sales
from aira_live.business.ledger import InsufficientStock
from aira_live.business.payments import PaymentMismatch
from aira_live.business.sales import SaleError

SHOP = "murugan_dairy"
D0 = date(2026, 10, 12)


async def _stock(n=10):
    await ledger.apply(SHOP, "sakthi_curd_1l", n, "DELIVERY", "delivery", "d1", "stock-k", today=D0)


async def test_record_payment_idempotent(store):
    a = await payments.record(SHOP, "in", "cash", 120, "sale", "s1", "pay-k")
    b = await payments.record(SHOP, "in", "cash", 120, "sale", "s1", "pay-k")
    assert a["id"] == b["id"] and a["state"] == "PENDING"
    assert len([p for p in store.data if p.startswith(f"shops/{SHOP}/payments/")]) == 1


async def test_confirm_upi_soundbox_amount_must_match(store):
    p = await payments.record(SHOP, "in", "upi", 120, "sale", "s1", "k")
    with pytest.raises(PaymentMismatch):
        await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 100})
    ok = await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 120, "provider": "Paytm"})
    assert ok["state"] == "CONFIRMED" and ok["evidence"]["type"] == "soundbox"


async def test_confirm_cash_out_exact(store):
    p = await payments.record(SHOP, "out", "cash", 1400, "order", "o1", "k")
    with pytest.raises(PaymentMismatch):
        await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 1500})
    assert (await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 1400}))["state"] == "CONFIRMED"


async def test_confirm_cash_in_tendered_at_least_total(store):
    p = await payments.record(SHOP, "in", "cash", 120, "sale", "s1", "k")
    with pytest.raises(PaymentMismatch):
        await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 100})
    c = await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 200})
    assert c["state"] == "CONFIRMED" and c["evidence"]["change"] == 80


async def test_confirm_idempotent(store):
    p = await payments.record(SHOP, "in", "upi", 60, "sale", "s1", "k")
    a = await payments.confirm(SHOP, p["id"], {"type": "user_confirm"})
    b = await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 999})   # already confirmed → unchanged
    assert a == b


async def test_sale_create_prices_from_catalog_and_aggregates(store):
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 1}, {"sku": "sakthi_curd_1l", "qty": 1}], "sale-k")
    assert s["state"] == "SALE_PENDING" and s["total"] == 120
    assert s["items"] == [{"sku": "sakthi_curd_1l", "qty": 2.0, "unitPrice": 60, "lineTotal": 120}]


async def test_sale_create_insufficient_stock(store):
    await _stock(1)
    with pytest.raises(InsufficientStock):
        await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    assert not [p for p in store.data if p.startswith(f"shops/{SHOP}/sales/")]


async def test_sale_complete_requires_confirmed_payment(store):
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    p = await payments.record(SHOP, "in", "cash", 120, "sale", s["id"], "pay-k")
    with pytest.raises(SaleError):
        await sales.complete(SHOP, s["id"], p["id"], "complete-k")
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 10


async def test_sale_complete_deducts_once_on_retry(store):
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    p = await payments.record(SHOP, "in", "cash", 120, "sale", s["id"], "pay-k")
    await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 200})
    done = await sales.complete(SHOP, s["id"], p["id"], "complete-k")
    assert done["state"] == "SALE_COMPLETED"
    await sales.complete(SHOP, s["id"], p["id"], "complete-k")        # same key retry
    await sales.complete(SHOP, s["id"], p["id"], "complete-k-2")      # new key, same sale
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 8


async def test_sale_timestamps(store):
    from datetime import datetime
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 1}], "ts-sale")
    assert isinstance(s["createdAt"], datetime) and "completedAt" not in s
    p = await payments.record(SHOP, "in", "upi", 60, "sale", s["id"], "ts-pay")
    await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 60})
    done = await sales.complete(SHOP, s["id"], p["id"], "ts-complete")
    assert isinstance(done["completedAt"], datetime)


async def test_sale_complete_publishes_once(store, published):
    await _stock()
    published.clear()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "pub-sale")
    p = await payments.record(SHOP, "in", "upi", 120, "sale", s["id"], "pub-pay")
    await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 120})
    await sales.complete(SHOP, s["id"], p["id"], "pub-complete")
    await sales.complete(SHOP, s["id"], p["id"], "pub-complete")                   # retry → no new events
    sales_msgs = [m for t, m in published if t == "sales"]
    moves = [m for t, m in published if t == "inventory-movements"]
    assert len(sales_msgs) == 1 and len(moves) == 1
    assert sales_msgs[0] == {"shop_id": SHOP, "sale_id": s["id"], "sku": "sakthi_curd_1l", "qty": 2.0,
                             "unit_price": 60, "ts": sales_msgs[0]["ts"]}
    assert moves[0]["delta"] == -2.0 and moves[0]["reason"] == "SALE"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest tests/business/test_payments_sales.py -q`
Expected: FAIL with `ImportError: cannot import name 'payments'`.

- [ ] **Step 3: Implement**

```python
# services/live/aira_live/business/payments.py
"""Payment records. Cash/UPI only. Confirmed only with evidence."""
from typing import Literal

from aira_live.business import paths
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso
from aira_live.business.money import InsufficientTender, change_due
from aira_live.business.states import transition
from aira_live.business.store import get_store

_EVIDENCE = {"cash_count", "soundbox", "user_confirm"}


class PaymentMismatch(ValueError):
    pass


async def record(shop_id: str, direction: Literal["in", "out"], method: Literal["cash", "upi"], amount: int,
                 ref_type: str, ref_id: str, idem_key: str) -> dict:
    if direction not in ("in", "out") or method not in ("cash", "upi"):
        raise ValueError("direction must be in/out and method cash/upi")
    if amount <= 0 or int(amount) != amount:
        raise ValueError("amount must be a positive whole number of rupees")
    pid = doc_id_for("p", idem_key)
    path = paths.payment(shop_id, pid)

    async def fn(txn):
        existing = await txn.get(path)
        if existing is not None:
            return existing
        at = now_iso()
        doc = {"id": pid, "direction": direction, "method": method, "amount": int(amount), "state": "PENDING",
               "evidence": None, "refType": ref_type, "refId": ref_id, "idemKey": idem_key,
               "createdAt": at, "updatedAt": at}
        txn.set(path, doc)
        return doc
    return await get_store().run(fn)


def _check_evidence(p: dict, evidence: dict) -> dict:
    etype = evidence.get("type")
    if etype not in _EVIDENCE:
        raise ValueError(f"evidence type must be one of {_EVIDENCE}")
    ev = dict(evidence)
    if etype == "soundbox":
        if p["method"] != "upi" or p["direction"] != "in":
            raise PaymentMismatch("soundbox evidence only confirms incoming UPI")
        if int(evidence.get("amount", -1)) != p["amount"]:
            raise PaymentMismatch(f"soundbox said {evidence.get('amount')}, expected {p['amount']}")
    if etype == "cash_count":
        if p["method"] != "cash":
            raise PaymentMismatch("cash_count evidence only for cash")
        total = int(evidence.get("total", -1))
        if p["direction"] == "out" and total != p["amount"]:
            raise PaymentMismatch(f"counted {total}, must pay exactly {p['amount']}")
        if p["direction"] == "in":
            try:
                ev["change"] = change_due(p["amount"], total)
            except InsufficientTender as e:
                raise PaymentMismatch(str(e)) from e
    return ev


async def confirm(shop_id: str, payment_id: str, evidence: dict) -> dict:
    path = paths.payment(shop_id, payment_id)

    async def fn(txn):
        p = await txn.get(path)
        if p is None:
            raise KeyError(f"payment {payment_id} not found")
        if p["state"] == "CONFIRMED":
            return p
        ev = _check_evidence(p, evidence)
        p = {**p, "state": transition("payment", p["state"], "CONFIRMED"), "evidence": ev, "updatedAt": now_iso()}
        txn.set(path, p)
        return p
    return await get_store().run(fn)


async def fail(shop_id: str, payment_id: str, reason: str) -> dict:
    path = paths.payment(shop_id, payment_id)

    async def fn(txn):
        p = await txn.get(path)
        if p is None:
            raise KeyError(f"payment {payment_id} not found")
        p = {**p, "state": transition("payment", p["state"], "FAILED"), "failReason": reason, "updatedAt": now_iso()}
        txn.set(path, p)
        return p
    return await get_store().run(fn)
```

```python
# services/live/aira_live/business/sales.py
"""Sales: price from catalog, deduct stock exactly once on completion, publish analytics after commit."""
from aira_live.business import audit, ledger, outbox, paths
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso
from aira_live.business.ledger import InsufficientStock
from aira_live.business.states import transition
from aira_live.business.store import get_store


class SaleError(ValueError):
    pass


async def create(shop_id: str, items: list[dict], idem_key: str) -> dict:
    agg: dict[str, float] = {}
    for it in items:
        qty = float(it["qty"])
        if qty <= 0:
            raise SaleError("quantity must be positive")
        agg[it["sku"]] = agg.get(it["sku"], 0.0) + qty
    if not agg:
        raise SaleError("empty sale")
    sid = doc_id_for("s", idem_key)
    path = paths.sale(shop_id, sid)
    store = get_store()

    async def fn(txn):
        existing = await txn.get(path)
        if existing is not None:
            return existing
        lines = []
        for sku, qty in agg.items():
            prod = await txn.get(paths.product(shop_id, sku))
            if prod is None:
                raise KeyError(f"unknown sku {sku}")
            inv = await txn.get(paths.inventory(shop_id, sku)) or {"onHand": 0.0}
            if float(inv["onHand"]) < qty:
                raise InsufficientStock(f"{sku}: have {inv['onHand']}, need {qty}")
            unit = int(prod["price"])
            lines.append({"sku": sku, "qty": qty, "unitPrice": unit, "lineTotal": int(round(unit * qty))})
        doc = {"id": sid, "items": lines, "total": sum(l["lineTotal"] for l in lines), "state": "SALE_PENDING",
               "paymentId": None, "idemKey": idem_key, "createdAt": store.server_ts(), "updatedAt": now_iso()}
        txn.set(path, doc)
        return doc
    return await store.run(fn)


async def complete(shop_id: str, sale_id: str, payment_id: str, idem_key: str) -> dict:
    spath, ppath = paths.sale(shop_id, sale_id), paths.payment(shop_id, payment_id)
    store = get_store()

    async def fn(txn):
        sale = await txn.get(spath)                                  # ---- reads ----
        if sale is None:
            raise KeyError(f"sale {sale_id} not found")
        if sale["state"] == "SALE_COMPLETED":
            return sale, []                                          # already done: no new events
        pay = await txn.get(ppath)
        if pay is None or pay["state"] != "CONFIRMED" or pay["direction"] != "in":
            raise SaleError("payment not confirmed")
        if pay["amount"] != sale["total"]:
            raise SaleError(f"payment {pay['amount']} != sale total {sale['total']}")
        preps = [await ledger.prepare_movement(txn, shop_id, line["sku"], -float(line["qty"]), "SALE", "sale",
                                               sale_id, f"{idem_key}:{line['sku']}")
                 for line in sale["items"]]
        for prep in preps:                                           # ---- writes ----
            ledger.commit_movement(txn, prep)
        sale = {**sale, "state": transition("sale", sale["state"], "SALE_COMPLETED"), "paymentId": payment_id,
                "completedAt": store.server_ts(), "updatedAt": now_iso()}
        txn.set(spath, sale)
        audit.log_in_txn(txn, shop_id, "aira", "sale_completed", "sale", sale_id, "ok")
        return sale, preps

    sale, preps = await store.run(fn)
    if preps:                                                        # ---- after commit, outside the txn ----
        await outbox.sale_lines(shop_id, sale_id, sale["items"])
        for prep in preps:
            await ledger.publish_movement(prep)
    return sale


async def void(shop_id: str, sale_id: str) -> dict:
    spath = paths.sale(shop_id, sale_id)

    async def fn(txn):
        sale = await txn.get(spath)
        if sale is None:
            raise KeyError(f"sale {sale_id} not found")
        sale = {**sale, "state": transition("sale", sale["state"], "SALE_VOID"), "updatedAt": now_iso()}
        txn.set(spath, sale)
        return sale
    return await get_store().run(fn)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest tests/business/test_payments_sales.py -q`
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add services/live/aira_live/business/payments.py services/live/aira_live/business/sales.py \
        services/live/tests/business/test_payments_sales.py
git commit -m "feat(business): evidence-checked payments and exactly-once sale completion"
```

---

### Task 6: Delivery reconciliation, spoken-hint keys (i18n) and the full engine scenario

**Files:**
- Create: `services/live/aira_live/business/reconcile.py`, `services/live/aira_live/business/hints.py`
- Modify: `services/live/aira_live/i18n/en.json`, `ta.json`, `hi.json` (merge in the `biz.*` keys)
- Create **only if absent**: `services/live/aira_live/vision/invoice.py` (models only; Sarmitha owns it)
- Test: `services/live/tests/business/test_reconcile_scenario.py`, `services/live/tests/business/test_hints.py`

**Interfaces:**
- Consumes: `vision.invoice.InvoiceRead`/`InvoiceLine` (interfaces), `aira_live.i18n.t(key, lang, **kw)` (foundation), and everything from Tasks 1–5.
- Produces:
  - `reconcile.ReconcileResult(status, discrepancies)`.
  - `reconcile.compare(order_items, counted, invoice)`, where `order_items` items are `{sku, name, qty, agreedPrice?}`. Discrepancy `kind`s: `SHORT_DELIVERY`, `OVER_DELIVERY`, `UNORDERED_ITEM`, `INVOICE_UNREADABLE`, `UNKNOWN_INVOICE_LINE`, `INVOICE_QTY_MISMATCH`, `PRICE_MISMATCH`, `LINE_TOTAL_MISMATCH`, `TOTAL_MISMATCH`.
  - `reconcile.payable(order_items, counted) -> int`.
  - `hints.HINTS: dict[str, str]` (code → i18n key), `hints.key_for(code) -> str`, `hints.say(code, lang, **kw) -> str`, which returns `aira_live.i18n.t(key, lang, **kw)`.
  - Codes: every discrepancy kind, plus `InsufficientStock`, `InsufficientTender`, `PaymentMismatch`, `InvalidTransition`, `PRODUCT_AMBIGUOUS`, `PRODUCT_UNKNOWN`, `SUPPLIER_UNKNOWN`, `SOUNDBOX_UNSURE`.
  - Placeholders used by the keys: `{product}`, `{have}`, `{short}`, `{expected}`, `{actual}`, `{name}`, `{options}`.

- [ ] **Step 1: If `services/live/aira_live/vision/invoice.py` does not exist yet, create the shared models**

```python
# services/live/aira_live/vision/invoice.py  — OWNER: Sarmitha. Models only until sarmitha/02 lands.
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

- [ ] **Step 2: Merge the `biz.*` keys into the i18n catalogs**

Add each block into the existing top-level JSON object of the matching file. Do not remove other owners' keys. The placeholders use Python `str.format` names, as the foundation's `t()` formats with `**kw`. If the foundation catalog nests keys instead of using flat dotted keys, nest these under `biz` accordingly.

`services/live/aira_live/i18n/en.json`:

```json
{
  "biz.stock.insufficient": "Only {have} of {product} in stock.",
  "biz.cash.short": "That is {short} rupees short.",
  "biz.payment.mismatch": "The payment does not match. Please check again.",
  "biz.state.invalid": "That step cannot be done now.",
  "biz.delivery.short": "{product}: ordered {expected}, counted {actual}. Short delivery.",
  "biz.delivery.over": "{product}: ordered {expected}, counted {actual}. Extra items.",
  "biz.delivery.unordered": "{product} was not in the order.",
  "biz.invoice.unreadable": "I could not read the bill clearly. Please show it again.",
  "biz.invoice.unknown_line": "The bill has an item I did not order: {name}.",
  "biz.invoice.qty_mismatch": "Bill says {actual} {product}, but {expected} arrived.",
  "biz.invoice.price_mismatch": "Bill price for {product} is {actual} rupees, agreed price is {expected}.",
  "biz.invoice.line_total_mismatch": "The line total for {product} is wrong on the bill.",
  "biz.invoice.total_mismatch": "Bill total is {actual} rupees, but the items add up to {expected}.",
  "biz.product.ambiguous": "Which one: {options}?",
  "biz.product.unknown": "I don't know that product.",
  "biz.supplier.unknown": "I don't know that vendor.",
  "biz.upi.unsure": "I did not hear the payment confirmation clearly."
}
```

`services/live/aira_live/i18n/ta.json`:

```json
{
  "biz.stock.insufficient": "{product} கையிருப்பில் {have} மட்டுமே உள்ளது.",
  "biz.cash.short": "{short} ரூபாய் குறைவாக உள்ளது.",
  "biz.payment.mismatch": "பணம் பொருந்தவில்லை. மீண்டும் சரிபார்க்கவும்.",
  "biz.state.invalid": "இந்த படியை இப்போது செய்ய முடியாது.",
  "biz.delivery.short": "{product}: ஆர்டர் {expected}, எண்ணியது {actual}. குறைவாக வந்துள்ளது.",
  "biz.delivery.over": "{product}: ஆர்டர் {expected}, எண்ணியது {actual}. கூடுதலாக வந்துள்ளது.",
  "biz.delivery.unordered": "{product} ஆர்டரில் இல்லை.",
  "biz.invoice.unreadable": "பில்லை தெளிவாக படிக்க முடியவில்லை. மீண்டும் காட்டுங்கள்.",
  "biz.invoice.unknown_line": "ஆர்டர் செய்யாத பொருள் பில்லில் உள்ளது: {name}.",
  "biz.invoice.qty_mismatch": "பில்லில் {actual} {product} என்று உள்ளது, ஆனால் {expected} தான் வந்தது.",
  "biz.invoice.price_mismatch": "{product} பில் விலை {actual} ரூபாய், ஒப்புக்கொண்ட விலை {expected}.",
  "biz.invoice.line_total_mismatch": "பில்லில் {product} மொத்தம் தவறாக உள்ளது.",
  "biz.invoice.total_mismatch": "பில் மொத்தம் {actual} ரூபாய், ஆனால் பொருட்களின் கூட்டு {expected}.",
  "biz.product.ambiguous": "எது வேண்டும்: {options}?",
  "biz.product.unknown": "அந்த பொருள் எனக்கு தெரியவில்லை.",
  "biz.supplier.unknown": "அந்த விற்பனையாளர் எனக்கு தெரியவில்லை.",
  "biz.upi.unsure": "பணம் வந்த அறிவிப்பு தெளிவாக கேட்கவில்லை."
}
```

`services/live/aira_live/i18n/hi.json`:

```json
{
  "biz.stock.insufficient": "{product} का स्टॉक सिर्फ़ {have} है।",
  "biz.cash.short": "{short} रुपये कम हैं।",
  "biz.payment.mismatch": "भुगतान मेल नहीं खाता। कृपया फिर से जाँचें।",
  "biz.state.invalid": "यह कदम अभी नहीं हो सकता।",
  "biz.delivery.short": "{product}: ऑर्डर {expected}, गिने {actual}। कम आया है।",
  "biz.delivery.over": "{product}: ऑर्डर {expected}, गिने {actual}। ज़्यादा आया है।",
  "biz.delivery.unordered": "{product} ऑर्डर में नहीं था।",
  "biz.invoice.unreadable": "बिल साफ़ नहीं पढ़ पाई। कृपया फिर से दिखाइए।",
  "biz.invoice.unknown_line": "बिल में ऐसा सामान है जो ऑर्डर नहीं किया: {name}।",
  "biz.invoice.qty_mismatch": "बिल में {actual} {product} लिखा है, पर {expected} आए हैं।",
  "biz.invoice.price_mismatch": "{product} का बिल रेट {actual} रुपये है, तय रेट {expected} है।",
  "biz.invoice.line_total_mismatch": "बिल में {product} का जोड़ गलत है।",
  "biz.invoice.total_mismatch": "बिल का कुल {actual} रुपये है, पर सामान का जोड़ {expected} है।",
  "biz.product.ambiguous": "कौन सा: {options}?",
  "biz.product.unknown": "यह सामान मुझे नहीं पता।",
  "biz.supplier.unknown": "यह विक्रेता मुझे नहीं पता।",
  "biz.upi.unsure": "भुगतान की आवाज़ साफ़ नहीं सुनाई दी।"
}
```

- [ ] **Step 3: Write the failing tests**

```python
# services/live/tests/business/test_hints.py
import json
from pathlib import Path

from aira_live.business import hints

I18N = Path(__file__).resolve().parents[2] / "aira_live" / "i18n"


def _flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flat(v, key + "."))
        else:
            out[key] = v
    return out


def test_every_hint_key_in_all_languages():
    for lang in ("en", "ta", "hi"):
        catalog = _flat(json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8")))
        missing = [k for k in hints.HINTS.values() if k not in catalog]
        assert not missing, f"{lang} missing {missing}"


def test_say_formats_through_i18n():
    s = hints.say("SHORT_DELIVERY", "en", product="Sakthi Curd 1 L", expected=30, actual=28)
    assert s == "Sakthi Curd 1 L: ordered 30, counted 28. Short delivery."
    assert hints.key_for("INVOICE_UNREADABLE") == "biz.invoice.unreadable"
```

```python
# services/live/tests/business/test_reconcile_scenario.py
from datetime import date

from aira_live.business import ledger, money, payments, reconcile, sales
from aira_live.business.states import transition
from aira_live.vision.invoice import InvoiceLine, InvoiceRead

SHOP = "murugan_dairy"
ORDER = [{"sku": "sakthi_curd_1l", "name": "Sakthi Curd 1 L", "qty": 30, "agreedPrice": 50}]


def inv(qty=30, unit_price=50, line_total=None, total=None, agreed=True):
    lt = qty * unit_price if line_total is None else line_total
    return InvoiceRead(supplier="Sakthi", date="2026-10-12",
                       lines=[InvoiceLine(name="SAKTHI CURD 1L", qty=qty, unit="L", unit_price=unit_price,
                                          line_total=lt)],
                       total=lt if total is None else total, agreed=agreed,
                       disagreements=[] if agreed else ["line 1 qty"])


def kinds(r):
    return sorted(d["kind"] for d in r.discrepancies)


def test_compare_match():
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, inv())
    assert r.status == "match" and r.discrepancies == []


def test_compare_short_and_invoice_mismatch():
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 28}, inv(qty=30))
    assert r.status == "discrepancy"
    assert kinds(r) == ["INVOICE_QTY_MISMATCH", "SHORT_DELIVERY"]


def test_compare_price_and_total_mismatch():
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, inv(unit_price=55, total=1700))
    assert kinds(r) == ["PRICE_MISMATCH", "TOTAL_MISMATCH"]


def test_compare_unreadable_invoice():
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, inv(agreed=False))
    assert r.status == "discrepancy" and kinds(r) == ["INVOICE_UNREADABLE"]


def test_payable_counts_only_received():
    assert reconcile.payable(ORDER, {"sakthi_curd_1l": 28}) == 1400
    assert reconcile.payable(ORDER, {"sakthi_curd_1l": 31}) == 1500


async def test_outbox_discrepancies_and_order_state(published):
    from aira_live.business import outbox
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 28}, inv(qty=30))
    await outbox.discrepancies(SHOP, "del_1", r)
    await outbox.order_state(SHOP, "ord_1", "DISCREPANCY")
    disc = [m for t, m in published if t == "discrepancies"]
    assert {m["field"] for m in disc} == {"SHORT_DELIVERY:sakthi_curd_1l", "INVOICE_QTY_MISMATCH:sakthi_curd_1l"}
    assert all(m["shop_id"] == SHOP and m["delivery_id"] == "del_1" for m in disc)
    short = next(m for m in disc if m["field"].startswith("SHORT_DELIVERY"))
    assert (short["expected"], short["actual"]) == (30.0, 28.0)
    assert ("orders", {"shop_id": SHOP, "order_id": "ord_1", "state": "DISCREPANCY"}) in published


async def test_full_scenario_order_30_count_28(store):
    d0 = date(2026, 10, 12)
    state = transition("order", "DRAFT", "ORDER_PLACED")
    state = transition("order", state, "DELIVERY_PENDING")

    counted = {"sakthi_curd_1l": 28}
    r = reconcile.compare(ORDER, counted, inv(qty=30))
    assert r.status == "discrepancy"
    state = transition("order", state, "DISCREPANCY")

    await ledger.apply(SHOP, "sakthi_curd_1l", 28, "DELIVERY", "delivery", "d1", "k-deliv", today=d0)
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 28

    state = transition("order", state, "PAYMENT_PENDING")
    due = reconcile.payable(ORDER, counted)
    assert due == 1400 and money.notes_for(due) == [{"denomination": 500, "count": 2},
                                                    {"denomination": 200, "count": 2}]
    vp = await payments.record(SHOP, "out", "cash", due, "order", "o1", "k-pay-vendor")
    vp = await payments.confirm(SHOP, vp["id"], {"type": "cash_count", "total": 1400})
    assert vp["state"] == "CONFIRMED"
    state = transition("order", state, "PAYMENT_CONFIRMED")

    sale = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "k-sale")
    assert sale["total"] == 120
    cp = await payments.record(SHOP, "in", "cash", sale["total"], "sale", sale["id"], "k-pay-cust")
    cp = await payments.confirm(SHOP, cp["id"], {"type": "cash_count", "total": 200})
    assert cp["evidence"]["change"] == 80
    await sales.complete(SHOP, sale["id"], cp["id"], "k-sale-complete")
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 26

    await sales.complete(SHOP, sale["id"], cp["id"], "k-sale-complete")       # retried tool call
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 26
    assert transition("order", state, "CLOSED") == "CLOSED"
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest tests/business/test_reconcile_scenario.py tests/business/test_hints.py -q`
Expected: FAIL with `ImportError: cannot import name 'reconcile'` / `'hints'`.

- [ ] **Step 5: Implement `reconcile.py` and `hints.py`**

```python
# services/live/aira_live/business/reconcile.py
"""Order vs physically counted vs paper invoice. An invoice is never proof of receipt."""
from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel

from aira_live.business.text import tokens
from aira_live.vision.invoice import InvoiceRead

_UNIT_WORDS = {"l", "ltr", "litre", "liter", "ml", "pkt", "packet", "pouch", "pc", "pcs", "nos", "box"}


class ReconcileResult(BaseModel):
    status: Literal["match", "discrepancy"]
    discrepancies: list[dict]


def _norm(name: str) -> str:
    return " ".join(t for t in tokens(name) if t not in _UNIT_WORDS)


def _match_line(line_name: str, order_items: list[dict]) -> dict | None:
    target = _norm(line_name)
    best, best_score = None, 0.0
    for it in order_items:
        score = SequenceMatcher(None, target, _norm(it["name"])).ratio()
        if score > best_score:
            best, best_score = it, score
    return best if best_score >= 0.6 else None


def compare(order_items: list[dict], counted: dict[str, float], invoice: InvoiceRead | None) -> ReconcileResult:
    d: list[dict] = []
    ordered_skus = {it["sku"] for it in order_items}
    for it in order_items:
        c, q = float(counted.get(it["sku"], 0.0)), float(it["qty"])
        if c < q:
            d.append({"kind": "SHORT_DELIVERY", "sku": it["sku"], "expected": q, "actual": c})
        elif c > q:
            d.append({"kind": "OVER_DELIVERY", "sku": it["sku"], "expected": q, "actual": c})
    for sku, c in counted.items():
        if sku not in ordered_skus:
            d.append({"kind": "UNORDERED_ITEM", "sku": sku, "expected": 0.0, "actual": float(c)})
    if invoice is not None:
        if not invoice.agreed:
            d.append({"kind": "INVOICE_UNREADABLE", "fields": list(invoice.disagreements)})
        else:
            line_sum = 0
            for line in invoice.lines:
                line_sum += line.line_total
                it = _match_line(line.name, order_items)
                if it is None:
                    d.append({"kind": "UNKNOWN_INVOICE_LINE", "name": line.name})
                    continue
                c = float(counted.get(it["sku"], 0.0))
                if abs(float(line.qty) - c) > 1e-9:
                    d.append({"kind": "INVOICE_QTY_MISMATCH", "sku": it["sku"], "expected": c,
                              "actual": float(line.qty)})
                if it.get("agreedPrice") is not None and line.unit_price != int(it["agreedPrice"]):
                    d.append({"kind": "PRICE_MISMATCH", "sku": it["sku"], "expected": int(it["agreedPrice"]),
                              "actual": line.unit_price})
                if line.line_total != int(round(float(line.qty) * line.unit_price)):
                    d.append({"kind": "LINE_TOTAL_MISMATCH", "sku": it["sku"],
                              "expected": int(round(float(line.qty) * line.unit_price)), "actual": line.line_total})
            if invoice.total is not None and invoice.total != line_sum:
                d.append({"kind": "TOTAL_MISMATCH", "expected": line_sum, "actual": invoice.total})
    return ReconcileResult(status="match" if not d else "discrepancy", discrepancies=d)


def payable(order_items: list[dict], counted: dict[str, float]) -> int:
    """Pay for what was verified as received (never more than ordered) at the agreed price."""
    total = 0
    for it in order_items:
        if it.get("agreedPrice") is None:
            raise ValueError(f"no agreed price for {it['sku']}")
        qty = min(float(counted.get(it["sku"], 0.0)), float(it["qty"]))
        total += int(round(qty * int(it["agreedPrice"])))
    return total
```

```python
# services/live/aira_live/business/hints.py
"""Engine outcome code → i18n key. Tools speak hints.say(code, lang, **kw); the engine never builds sentences."""
from aira_live.i18n import t

HINTS: dict[str, str] = {
    "InsufficientStock": "biz.stock.insufficient",          # product, have
    "InsufficientTender": "biz.cash.short",                 # short
    "PaymentMismatch": "biz.payment.mismatch",
    "InvalidTransition": "biz.state.invalid",
    "SHORT_DELIVERY": "biz.delivery.short",                 # product, expected, actual
    "OVER_DELIVERY": "biz.delivery.over",                   # product, expected, actual
    "UNORDERED_ITEM": "biz.delivery.unordered",             # product
    "INVOICE_UNREADABLE": "biz.invoice.unreadable",
    "UNKNOWN_INVOICE_LINE": "biz.invoice.unknown_line",     # name
    "INVOICE_QTY_MISMATCH": "biz.invoice.qty_mismatch",     # product, expected, actual
    "PRICE_MISMATCH": "biz.invoice.price_mismatch",         # product, expected, actual
    "LINE_TOTAL_MISMATCH": "biz.invoice.line_total_mismatch",  # product
    "TOTAL_MISMATCH": "biz.invoice.total_mismatch",         # expected, actual
    "PRODUCT_AMBIGUOUS": "biz.product.ambiguous",           # options
    "PRODUCT_UNKNOWN": "biz.product.unknown",
    "SUPPLIER_UNKNOWN": "biz.supplier.unknown",
    "SOUNDBOX_UNSURE": "biz.upi.unsure",
}


def key_for(code: str) -> str:
    return HINTS[code]


def say(code: str, lang: str, **kw) -> str:
    return t(HINTS[code], lang, **kw)
```

- [ ] **Step 6: Run the whole engine suite**

Run: `cd services/live && uv run pytest tests/business -q`
Expected: `81 passed, 1 skipped`:

| Test file | Passed |
|---|---|
| `test_states_store.py` | 7 |
| `test_money_soundbox.py` | 27 |
| `test_catalog.py` | 17 |
| `test_ledger_expiry_audit.py` | 10 (+1 emulator test skipped without `FIRESTORE_EMULATOR_HOST`) |
| `test_payments_sales.py` | 11 |
| `test_reconcile_scenario.py` | 7 |
| `test_hints.py` | 2 |

- [ ] **Step 7: Commit and open the PR**

```bash
git add services/live/aira_live/business/reconcile.py services/live/aira_live/business/hints.py \
        services/live/aira_live/i18n services/live/aira_live/vision/invoice.py \
        services/live/tests/business/test_reconcile_scenario.py services/live/tests/business/test_hints.py
git commit -m "feat(business): delivery reconciliation, biz.* i18n hint keys, full order→count→invoice→pay→sell scenario"
git push -u origin kanish/business-engine
gh pr create --base main --title "Business engine (states, ledger, sales, payments, soundbox, reconcile, hints)" \
  --body "Implements docs/superpowers/plans/kanish/01-business-engine.md. Engine suite: 81 passed + 1 emulator test. Adds catalog.resolve_supplier and outbox.order_state/discrepancies (used by kanish/02), post-commit analytics events, biz.* i18n keys."
```

---

## Self-Review Notes

- **Spec coverage:**

  | Spec section | Covered by |
  |---|---|
  | §3 principles 1–4, 6 | Transactions; evidence-checked `confirm`; ambiguity → `None` and `INVOICE_UNREADABLE`; no counterfeit claims |
  | §5 paths and states | Tasks 1, 4, 5 |
  | §6 idempotency, exactly-once, soundbox ±₹0 | Tasks 2, 4, 5 |
  | §2 steps 2–4 and 6–8 (engine side) | Tasks 4–6 |

  Tools, messaging and the dashboard are in `kanish/02` and `kanish/03`.
- **Fixed IDs** (`murugan_dairy`, 5 SKUs, 3 suppliers) are used in the catalog and every test. Phones come only from env.
- **Cash/UPI only:** `payments.record` rejects anything but `cash`/`upi`, and `test_unknown_entity_raises` guards the state tables.
- **Spoken hints:** the engine returns codes and exceptions; `hints.say` → `i18n.t` is the only way they are spoken. `test_every_hint_key_in_all_languages` keeps en/ta/hi complete.
- **Cross-team:**
  - The stub models in `vision/identify.py` and `vision/invoice.py` match the interfaces field-for-field; Sarmitha's PRs replace them with the same names.
  - `catalog.resolve_supplier`, `outbox.order_state` and `outbox.discrepancies` are new additive helpers, consumed by `kanish/02`. That plan must call them after each order transition and after saving a delivery.
- **Analytics contracts (`saravana/02`):**
  - `sales` (per line), `inventory-movements`, `orders`, `discrepancies`: payload keys exactly as specified, published only after a new commit, outside transactions.
  - `createdAt`/`completedAt` are server timestamps.
  - `expiry.scan` returns `sku, name, qty, daysLeft`.
  - The catalog carries the optional `inventory` and `demoDailyBase`.
- **Idempotency:** engine signatures keep `idem_key`; tools pass `aira_live.ids.current_idem_key()`; tests use fixed keys.
