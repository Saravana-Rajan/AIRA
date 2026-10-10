# AIRA: Orders, Delivery, Payments & WhatsApp Implementation Plan (Kanish · 02)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Murugan's shop loop work end to end through Gemini Live tools:
- order stock by voice and send it to the vendor on WhatsApp, with the vendor's reply spoken back
- count the delivery live
- check the paper invoice against the order and the count
- pay the vendor with the cash checked
- run a customer sale, with cash and change checked, or UPI confirmed by the shop's payment soundbox
- answer stock and expiry questions

**Architecture:** Every tool in `services/live/aira_live/tools/` is a thin adapter.
- **The model proposes, the backend commits.** A consequential step returns `confirm.propose(...)`. The change happens only in a **committer** (registered with `confirm.register_committer`) after the owner says "yes".
- **The engine (`kanish/01`) is the only writer of stock, money and states:** `catalog`, `ledger`, `sales`, `payments`, `money`, `soundbox`, `reconcile`, `states`, `outbox`, `audit`, `hints`.
- **Vision comes from `sarmitha/01–02`.**
- **Orders and deliveries persist through `tools/_repo.py`** on the engine's `Store` (`get_store()`). Tests use `MemoryStore`.
- **Supplier messages go out through `aira_live/messaging/`:**
  - WhatsApp Cloud API, gated by Remote Config
  - Telegram (optional)
  - a click-to-chat fallback
- **Vendor replies come back** through `messaging/webhooks.py` and are spoken with `sessions.notify`.

**Tech Stack:** Python 3.12, FastAPI `APIRouter`, `httpx` (async; `httpx.MockTransport` and `ASGITransport` in tests), pydantic v2, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-10-10-aira-design.md` (§2 core loop, §3 principles, §4.2 ordering channel, §5 data model, §6 safety)
**Interfaces:** `docs/superpowers/plans/2026-10-10-00-interfaces.md`

**Prerequisites merged on `main`:**
- the foundation plan
- `kanish/01-business-engine`

`sarmitha/01–02` (vision) and `saravana/01` (`confirm`, `sessions`, `live_bridge`, `ids.current_idem_key`) may still be in progress. This plan's tests fake them through their exact signatures.

## Global Constraints

- **Committers only.** Orders, stock, payments and sales change only inside committers, and only after the owner's "yes".
  - Committer signature (foundation): `async def fn(payload: dict, value: str | None) -> dict`.
  - Confirm kinds: `"order" | "payment" | "sale" | "discrepancy" | "generic"`.
- **Idempotency keys come only from `aira_live.ids.current_idem_key()`.**
  - Capture the key at **propose** time and carry it in the payload as `payload["idem"]`, so a retried `confirm_action` reuses it.
  - Where one commit writes several SKUs, use `f"{payload['idem']}:{sku}"`, the same suffix convention `sales.complete` uses.
  - Never invent keys any other way.
  - In tests, set `aira_live.session.current_tool_call_id` to a fixed value.
- **Only VERIFIED quantities enter stock.** Use `ledger.apply(..., reason="DELIVERY", ...)` with the counted quantity, never the ordered or invoiced quantity.
- **Quantities are in each product's selling unit:** `L` (milk, curd), `pack` (buttermilk, paneer, butter), `jar` (ghee), `box` (ice cream).
  - Every spoken quantity converts with `kanish/01`'s `catalog.to_selling_units(product, qty, spoken_unit) -> (qty, unit)`. Example: "5 kg paneer" → `(25, "pack")`.
  - Counted packs convert with `to_selling_units(product, packs, "pack")`. Example: 2 × `sakthi_curd_500ml` → `(1.0, "L")`.
  - Read-backs say both forms: "25 packs of 200 g = 5 kg".
- **Money is `int` rupees.**
  - Change and note plans go through `business.money` (`change_due`, `notes_for`, `total_of`).
  - The supplier amount due comes from `reconcile.payable(order_items, counted)`.
  - UPI is confirmed **only** by `soundbox.parse(...)` with `amount == sale total`, or by the owner's explicit "yes" (`user_confirm` evidence). A customer's screen is never trusted.
- **Order states only via `states.transition("order", cur, target)`.**
  - After every saved order state change, call `outbox.order_state(shop_id, order_id, state)`.
  - After saving a delivery that has discrepancies, call `outbox.discrepancies(shop_id, delivery_id, result)`.
  - Never publish analytics directly.
- **Fixed demo data:**
  - shop id `murugan_dairy` (env `AIRA_DEMO_SHOP`)
  - SKUs: `aavin_milk_500ml`, `aavin_milk_1l`, `sakthi_curd_500ml`, `sakthi_curd_1l`, `aavin_buttermilk_200ml`, `aavin_ghee_200ml`, `aavin_ghee_500ml`, `aavin_paneer_200g`, `aavin_butter_100g`, `arun_icecream_box`
  - suppliers `aavin_vendor`, `sakthi_vendor`, `arun_distributor`
- **All speech goes through `aira_live.i18n.t(key, session.lang, ...)`.**
  - That includes `CashRead.hint_key` (e.g. `cash.spread_notes`) and `CountResult.aim_hint` (e.g. `vision.aim.move_back`). When a hint key is non-empty, speak `t(key, lang)` and re-request the frame instead of proceeding.
  - Engine outcomes use `business.hints.say(code, lang, ...)`.
  - This plan adds `k2.*` keys in `en`, `ta` and `hi`.
- **WhatsApp is gated by `aira_live.remote_config.flag("whatsapp_enabled", True)`.** When the flag is false, click-to-chat is used.
- **Secrets come from env only:**
  - `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`, `TELEGRAM_TOKEN`
  - optional: `WHATSAPP_APP_SECRET` (webhook signature), `WHATSAPP_TEMPLATE` (default `order_request`), `WHATSAPP_GRAPH_VERSION` (default `v21.0`)
- **Payments are cash or UPI.** The product name is **AIRA**. Every tool returns a dict containing `say`.

## Review Focus

1. **A retried commit** (the same proposal committed twice) must not add delivery stock twice or complete a sale twice. Test: `test_delivery_commit_is_idempotent`, `test_sale_commit_is_idempotent`.
2. **One pack held in view for 4 seconds** counts once. A different SKU held up never counts. Test: `test_counter_holding_same_pack_counts_once`, `test_counter_ignores_other_sku`.
3. **A soundbox "₹600 received" for a ₹60 sale** never confirms the payment. AIRA warns and keeps listening. Test: `test_upi_soundbox_wrong_amount_does_not_confirm`.
4. **WhatsApp failure or the flag off** returns a click-to-chat fallback, and the order stays `DRAFT` until the owner says it was sent. Test: `test_send_order_falls_back_to_click_to_chat`, `test_commit_send_fallback_keeps_draft_and_opens_url`.
5. **Unreadable cash** (`hint_key` set) or a blurred count (`aim_hint` set) speaks the hint and re-asks, and never proposes a payment or a count. Test: `test_supplier_pay_cash_hint_key_retake`, `test_sale_pay_cash_hint_key_retake`, `test_delivery_count_crate_retake_speaks_aim_hint`.

## Cross-team notes

- **Saravana (`saravana/01`):**
  - Mount `aira_live.messaging.webhooks.router` in `main.py`.
  - Register `delivery_count` and `sale_pay` as **non-blocking** Live tools. They can run for up to 3 minutes and 60 s.
  - Add `WHATSAPP_APP_SECRET` next to the other WhatsApp secrets.
- **Ishwarya (`ishwarya/01`):** when a `state` message arrives with `entity == "order"` and a `summary` starting with `OPEN_URL `, open the URL that follows (a `wa.me` or `tel:` link) and say "double-tap Send".
- **Firestore:**
  - Two indexes, written only by this module (server-only; Saravana adds the rules):
    - `msgIndex/{channel}:{messageId}` → `{shopId, orderId}`
    - `contactIndex/{channel}:{contact}` → `{shopId, supplierId}`
  - Optional `Supplier.telegramChatId`.
- **`kanish/01` (`catalog.to_selling_units`):** this plan relies on two behaviours:
  - `spoken_unit="pack"` converts a counted number of physical packs into the selling unit. Example: `(product_doc_for_sakthi_curd_500ml, 2, "pack")` → `(1.0, "L")`.
  - It raises `ValueError` for a unit that makes no sense for the product (e.g. "kg" of milk).
  - The `product` argument is the product document dict, including `"sku"`.

## File Structure

```
services/live/aira_live/
  messaging/__init__.py      send_order(): channel choice (flag-gated) + fallback + indexes
  messaging/whatsapp.py      WhatsAppError, configured(), send_template()
  messaging/telegram.py      TelegramError, configured(), send_message()
  messaging/clicktochat.py   normalize_in_phone(), wa_link(), tel_link(), order_text()
  messaging/webhooks.py      router: GET/POST /webhooks/whatsapp, POST /webhooks/telegram
  tools/_repo.py             orders, deliveries, message/contact indexes on get_store()
  tools/orders.py            order_create, order_status; committers order.send, order.mark_sent
  tools/delivery.py          OnePassCounter, delivery_start, delivery_count, invoice_check;
                             committer delivery.record_count
  tools/payments.py          supplier_pay, sale_pay; committers supplier_pay.cash,
                             supplier_pay.upi, sale.complete
  tools/sales.py             sale_start
  tools/stock.py             stock_query, expiry_check
  vision/count.py, vision/cash.py   models + stubs ONLY IF ABSENT (Sarmitha's plan owns them)
  i18n/{en,ta,hi}.json       + k2.* keys (merge into the existing files)
services/live/tests/k2/
  __init__.py  conftest.py   store (MemoryStore + seeded catalog/stock), published, session,
                             proposals, notified, fake vision helpers
  test_messaging.py  test_webhooks.py  test_orders.py  test_delivery.py
  test_payments_sales.py  test_e2e_loop.py
```

## Schedule (Kanish)

| Day | Date | Tasks |
|---|---|---|
| D2 | Oct 11 | Task 1 (channels) + Task 2 (webhooks). Send a real `hello_world` from the Meta test number to your own phone |
| D3 | Oct 12 | Task 3 (orders) + Task 4 (delivery count + invoice). Try them through the tool harness with real photos |
| D4 | Oct 13 | Task 5 (payments, sales, stock) + Task 6 (end-to-end loop). Run the full loop live on the phone |

---

### Task 1: Messaging channels (WhatsApp Cloud API, Telegram, click-to-chat) + test fixtures

**Files:**
- Create: `services/live/aira_live/messaging/__init__.py`, `messaging/whatsapp.py`, `messaging/telegram.py`, `messaging/clicktochat.py`
- Create: `services/live/aira_live/tools/_repo.py`
- Test: `services/live/tests/k2/__init__.py` (empty), `services/live/tests/k2/conftest.py`, `services/live/tests/k2/test_messaging.py`

**Interfaces:**
- Consumes:
  - `business.store.get_store()`, `business.paths.shop(shop_id)` (`kanish/01`)
  - `remote_config.flag(name, default) -> bool` (`saravana/02`)
- Produces:
  - `messaging.send_order(shop_id: str, supplier: dict, order: dict) -> dict`, returning one of:
    - `{"channel": "whatsapp" | "telegram", "messageId": str}`
    - `{"channel": "whatsapp_link" | "call", "fallbackUrl": str}`
  - `clicktochat.normalize_in_phone(phone) -> str`, `wa_link(phone, text) -> str`, `tel_link(phone) -> str`, `items_text(items) -> str`, `order_text(shop_name, items) -> str`
  - `whatsapp.WhatsAppError`, `whatsapp.configured() -> bool`, `whatsapp.send_template(to_phone, shop_name, items_text, *, transport=None) -> str`
  - `telegram.TelegramError`, `telegram.configured() -> bool`, `telegram.send_message(chat_id, text, *, transport=None) -> str`
  - `_repo`:
    - `get_order`, `save_order`, `latest_order(shop_id, states, supplier_id=None)`
    - `get_delivery`, `save_delivery`, `delivery_for_order`
    - `index_message`, `lookup_message`, `index_contact`, `lookup_contact`

- [ ] **Step 1: Write the shared test fixtures**

```python
# services/live/tests/k2/conftest.py
"""Fakes for kanish/02. Real engine (kanish/01) on a MemoryStore seeded from the real demo catalog."""
import itertools
from contextvars import ContextVar
from datetime import date
from pathlib import Path

import pytest

from aira_live import ids
from aira_live import session as session_mod
from aira_live.business import outbox, seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.frames import Frame, FrameStore

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "content" / "catalog" / "murugan_dairy.json"
SHOP = "murugan_dairy"
TODAY = date(2026, 10, 12)
PHONES = {"aavin_vendor": "+919000000001", "sakthi_vendor": "+919000000002", "arun_distributor": "+919000000003"}

# saravana/01 adds current_tool_call_id + ids.current_idem_key; until it is merged, install the same contract.
if not hasattr(session_mod, "current_tool_call_id"):
    session_mod.current_tool_call_id = ContextVar("current_tool_call_id", default="")
current_tool_call_id = session_mod.current_tool_call_id
current_session = session_mod.current_session


def _current_idem_key() -> str:
    call = current_tool_call_id.get()
    if not call:
        raise RuntimeError("current_idem_key() called outside a tool call")
    return ids.idem_key(current_session.get().session_id, call)


@pytest.fixture(autouse=True)
def _idem_contract(monkeypatch):
    if not hasattr(ids, "current_idem_key"):
        monkeypatch.setattr(ids, "current_idem_key", _current_idem_key, raising=False)


@pytest.fixture(autouse=True)
def published():
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
    catalog = seed.load_catalog(CATALOG)
    await seed.seed_into(s, catalog)
    await seed.seed_inventory(s, catalog, today=TODAY)
    for sid, phone in PHONES.items():                       # seed reads phones from env; tests set them here
        path = f"shops/{SHOP}/suppliers/{sid}"
        await s.set(path, {**(await s.get(path)), "phone": phone})
    yield s
    set_store(None)


async def supplier_doc(store, sid: str) -> dict:
    return {"id": sid, **(await store.get(f"shops/{SHOP}/suppliers/{sid}"))}


class FakeSession:
    def __init__(self, lang: str = "en"):
        self.session_id, self.uid, self.shop_id = "sess1", "owner1", SHOP
        self.lang, self.verbosity = lang, "short"
        self.frames = FrameStore()
        self.tool_results: list[dict] = []
        self.sent: list = []
        self.queued: dict[str, list[Frame]] = {}
        self._ts = itertools.count(1_000)

    async def send(self, msg) -> None:
        self.sent.append(msg)

    def queue(self, purpose: str, *frame_ids: str) -> None:
        """Frames returned by request_frames(purpose, count) in order."""
        for fid in frame_ids:
            self.queued.setdefault(purpose, []).append(
                Frame(id=fid, jpeg_b64="x", w=640, h=480, ts=float(next(self._ts)), purpose=purpose))

    def stream(self, purpose: str, items: list[tuple[str, float]]) -> None:
        """Frames arriving on the live FrameStore (continuous capture): [(frame_id, ts_ms), ...]."""
        for fid, ts in items:
            self.frames.add(Frame(id=fid, jpeg_b64="x", w=640, h=480, ts=float(ts), purpose=purpose))

    async def request_frames(self, purpose, count=1, interval_ms=0, timeout_s=4.0):
        q = self.queued.get(purpose, [])
        out, self.queued[purpose] = q[:count], q[count:]
        return out

    def sent_of(self, t: str) -> list:
        return [m for m in self.sent if getattr(m, "t", None) == t]


@pytest.fixture
def session():
    s = FakeSession()
    tok_s, tok_c = current_session.set(s), current_tool_call_id.set("tc-1")
    yield s
    current_session.reset(tok_s)
    current_tool_call_id.reset(tok_c)


def use_call(call_id: str) -> None:
    """Simulate a new Live tool call (new idempotency key)."""
    current_tool_call_id.set(call_id)


@pytest.fixture
def proposals(monkeypatch):
    """Fake confirm.propose → records proposals; returns the foundation's dict shape."""
    from aira_live import confirm

    made: list[dict] = []

    async def fake_propose(kind, prompt, payload, committer):
        made.append({"kind": kind, "prompt": prompt, "payload": payload, "committer": committer})
        return {"needs_confirm": True, "confirm_id": f"cf_{len(made)}", "say": prompt}

    monkeypatch.setattr(confirm, "propose", fake_propose)
    return made


@pytest.fixture
def notified(monkeypatch):
    from aira_live import sessions

    said: list[tuple[str, str, str]] = []

    async def fake_notify(shop_id, text, cue="ack"):
        said.append((shop_id, text, cue))
        return 1

    monkeypatch.setattr(sessions, "notify", fake_notify)
    return said
```

- [ ] **Step 2: Write the failing tests**

```python
# services/live/tests/k2/test_messaging.py
import json

import httpx
import pytest

import aira_live.messaging as messaging
from aira_live.messaging import clicktochat, whatsapp
from aira_live.tools import _repo

from .conftest import SHOP, supplier_doc

ORDER = {"id": "o1", "items": [
    {"sku": "sakthi_curd_1l", "name": "Sakthi Curd 1 L", "qty": 30.0, "unit": "L", "agreedPrice": 50},
    {"sku": "aavin_paneer_200g", "name": "Aavin Paneer 200 g", "qty": 25.0, "unit": "pack", "agreedPrice": 80}]}


def test_phone_normalization_and_links():
    assert clicktochat.normalize_in_phone("+91 90000 00002") == "919000000002"
    assert clicktochat.normalize_in_phone("9000000002") == "919000000002"
    assert clicktochat.normalize_in_phone("09000000002") == "919000000002"
    assert clicktochat.wa_link("9000000002", "30 L curd") == "https://wa.me/919000000002?text=30%20L%20curd"
    assert clicktochat.tel_link("9000000003") == "tel:+919000000003"


def test_order_text_lists_quantities_and_units():
    assert clicktochat.order_text("Murugan Dairy", ORDER["items"]) == (
        "New order from Murugan Dairy: 30 L Sakthi Curd 1 L, 25 pack Aavin Paneer 200 g. "
        "Please confirm delivery time.")


def _env(monkeypatch, template=None):
    monkeypatch.setenv("WHATSAPP_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "123")
    monkeypatch.delenv("WHATSAPP_GRAPH_VERSION", raising=False)
    if template:
        monkeypatch.setenv("WHATSAPP_TEMPLATE", template)
    else:
        monkeypatch.delenv("WHATSAPP_TEMPLATE", raising=False)


async def test_send_template_builds_graph_request(monkeypatch):
    _env(monkeypatch)
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(url=str(req.url), auth=req.headers["authorization"], body=json.loads(req.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    mid = await whatsapp.send_template("+919000000002", "Murugan Dairy", "30 L Sakthi Curd 1 L",
                                       transport=httpx.MockTransport(handler))
    assert mid == "wamid.ABC"
    assert seen["url"] == "https://graph.facebook.com/v21.0/123/messages"
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["to"] == "919000000002"
    tpl = seen["body"]["template"]
    assert tpl["name"] == "order_request"
    assert [p["text"] for p in tpl["components"][0]["parameters"]] == ["Murugan Dairy", "30 L Sakthi Curd 1 L"]


async def test_hello_world_template_has_no_components(monkeypatch):
    _env(monkeypatch, template="hello_world")
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.H"}]})

    await whatsapp.send_template("9000000002", "Murugan Dairy", "x", transport=httpx.MockTransport(handler))
    assert seen["body"]["template"] == {"name": "hello_world", "language": {"code": "en_US"}}


async def test_send_template_http_error_raises(monkeypatch):
    _env(monkeypatch)
    transport = httpx.MockTransport(lambda req: httpx.Response(401, json={"error": {"message": "bad token"}}))
    with pytest.raises(whatsapp.WhatsAppError):
        await whatsapp.send_template("9000000002", "Murugan Dairy", "x", transport=transport)


async def test_send_order_whatsapp_success_indexes_message(store, monkeypatch):
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: True)
    monkeypatch.setattr(whatsapp, "configured", lambda: True)

    async def fake_send(to, shop_name, items_text, **kw):
        assert (to, shop_name) == ("+919000000002", "Murugan Dairy")
        return "wamid.1"

    monkeypatch.setattr(whatsapp, "send_template", fake_send)
    res = await messaging.send_order(SHOP, await supplier_doc(store, "sakthi_vendor"), ORDER)
    assert res == {"channel": "whatsapp", "messageId": "wamid.1"}
    assert await _repo.lookup_message("whatsapp", "wamid.1") == {"shopId": SHOP, "orderId": "o1"}
    assert await _repo.lookup_contact("whatsapp", "919000000002") == {"shopId": SHOP, "supplierId": "sakthi_vendor"}


async def test_send_order_falls_back_to_click_to_chat(store, monkeypatch):
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: True)
    monkeypatch.setattr(whatsapp, "configured", lambda: True)

    async def failing(*a, **kw):
        raise whatsapp.WhatsAppError("HTTP 500")

    monkeypatch.setattr(whatsapp, "send_template", failing)
    res = await messaging.send_order(SHOP, await supplier_doc(store, "sakthi_vendor"), ORDER)
    assert res["channel"] == "whatsapp_link"
    assert res["fallbackUrl"].startswith("https://wa.me/919000000002?text=New%20order%20from%20Murugan%20Dairy")


async def test_send_order_flag_off_uses_click_to_chat(store, monkeypatch):
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: False)

    async def must_not_send(*a, **kw):
        raise AssertionError("WhatsApp API must not be called when the flag is off")

    monkeypatch.setattr(whatsapp, "send_template", must_not_send)
    res = await messaging.send_order(SHOP, await supplier_doc(store, "aavin_vendor"), ORDER)
    assert res["channel"] == "whatsapp_link" and "919000000001" in res["fallbackUrl"]


async def test_send_order_call_channel_returns_tel(store):
    res = await messaging.send_order(SHOP, await supplier_doc(store, "arun_distributor"), ORDER)
    assert res == {"channel": "call", "fallbackUrl": "tel:+919000000003"}
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `cd services/live && uv run pytest tests/k2/test_messaging.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.messaging'`.

- [ ] **Step 4: Implement the click-to-chat helpers**

```python
# services/live/aira_live/messaging/clicktochat.py
"""Zero-setup fallbacks: a WhatsApp click-to-chat link and a phone-call link, plus the order wording."""
import re
from urllib.parse import quote


def normalize_in_phone(phone: str) -> str:
    """Digits only, with the Indian country code: '+91 90000 00002' / '9000000002' / '09000000002' → '919000000002'."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def wa_link(phone: str, text: str) -> str:
    return f"https://wa.me/{normalize_in_phone(phone)}?text={quote(text)}"


def tel_link(phone: str) -> str:
    return f"tel:+{normalize_in_phone(phone)}"


def fmt_qty(q: float) -> str:
    q = float(q)
    return str(int(q)) if q.is_integer() else f"{q:g}"


def items_text(items: list[dict]) -> str:
    """[{name, qty, unit}] → '30 L Sakthi Curd 1 L, 25 pack Aavin Paneer 200 g' (single line: WhatsApp template rule)."""
    return ", ".join(f"{fmt_qty(it['qty'])} {it['unit']} {it['name']}" for it in items)


def order_text(shop_name: str, items: list[dict]) -> str:
    return f"New order from {shop_name}: {items_text(items)}. Please confirm delivery time."
```

- [ ] **Step 5: Implement the WhatsApp and Telegram clients**

Verify the request shape against https://developers.facebook.com/docs/whatsapp/cloud-api (messages endpoint, template messages) and https://core.telegram.org/bots/api#sendmessage. The code below follows the documented shapes.

```python
# services/live/aira_live/messaging/whatsapp.py
"""WhatsApp Business Cloud API (Meta). Hackathon: Meta's free test number, vendors registered as test recipients.
Template `order_request` (utility, en): body "New order from {{1}}: {{2}}. Please confirm delivery time."
Set WHATSAPP_TEMPLATE=hello_world for the very first smoke test (no parameters)."""
import os

import httpx

from aira_live.messaging.clicktochat import normalize_in_phone


class WhatsAppError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("WHATSAPP_TOKEN") and os.getenv("WHATSAPP_PHONE_ID"))


def _url() -> str:
    version = os.getenv("WHATSAPP_GRAPH_VERSION", "v21.0")
    return f"https://graph.facebook.com/{version}/{os.environ['WHATSAPP_PHONE_ID']}/messages"


def template_body(to_phone: str, shop_name: str, items: str) -> dict:
    name = os.getenv("WHATSAPP_TEMPLATE", "order_request")
    if name == "hello_world":
        template = {"name": "hello_world", "language": {"code": "en_US"}}
    else:
        template = {"name": name, "language": {"code": "en"},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": shop_name},
                                                                   {"type": "text", "text": items}]}]}
    return {"messaging_product": "whatsapp", "to": normalize_in_phone(to_phone), "type": "template",
            "template": template}


async def send_template(to_phone: str, shop_name: str, items: str, *,
                        transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Returns the WhatsApp message id (wamid…); raises WhatsAppError on any failure."""
    if not configured():
        raise WhatsAppError("WhatsApp is not configured")
    headers = {"Authorization": f"Bearer {os.environ['WHATSAPP_TOKEN']}"}
    try:
        async with httpx.AsyncClient(timeout=8.0, transport=transport) as client:
            resp = await client.post(_url(), json=template_body(to_phone, shop_name, items), headers=headers)
    except httpx.HTTPError as exc:
        raise WhatsAppError(str(exc)) from exc
    if resp.status_code >= 300:
        raise WhatsAppError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()["messages"][0]["id"]
    except (KeyError, IndexError, ValueError) as exc:
        raise WhatsAppError("no message id in response") from exc
```

```python
# services/live/aira_live/messaging/telegram.py
"""Optional Telegram channel (free). Works only after the vendor has started a chat with the AIRA bot."""
import os

import httpx


class TelegramError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.getenv("TELEGRAM_TOKEN"))


async def send_message(chat_id: str, text: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    if not configured():
        raise TelegramError("Telegram is not configured")
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=8.0, transport=transport) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text})
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TelegramError(str(exc)) from exc
    if not data.get("ok"):
        raise TelegramError(str(data.get("description", "telegram error")))
    return str(data["result"]["message_id"])
```

- [ ] **Step 6: Implement the repository and `send_order`**

```python
# services/live/aira_live/tools/_repo.py
"""Orders, deliveries and message/contact indexes on the engine Store (Firestore in prod, MemoryStore in tests)."""
from aira_live.business.store import get_store


def order_path(shop_id: str, oid: str) -> str: return f"shops/{shop_id}/orders/{oid}"
def delivery_path(shop_id: str, did: str) -> str: return f"shops/{shop_id}/deliveries/{did}"


async def get_order(shop_id: str, oid: str) -> dict | None:
    doc = await get_store().get(order_path(shop_id, oid))
    return {**doc, "id": oid} if doc else None


async def save_order(shop_id: str, order: dict) -> None:
    await get_store().set(order_path(shop_id, order["id"]), order)


async def latest_order(shop_id: str, states: list[str], supplier_id: str | None = None) -> dict | None:
    rows = await get_store().list(f"shops/{shop_id}/orders")
    cands = [{**d, "id": oid} for oid, d in rows
             if d.get("state") in states and (supplier_id is None or d.get("supplierId") == supplier_id)]
    return max(cands, key=lambda o: o.get("updatedAt", ""), default=None)


async def get_delivery(shop_id: str, did: str) -> dict | None:
    doc = await get_store().get(delivery_path(shop_id, did))
    return {**doc, "id": did} if doc else None


async def save_delivery(shop_id: str, delivery: dict) -> None:
    await get_store().set(delivery_path(shop_id, delivery["id"]), delivery)


async def delivery_for_order(shop_id: str, order_id: str) -> dict | None:
    rows = await get_store().list(f"shops/{shop_id}/deliveries")
    found = [{**d, "id": did} for did, d in rows if d.get("orderId") == order_id]
    return max(found, key=lambda d: d.get("updatedAt", ""), default=None)


async def index_message(channel: str, key: str, shop_id: str, order_id: str) -> None:
    await get_store().set(f"msgIndex/{channel}:{key}", {"shopId": shop_id, "orderId": order_id})


async def lookup_message(channel: str, key: str) -> dict | None:
    return await get_store().get(f"msgIndex/{channel}:{key}")


async def index_contact(channel: str, contact: str, shop_id: str, supplier_id: str) -> None:
    await get_store().set(f"contactIndex/{channel}:{contact}", {"shopId": shop_id, "supplierId": supplier_id})


async def lookup_contact(channel: str, contact: str) -> dict | None:
    return await get_store().get(f"contactIndex/{channel}:{contact}")
```

```python
# services/live/aira_live/messaging/__init__.py
"""Supplier order delivery: WhatsApp Cloud API (flag-gated) → Telegram (optional) → click-to-chat / call fallback."""
from aira_live.business import paths
from aira_live.business.store import get_store
from aira_live.messaging import clicktochat, telegram, whatsapp
from aira_live.tools import _repo


def _whatsapp_enabled() -> bool:
    from aira_live import remote_config
    return remote_config.flag("whatsapp_enabled", True)


async def send_order(shop_id: str, supplier: dict, order: dict) -> dict:
    shop = await get_store().get(paths.shop(shop_id)) or {}
    shop_name = shop.get("name", shop_id)
    phone = supplier.get("phone", "")
    channel = supplier.get("channel", "whatsapp")
    if channel == "call":
        return {"channel": "call", "fallbackUrl": clicktochat.tel_link(phone)}
    if channel == "telegram" and supplier.get("telegramChatId") and telegram.configured():
        chat = str(supplier["telegramChatId"])
        try:
            mid = await telegram.send_message(chat, clicktochat.order_text(shop_name, order["items"]))
            await _repo.index_message("telegram", f"{chat}:{mid}", shop_id, order["id"])
            await _repo.index_contact("telegram", chat, shop_id, supplier["id"])
            return {"channel": "telegram", "messageId": mid}
        except telegram.TelegramError:
            pass
    if _whatsapp_enabled() and whatsapp.configured():
        try:
            mid = await whatsapp.send_template(phone, shop_name, clicktochat.items_text(order["items"]))
            await _repo.index_message("whatsapp", mid, shop_id, order["id"])
            await _repo.index_contact("whatsapp", clicktochat.normalize_in_phone(phone), shop_id, supplier["id"])
            return {"channel": "whatsapp", "messageId": mid}
        except whatsapp.WhatsAppError:
            pass
    return {"channel": "whatsapp_link",
            "fallbackUrl": clicktochat.wa_link(phone, clicktochat.order_text(shop_name, order["items"]))}
```

- [ ] **Step 7: Run the tests to see them pass**

Run: `cd services/live && uv run pytest tests/k2/test_messaging.py -q`
Expected: `9 passed`.

- [ ] **Step 8: Smoke-test the real Meta test number (manual, 5 minutes)**

1. In Meta for Developers, create the app with the WhatsApp product.
2. Copy the **test phone number id** and a temporary access token.
3. Add your own phone as a test recipient.
4. Run the commands below. Your phone should receive the "Hello World" template.

```bash
cd services/live
WHATSAPP_TOKEN=... WHATSAPP_PHONE_ID=... WHATSAPP_TEMPLATE=hello_world \
uv run python -c "import asyncio; from aira_live.messaging import whatsapp; print(asyncio.run(whatsapp.send_template('+91XXXXXXXXXX','Murugan Dairy','test')))"
```

Expected: a `wamid.` id is printed.

Then submit the `order_request` utility template (body exactly as in the module docstring, language English) for approval in WhatsApp Manager.

- [ ] **Step 9: Commit**

```bash
git add services/live/aira_live/messaging services/live/aira_live/tools/_repo.py services/live/tests/k2
git commit -m "feat(messaging): WhatsApp Cloud API, Telegram, click-to-chat fallback, order repository"
```

---

### Task 2: Vendor-reply webhooks (WhatsApp + Telegram) and the `k2.*` speech keys

**Files:**
- Create: `services/live/aira_live/messaging/webhooks.py`
- Modify: `services/live/aira_live/i18n/en.json`, `ta.json`, `hi.json` (add the `k2.*` keys; keep existing keys)
- Test: `services/live/tests/k2/test_webhooks.py`

**Interfaces:**
- Consumes:
  - `_repo.lookup_message`, `lookup_contact`, `get_order`, `save_order`, `latest_order` (Task 1)
  - `sessions.notify(shop_id, text, cue)` (foundation / `saravana/01`)
  - `i18n.t`
- Produces:
  - `messaging.webhooks.router` (`fastapi.APIRouter`): `GET /webhooks/whatsapp`, `POST /webhooks/whatsapp`, `POST /webhooks/telegram`
  - Order doc fields set on reply: `vendorReply = {text, at, channel}`
  - Every `k2.*` key that later tasks speak

- [ ] **Step 1: Add the speech keys**

Merge these into the existing JSON objects (do not delete other keys).

`i18n/en.json`:
```json
{
  "k2.order.read_back": "Order {items} from {supplier}?",
  "k2.order.sent": "Order sent to {supplier}.",
  "k2.order.fallback": "The order message is ready. Double-tap Send, then tell me when it is sent.",
  "k2.order.call": "Calling {supplier}. Tell them: {items}.",
  "k2.order.mark_sent_q": "Did you send the order to {supplier}?",
  "k2.order.already": "That order was already sent.",
  "k2.order.vendor_replied": "{supplier} replied: {text}",
  "k2.order.none": "No open order found.",
  "k2.order.status": "Order to {supplier}: {state}.",
  "k2.order.status_reply": "Order to {supplier}: {state}. They said: {text}",
  "k2.qty.both": "{qty} × {pack} = {spoken}",
  "k2.delivery.started": "Delivery from {supplier}. Show each pack to the camera, one at a time.",
  "k2.delivery.none": "No delivery is open. Say: start the delivery.",
  "k2.delivery.readback": "I counted {counted} for {product}. Ordered {expected}. Save {counted}?",
  "k2.delivery.saved": "Saved {counted} for {product}.",
  "k2.delivery.nothing_seen": "I did not see any {product}. Show each pack to the camera.",
  "k2.invoice.match": "The bill matches. Total {total} rupees.",
  "k2.pay.plan": "Pay {amount} rupees: {notes}. Show the notes to the camera.",
  "k2.pay.cash_q": "{amount} rupees counted. Give it to {supplier} and confirm?",
  "k2.pay.cash_wrong": "I see {seen} rupees, but it should be {amount}.",
  "k2.pay.upi_q": "Pay {amount} rupees to {supplier} from your UPI app, then say yes.",
  "k2.pay.done": "Payment of {amount} rupees to {supplier} recorded.",
  "k2.pay.not_ready": "Check the delivery and the bill before paying.",
  "k2.cash.unclear": "I cannot see the notes clearly. Show them again.",
  "k2.sale.price": "{items}. Total {total} rupees. Cash or UPI?",
  "k2.sale.none": "No open sale.",
  "k2.sale.change": "Received {tendered}. Give back {change} rupees: {notes}. Show the change to the camera.",
  "k2.sale.change_wrong": "That change is {seen} rupees. It should be {change}.",
  "k2.sale.cash_q": "Received {tendered}, change {change}. Complete the sale?",
  "k2.sale.upi_wait": "Waiting for the payment soundbox.",
  "k2.sale.upi_heard_q": "{amount} rupees received by UPI. Complete the sale?",
  "k2.sale.upi_wrong": "The soundbox said {heard} rupees, but the bill is {total}.",
  "k2.sale.upi_timeout_q": "I did not hear the soundbox. Did you receive {total} rupees by UPI?",
  "k2.sale.done": "Sale complete.",
  "k2.sale.low_stock": "Only {left} left of {product}. Order more?",
  "k2.stock.one": "{product}: {qty} in stock.",
  "k2.stock.all": "Stock: {list}.",
  "k2.expiry.none": "Nothing expires today or tomorrow.",
  "k2.expiry.list": "{list}. Sell or return these first.",
  "k2.expiry.item": "{product} {qty} {when}",
  "k2.when.expired": "already expired",
  "k2.when.today": "expires today",
  "k2.when.tomorrow": "expires tomorrow"
}
```

`i18n/ta.json`:
```json
{
  "k2.order.read_back": "{supplier}-இடம் {items} ஆர்டர் செய்யவா?",
  "k2.order.sent": "{supplier}-க்கு ஆர்டர் அனுப்பப்பட்டது.",
  "k2.order.fallback": "ஆர்டர் செய்தி தயார். அனுப்ப இருமுறை தட்டுங்கள், அனுப்பியதும் சொல்லுங்கள்.",
  "k2.order.call": "{supplier}-ஐ அழைக்கிறேன். அவர்களிடம் சொல்லுங்கள்: {items}.",
  "k2.order.mark_sent_q": "{supplier}-க்கு ஆர்டர் அனுப்பினீர்களா?",
  "k2.order.already": "அந்த ஆர்டர் ஏற்கனவே அனுப்பப்பட்டது.",
  "k2.order.vendor_replied": "{supplier} பதில்: {text}",
  "k2.order.none": "திறந்த ஆர்டர் எதுவும் இல்லை.",
  "k2.order.status": "{supplier} ஆர்டர்: {state}.",
  "k2.order.status_reply": "{supplier} ஆர்டர்: {state}. அவர்கள் சொன்னது: {text}",
  "k2.qty.both": "{qty} × {pack} = {spoken}",
  "k2.delivery.started": "{supplier} டெலிவரி. ஒவ்வொரு பாக்கெட்டையும் ஒன்றன்பின் ஒன்றாக கேமராவில் காட்டுங்கள்.",
  "k2.delivery.none": "திறந்த டெலிவரி இல்லை. சொல்லுங்கள்: டெலிவரி தொடங்கு.",
  "k2.delivery.readback": "{product} {counted} எண்ணினேன். ஆர்டர் {expected}. {counted} சேமிக்கவா?",
  "k2.delivery.saved": "{product} {counted} சேமிக்கப்பட்டது.",
  "k2.delivery.nothing_seen": "{product} எதுவும் தெரியவில்லை. ஒவ்வொரு பாக்கெட்டையும் கேமராவில் காட்டுங்கள்.",
  "k2.invoice.match": "பில் சரியாக உள்ளது. மொத்தம் {total} ரூபாய்.",
  "k2.pay.plan": "{amount} ரூபாய் கொடுங்கள்: {notes}. நோட்டுகளை கேமராவில் காட்டுங்கள்.",
  "k2.pay.cash_q": "{amount} ரூபாய் எண்ணப்பட்டது. {supplier}-இடம் கொடுத்து உறுதி செய்யவா?",
  "k2.pay.cash_wrong": "{seen} ரூபாய் தெரிகிறது, ஆனால் {amount} இருக்க வேண்டும்.",
  "k2.pay.upi_q": "உங்கள் UPI செயலியில் {supplier}-க்கு {amount} ரூபாய் செலுத்தி, பிறகு ஆம் சொல்லுங்கள்.",
  "k2.pay.done": "{supplier}-க்கு {amount} ரூபாய் கட்டணம் பதிவு செய்யப்பட்டது.",
  "k2.pay.not_ready": "பணம் கொடுக்கும் முன் டெலிவரியையும் பில்லையும் சரிபார்க்கவும்.",
  "k2.cash.unclear": "நோட்டுகள் தெளிவாக தெரியவில்லை. மீண்டும் காட்டுங்கள்.",
  "k2.sale.price": "{items}. மொத்தம் {total} ரூபாய். பணமா UPI-யா?",
  "k2.sale.none": "திறந்த விற்பனை இல்லை.",
  "k2.sale.change": "{tendered} பெறப்பட்டது. {change} ரூபாய் திருப்பிக் கொடுங்கள்: {notes}. சில்லறையை கேமராவில் காட்டுங்கள்.",
  "k2.sale.change_wrong": "அந்த சில்லறை {seen} ரூபாய். {change} இருக்க வேண்டும்.",
  "k2.sale.cash_q": "{tendered} பெறப்பட்டது, சில்லறை {change}. விற்பனையை முடிக்கவா?",
  "k2.sale.upi_wait": "பணம் வந்த ஒலிக்காக காத்திருக்கிறேன்.",
  "k2.sale.upi_heard_q": "UPI மூலம் {amount} ரூபாய் வந்தது. விற்பனையை முடிக்கவா?",
  "k2.sale.upi_wrong": "சவுண்ட்பாக்ஸ் {heard} ரூபாய் என்றது, ஆனால் பில் {total}.",
  "k2.sale.upi_timeout_q": "சவுண்ட்பாக்ஸ் கேட்கவில்லை. UPI மூலம் {total} ரூபாய் வந்ததா?",
  "k2.sale.done": "விற்பனை முடிந்தது.",
  "k2.sale.low_stock": "{product} {left} மட்டுமே உள்ளது. மேலும் ஆர்டர் செய்யவா?",
  "k2.stock.one": "{product}: {qty} இருப்பு.",
  "k2.stock.all": "இருப்பு: {list}.",
  "k2.expiry.none": "இன்றோ நாளையோ எதுவும் காலாவதியாகவில்லை.",
  "k2.expiry.list": "{list}. இவற்றை முதலில் விற்கவும் அல்லது திருப்பவும்.",
  "k2.expiry.item": "{product} {qty} {when}",
  "k2.when.expired": "ஏற்கனவே காலாவதியானது",
  "k2.when.today": "இன்று காலாவதியாகிறது",
  "k2.when.tomorrow": "நாளை காலாவதியாகிறது"
}
```

`i18n/hi.json`:
```json
{
  "k2.order.read_back": "{supplier} से {items} ऑर्डर करूँ?",
  "k2.order.sent": "{supplier} को ऑर्डर भेज दिया।",
  "k2.order.fallback": "ऑर्डर संदेश तैयार है। भेजने के लिए दो बार टैप करें, फिर बताएँ।",
  "k2.order.call": "{supplier} को कॉल कर रहा हूँ। उन्हें बताइए: {items}।",
  "k2.order.mark_sent_q": "क्या आपने {supplier} को ऑर्डर भेज दिया?",
  "k2.order.already": "वह ऑर्डर पहले ही भेजा जा चुका है।",
  "k2.order.vendor_replied": "{supplier} का जवाब: {text}",
  "k2.order.none": "कोई खुला ऑर्डर नहीं मिला।",
  "k2.order.status": "{supplier} का ऑर्डर: {state}।",
  "k2.order.status_reply": "{supplier} का ऑर्डर: {state}। उन्होंने कहा: {text}",
  "k2.qty.both": "{qty} × {pack} = {spoken}",
  "k2.delivery.started": "{supplier} की डिलीवरी। हर पैकेट एक-एक करके कैमरे को दिखाइए।",
  "k2.delivery.none": "कोई डिलीवरी खुली नहीं है। कहिए: डिलीवरी शुरू करो।",
  "k2.delivery.readback": "{product} के {counted} गिने। ऑर्डर {expected} था। {counted} सेव करूँ?",
  "k2.delivery.saved": "{product} के {counted} सेव किए।",
  "k2.delivery.nothing_seen": "मुझे कोई {product} नहीं दिखा। हर पैकेट कैमरे को दिखाइए।",
  "k2.invoice.match": "बिल सही है। कुल {total} रुपये।",
  "k2.pay.plan": "{amount} रुपये दीजिए: {notes}। नोट कैमरे को दिखाइए।",
  "k2.pay.cash_q": "{amount} रुपये गिने गए। {supplier} को देकर पक्का करूँ?",
  "k2.pay.cash_wrong": "मुझे {seen} रुपये दिख रहे हैं, पर {amount} होने चाहिए।",
  "k2.pay.upi_q": "अपने UPI ऐप से {supplier} को {amount} रुपये भेजिए, फिर हाँ कहिए।",
  "k2.pay.done": "{supplier} को {amount} रुपये का भुगतान दर्ज हुआ।",
  "k2.pay.not_ready": "भुगतान से पहले डिलीवरी और बिल जाँच लीजिए।",
  "k2.cash.unclear": "नोट साफ़ नहीं दिख रहे। फिर से दिखाइए।",
  "k2.sale.price": "{items}। कुल {total} रुपये। कैश या UPI?",
  "k2.sale.none": "कोई खुली बिक्री नहीं है।",
  "k2.sale.change": "{tendered} मिले। {change} रुपये वापस दीजिए: {notes}। छुट्टे कैमरे को दिखाइए।",
  "k2.sale.change_wrong": "यह छुट्टा {seen} रुपये है। {change} होना चाहिए।",
  "k2.sale.cash_q": "{tendered} मिले, छुट्टा {change}। बिक्री पूरी करूँ?",
  "k2.sale.upi_wait": "पेमेंट साउंडबॉक्स का इंतज़ार कर रहा हूँ।",
  "k2.sale.upi_heard_q": "UPI से {amount} रुपये मिले। बिक्री पूरी करूँ?",
  "k2.sale.upi_wrong": "साउंडबॉक्स ने {heard} रुपये कहा, पर बिल {total} का है।",
  "k2.sale.upi_timeout_q": "साउंडबॉक्स सुनाई नहीं दिया। क्या UPI से {total} रुपये मिले?",
  "k2.sale.done": "बिक्री पूरी हुई।",
  "k2.sale.low_stock": "{product} सिर्फ़ {left} बचा है। और ऑर्डर करूँ?",
  "k2.stock.one": "{product}: {qty} स्टॉक में।",
  "k2.stock.all": "स्टॉक: {list}।",
  "k2.expiry.none": "आज या कल कुछ भी एक्सपायर नहीं हो रहा।",
  "k2.expiry.list": "{list}। इन्हें पहले बेचिए या लौटाइए।",
  "k2.expiry.item": "{product} {qty} {when}",
  "k2.when.expired": "पहले ही एक्सपायर",
  "k2.when.today": "आज एक्सपायर",
  "k2.when.tomorrow": "कल एक्सपायर"
}
```

- [ ] **Step 2: Write the failing webhook tests**

```python
# services/live/tests/k2/test_webhooks.py
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi import FastAPI

from aira_live.messaging.webhooks import router
from aira_live.tools import _repo

from .conftest import SHOP

app = FastAPI()
app.include_router(router)


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def wa_payload(frm: str, text: str, context_id: str | None = None) -> dict:
    msg = {"from": frm, "id": "wamid.IN", "type": "text", "text": {"body": text}}
    if context_id:
        msg["context"] = {"id": context_id}
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "messages", "value": {"messages": [msg]}}]}]}


async def _order(oid: str, state: str, updated: str) -> None:
    await _repo.save_order(SHOP, {"id": oid, "supplierId": "sakthi_vendor", "supplierName": "Sakthi curd vendor",
                                  "state": state, "lang": "en", "items": [], "updatedAt": updated})


async def test_verify_ok(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v123")
    async with client() as c:
        r = await c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "v123",
                                                     "hub.challenge": "4242"})
    assert r.status_code == 200 and r.text == "4242"


async def test_verify_bad_token_403(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v123")
    async with client() as c:
        r = await c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                     "hub.challenge": "1"})
    assert r.status_code == 403


async def test_vendor_reply_by_context_id_updates_order_and_notifies(store, notified, monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    await _order("o1", "ORDER_PLACED", "2026-10-12T09:00:00")
    await _repo.index_message("whatsapp", "wamid.1", SHOP, "o1")
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", json=wa_payload("919000000002", "OK, delivery at 6 pm", "wamid.1"))
    assert r.status_code == 200 and r.json()["handled"] == 1
    assert (await _repo.get_order(SHOP, "o1"))["vendorReply"]["text"] == "OK, delivery at 6 pm"
    assert notified == [(SHOP, "Sakthi curd vendor replied: OK, delivery at 6 pm", "ack")]


async def test_vendor_reply_by_sender_phone_matches_latest_open_order(store, notified, monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    await _order("o1", "ORDER_PLACED", "2026-10-12T08:00:00")
    await _order("o2", "DELIVERY_PENDING", "2026-10-12T09:30:00")
    await _repo.index_contact("whatsapp", "919000000002", SHOP, "sakthi_vendor")
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", json=wa_payload("919000000002", "Coming in 10 minutes"))
    assert r.json()["handled"] == 1
    assert (await _repo.get_order(SHOP, "o2"))["vendorReply"]["text"] == "Coming in 10 minutes"
    assert "vendorReply" not in await _repo.get_order(SHOP, "o1")


async def test_webhook_unknown_sender_is_ignored(store, notified, monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", json=wa_payload("919999999999", "hello"))
    assert r.status_code == 200 and r.json()["handled"] == 0 and notified == []


async def test_webhook_malformed_payload_is_200(store, monkeypatch):
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 200 and r.json()["handled"] == 0


async def test_webhook_bad_signature_403(store, notified, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
    body = json.dumps(wa_payload("919000000002", "hi")).encode()
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    async with client() as c:
        bad = await c.post("/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": "sha256=dead"})
        ok = await c.post("/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": good})
    assert bad.status_code == 403 and ok.status_code == 200


async def test_telegram_reply_by_reply_to_message(store, notified, monkeypatch):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    await _order("o3", "ORDER_PLACED", "2026-10-12T10:00:00")
    await _repo.index_message("telegram", "555:77", SHOP, "o3")
    update = {"message": {"chat": {"id": 555}, "text": "Will deliver at 5", "reply_to_message": {"message_id": 77}}}
    async with client() as c:
        r = await c.post("/webhooks/telegram", json=update)
    assert r.json()["handled"] == 1
    assert (await _repo.get_order(SHOP, "o3"))["vendorReply"]["channel"] == "telegram"
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `cd services/live && uv run pytest tests/k2/test_webhooks.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.messaging.webhooks'`.

- [ ] **Step 4: Implement the webhooks**

Verify the payload fields against https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples and https://core.telegram.org/bots/api#update.

```python
# services/live/aira_live/messaging/webhooks.py
"""Vendor replies → order.vendorReply, spoken to Murugan via sessions.notify.
Always 200 for unknown senders or malformed bodies (Meta retries non-200 responses); 403 only for bad auth."""
import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from aira_live.business.clock import now_iso
from aira_live.i18n import t
from aira_live.messaging.clicktochat import normalize_in_phone
from aira_live.tools import _repo

router = APIRouter()
log = logging.getLogger(__name__)
OPEN_STATES = ["ORDER_PLACED", "DELIVERY_PENDING"]


async def _notify(shop_id: str, text: str) -> None:
    from aira_live import sessions
    await sessions.notify(shop_id, text, "ack")


@router.get("/webhooks/whatsapp")
async def whatsapp_verify(request: Request):
    q = request.query_params
    expected = os.getenv("WHATSAPP_VERIFY_TOKEN")
    if q.get("hub.mode") == "subscribe" and expected and q.get("hub.verify_token") == expected:
        return PlainTextResponse(q.get("hub.challenge", ""))
    return Response(status_code=403)


def _signature_ok(body: bytes, header: str | None) -> bool:
    secret = os.getenv("WHATSAPP_APP_SECRET")
    if not secret:
        return True
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header[len("sha256="):])


def _wa_text_messages(payload: dict):
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for m in (change.get("value") or {}).get("messages") or []:
                if m.get("type") == "text":
                    yield m.get("from", ""), (m.get("text") or {}).get("body", ""), (m.get("context") or {}).get("id")


async def _apply_reply(channel: str, msg_key: str | None, contact: str, text: str) -> bool:
    order, shop_id = None, None
    ref = await _repo.lookup_message(channel, msg_key) if msg_key else None
    if ref:
        shop_id = ref["shopId"]
        order = await _repo.get_order(shop_id, ref["orderId"])
    if order is None:
        who = await _repo.lookup_contact(channel, contact)
        if not who:
            return False
        shop_id = who["shopId"]
        order = await _repo.latest_order(shop_id, OPEN_STATES, supplier_id=who["supplierId"])
        if order is None:
            return False
    now = now_iso()
    await _repo.save_order(shop_id, {**order, "vendorReply": {"text": text, "at": now, "channel": channel},
                                     "updatedAt": now})
    supplier = order.get("supplierName") or order.get("supplierId", "")
    await _notify(shop_id, t("k2.order.vendor_replied", order.get("lang", "en"), supplier=supplier, text=text))
    return True


@router.post("/webhooks/whatsapp")
async def whatsapp_in(request: Request):
    body = await request.body()
    if not _signature_ok(body, request.headers.get("x-hub-signature-256")):
        return Response(status_code=403)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return {"ok": True, "handled": 0}
    handled = 0
    for frm, text, ctx in _wa_text_messages(payload if isinstance(payload, dict) else {}):
        try:
            handled += int(await _apply_reply("whatsapp", ctx, normalize_in_phone(frm), text))
        except Exception:                                     # one bad message never blocks the rest
            log.exception("whatsapp reply failed from=%s", frm)
    return {"ok": True, "handled": handled}


@router.post("/webhooks/telegram")
async def telegram_in(request: Request):
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if secret and request.headers.get("x-telegram-bot-api-secret-token") != secret:
        return Response(status_code=403)
    try:
        update = await request.json()
    except ValueError:
        return {"ok": True, "handled": 0}
    msg = (update or {}).get("message") or {}
    chat = str((msg.get("chat") or {}).get("id", ""))
    text = msg.get("text", "")
    if not chat or not text:
        return {"ok": True, "handled": 0}
    reply_to = (msg.get("reply_to_message") or {}).get("message_id")
    key = f"{chat}:{reply_to}" if reply_to is not None else None
    return {"ok": True, "handled": int(await _apply_reply("telegram", key, chat, text))}
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `cd services/live && uv run pytest tests/k2/test_webhooks.py -q`
Expected: `8 passed`.

- [ ] **Step 6: Commit**

```bash
git add services/live/aira_live/messaging/webhooks.py services/live/aira_live/i18n services/live/tests/k2/test_webhooks.py
git commit -m "feat(messaging): WhatsApp + Telegram vendor-reply webhooks spoken to the owner; k2 speech keys"
```

After merge: Saravana mounts the router, and the webhook URL `https://<aira-live>/webhooks/whatsapp` (with `WHATSAPP_VERIFY_TOKEN`) is entered in the Meta app's WhatsApp → Configuration.

---

### Task 3: Order tools: `order_create` (propose → WhatsApp) and `order_status`

**Files:**
- Modify (replace the foundation stub): `services/live/aira_live/tools/orders.py`
- Test: `services/live/tests/k2/test_orders.py`

**Interfaces:**
- Consumes:
  - `catalog.resolve_supplier`, `catalog.resolve`, `catalog.to_selling_units` (`kanish/01`)
  - `states.transition`, `outbox.order_state`, `audit.log`, `hints.say`
  - `_ids.doc_id_for`, `paths`, `get_store`
  - `ids.current_idem_key()`
  - `confirm.propose`, `confirm.register_committer`
  - `messaging.send_order`, `_repo`
  - `contracts.StateMsg`, `i18n.t`
- Produces:
  - Live tools (names and signatures already in `TOOL_FUNCS`):
    - `order_create(supplier: str, items: list[dict]) -> dict`. `items` = `[{"product": str, "qty": number, "unit": "L"|"ml"|"kg"|"g"|"pack"|"jar"|"box"}]`; `"name"` is accepted as an alias of `"product"`, and `unit` is optional.
    - `order_status(order_id: str | None = None) -> dict`
  - Committers: `"order.send"`, `"order.mark_sent"`
  - Order doc fields: `id, supplierId, supplierName, channel, items[{sku, name, qty, unit, agreedPrice}], state, lang, idemKey, channelMsgId?, vendorReply?, createdAt, updatedAt, placedAt?`

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/k2/test_orders.py
import aira_live.messaging as messaging
from aira_live.business import hints, paths
from aira_live.tools import _repo, orders

from .conftest import SHOP, use_call


async def _name(store, sku):
    return (await store.get(paths.product(SHOP, sku)))["name"]


async def test_order_create_resolves_and_proposes(store, session, proposals):
    r = await orders.order_create("sakthi vendor", [{"product": "sakthi curd", "qty": 30, "unit": "L"}])
    assert r["needs_confirm"] is True
    p = proposals[0]
    assert (p["kind"], p["committer"]) == ("order", "order.send")
    o = await _repo.get_order(SHOP, p["payload"]["order_id"])
    assert (o["state"], o["supplierId"], o["lang"]) == ("DRAFT", "sakthi_vendor", "en")
    line = o["items"][0]
    assert (line["sku"], line["qty"], line["unit"]) == ("sakthi_curd_1l", 30.0, "L")
    assert isinstance(line["agreedPrice"], int)
    assert p["prompt"] == f"Order 30 L {await _name(store, 'sakthi_curd_1l')} from Sakthi curd vendor?"


async def test_order_create_converts_kg_paneer_and_reads_both_forms(store, session, proposals):
    await orders.order_create("aavin vendor", [{"product": "paneer", "qty": 5, "unit": "kg"}])
    line = (await _repo.get_order(SHOP, proposals[0]["payload"]["order_id"]))["items"][0]
    assert (line["sku"], line["qty"], line["unit"]) == ("aavin_paneer_200g", 25.0, "pack")
    assert "25 × " in proposals[0]["prompt"] and "= 5 kg" in proposals[0]["prompt"]


async def test_order_create_unknown_supplier_says_hint(store, session, proposals):
    r = await orders.order_create("xyz traders", [{"product": "milk", "qty": 10, "unit": "L"}])
    assert r["ok"] is False and r["say"] == hints.say("SUPPLIER_UNKNOWN", "en", supplier="xyz traders")
    assert proposals == []


async def test_order_create_retry_reuses_draft(store, session, proposals):
    for _ in range(2):                                         # same Live tool call retried
        await orders.order_create("sakthi vendor", [{"product": "sakthi curd", "qty": 30, "unit": "L"}])
    assert proposals[0]["payload"]["order_id"] == proposals[1]["payload"]["order_id"]
    assert len(await store.list(f"shops/{SHOP}/orders")) == 1


async def _drafted(proposals):
    await orders.order_create("sakthi vendor", [{"product": "sakthi curd", "qty": 30, "unit": "L"}])
    return proposals[0]["payload"]


async def test_commit_send_places_order_and_publishes(store, session, proposals, published, monkeypatch):
    payload = await _drafted(proposals)

    async def fake_send(shop_id, supplier, order):
        assert supplier["id"] == "sakthi_vendor"
        return {"channel": "whatsapp", "messageId": "wamid.9"}

    monkeypatch.setattr(messaging, "send_order", fake_send)
    use_call("tc-confirm")
    r = await orders._commit_send(payload, None)
    o = await _repo.get_order(SHOP, payload["order_id"])
    assert (o["state"], o["channelMsgId"]) == ("ORDER_PLACED", "wamid.9")
    assert ("orders", {"shop_id": SHOP, "order_id": o["id"], "state": "ORDER_PLACED"}) in published
    assert r["say"] == "Order sent to Sakthi curd vendor."
    assert session.sent_of("state")[-1].state == "ORDER_PLACED"


async def test_commit_send_twice_is_idempotent(store, session, proposals, monkeypatch):
    payload = await _drafted(proposals)
    calls = []

    async def fake_send(shop_id, supplier, order):
        calls.append(order["id"])
        return {"channel": "whatsapp", "messageId": "wamid.9"}

    monkeypatch.setattr(messaging, "send_order", fake_send)
    await orders._commit_send(payload, None)
    again = await orders._commit_send(payload, None)
    assert calls == [payload["order_id"]] and again["say"] == "That order was already sent."


async def test_commit_send_fallback_keeps_draft_and_opens_url(store, session, proposals, monkeypatch):
    payload = await _drafted(proposals)

    async def fake_send(shop_id, supplier, order):
        return {"channel": "whatsapp_link", "fallbackUrl": "https://wa.me/919000000002?text=x"}

    monkeypatch.setattr(messaging, "send_order", fake_send)
    r = await orders._commit_send(payload, None)
    assert (await _repo.get_order(SHOP, payload["order_id"]))["state"] == "DRAFT"
    assert session.sent_of("state")[-1].summary == "OPEN_URL https://wa.me/919000000002?text=x"
    assert proposals[-1]["committer"] == "order.mark_sent" and r["needs_confirm"] is True
    await orders._commit_mark_sent(proposals[-1]["payload"], None)
    o = await _repo.get_order(SHOP, payload["order_id"])
    assert (o["state"], o["channel"]) == ("ORDER_PLACED", "manual")


async def test_order_status_reports_vendor_reply(store, session):
    await _repo.save_order(SHOP, {"id": "o9", "supplierId": "sakthi_vendor", "supplierName": "Sakthi curd vendor",
                                  "state": "ORDER_PLACED", "items": [], "lang": "en",
                                  "vendorReply": {"text": "OK, 6 pm"}, "updatedAt": "2026-10-12T09:00:00"})
    r = await orders.order_status()
    assert r["say"] == "Order to Sakthi curd vendor: order placed. They said: OK, 6 pm"
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `cd services/live && uv run pytest tests/k2/test_orders.py -q`
Expected: FAIL with `NotImplementedError` (from the foundation stub), or `AttributeError: module 'aira_live.tools.orders' has no attribute '_commit_send'`.

- [ ] **Step 3: Implement `tools/orders.py`**

```python
# services/live/aira_live/tools/orders.py — OWNER: Kanish (kanish/02)
"""Supplier ordering. The model proposes (read-back); committers 'order.send' / 'order.mark_sent' commit."""
import aira_live.messaging as messaging
from aira_live import confirm, ids
from aira_live.business import audit, catalog, hints, outbox, paths
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso
from aira_live.business.states import transition
from aira_live.business.store import get_store
from aira_live.contracts import StateMsg
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty, items_text
from aira_live.session import get_session
from aira_live.tools import _repo

ACTIVE_STATES = ["ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED", "DISCREPANCY",
                 "PAYMENT_PENDING", "PAYMENT_CONFIRMED"]


async def product_doc(shop_id: str, sku: str) -> dict:
    return {**(await get_store().get(paths.product(shop_id, sku))), "sku": sku}


def qty_phrase(product: dict, qty: float, unit: str, spoken_qty: float, spoken_unit: str, lang: str) -> str:
    """'30 L Sakthi Curd 1 L' or '25 pack Aavin Paneer 200 g (25 × 200 g pack = 5 kg)'."""
    base = f"{fmt_qty(qty)} {unit} {product['name']}"
    if spoken_unit and spoken_unit.lower() != unit.lower():
        both = t("k2.qty.both", lang, qty=fmt_qty(qty), pack=product.get("pack", unit),
                 spoken=f"{fmt_qty(spoken_qty)} {spoken_unit}")
        return f"{base} ({both})"
    return base


async def resolve_lines(shop_id: str, items: list[dict], lang: str) -> tuple[list[dict], list[str], str | None]:
    """Spoken items → order/sale lines in selling units. Returns (lines, spoken phrases, error_say)."""
    lines, phrases = [], []
    for it in items:
        spoken = str(it.get("product") or it.get("name") or "").strip()
        ref = await catalog.resolve(shop_id, spoken, lang)
        if ref is None:
            return [], [], hints.say("PRODUCT_UNKNOWN", lang, product=spoken)
        product = await product_doc(shop_id, ref.sku)
        spoken_qty = float(it.get("qty") or 0)
        spoken_unit = str(it.get("unit") or product["unit"])
        try:
            qty, unit = catalog.to_selling_units(product, spoken_qty, spoken_unit)
        except ValueError:
            return [], [], hints.say("PRODUCT_UNKNOWN", lang, product=spoken)
        if qty <= 0:
            return [], [], hints.say("PRODUCT_UNKNOWN", lang, product=spoken)
        lines.append({"sku": ref.sku, "name": product["name"], "qty": float(qty), "unit": unit,
                      "agreedPrice": int(product["costPrice"])})
        phrases.append(qty_phrase(product, qty, unit, spoken_qty, spoken_unit, lang))
    return lines, phrases, None


async def order_create(supplier: str, items: list[dict]) -> dict:
    """Order stock from a supplier when the OWNER asks (e.g. "order 30 litres Sakthi curd from the Sakthi vendor").
    items: [{"product": "sakthi curd", "qty": 30, "unit": "L"}]; unit may be L, ml, kg, g, pack, jar or box.
    Reads the order back and waits for the owner's yes before anything is sent."""
    s = get_session()
    sup = await catalog.resolve_supplier(s.shop_id, supplier, s.lang)
    if sup is None:
        return {"ok": False, "say": hints.say("SUPPLIER_UNKNOWN", s.lang, supplier=supplier)}
    lines, phrases, err = await resolve_lines(s.shop_id, items, s.lang)
    if err:
        return {"ok": False, "say": err}
    idem = ids.current_idem_key()
    oid = doc_id_for("o", idem)
    if await _repo.get_order(s.shop_id, oid) is None:          # a retried tool call reuses the same draft
        now = now_iso()
        await _repo.save_order(s.shop_id, {
            "id": oid, "supplierId": sup["id"], "supplierName": sup["name"], "channel": sup.get("channel", "whatsapp"),
            "items": lines, "state": "DRAFT", "lang": s.lang, "idemKey": idem, "createdAt": now, "updatedAt": now})
    prompt = t("k2.order.read_back", s.lang, items=", ".join(phrases), supplier=sup["name"])
    return await confirm.propose("order", prompt, {"order_id": oid, "idem": idem}, "order.send")


async def _supplier(shop_id: str, supplier_id: str) -> dict:
    return {"id": supplier_id, **(await get_store().get(paths.supplier(shop_id, supplier_id)))}


async def _place(s, order: dict, channel: str, message_id: str | None = None) -> dict:
    now = now_iso()
    order = {**order, "state": transition("order", order["state"], "ORDER_PLACED"), "channel": channel,
             "channelMsgId": message_id, "placedAt": now, "updatedAt": now}
    await _repo.save_order(s.shop_id, order)
    await outbox.order_state(s.shop_id, order["id"], "ORDER_PLACED")
    await audit.log(s.shop_id, "owner", "order_placed", "order", order["id"], channel)
    await s.send(StateMsg(entity="order", id=order["id"], state="ORDER_PLACED", summary=items_text(order["items"])))
    return {"ok": True, "orderId": order["id"], "state": "ORDER_PLACED",
            "say": t("k2.order.sent", s.lang, supplier=order["supplierName"])}


async def _commit_send(payload: dict, value: str | None) -> dict:
    s = get_session()
    order = await _repo.get_order(s.shop_id, payload["order_id"])
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    if order["state"] != "DRAFT":
        return {"ok": True, "orderId": order["id"], "say": t("k2.order.already", s.lang)}
    res = await messaging.send_order(s.shop_id, await _supplier(s.shop_id, order["supplierId"]), order)
    if res.get("messageId"):
        return await _place(s, order, res["channel"], res["messageId"])
    await s.send(StateMsg(entity="order", id=order["id"], state="DRAFT", summary=f"OPEN_URL {res['fallbackUrl']}"))
    first = t("k2.order.call" if res["channel"] == "call" else "k2.order.fallback", s.lang,
              supplier=order["supplierName"], items=items_text(order["items"]))
    follow = await confirm.propose("generic", t("k2.order.mark_sent_q", s.lang, supplier=order["supplierName"]),
                                   {"order_id": order["id"], "idem": payload["idem"]}, "order.mark_sent")
    return {**follow, "ok": True, "orderId": order["id"], "fallbackUrl": res["fallbackUrl"],
            "say": f"{first} {follow['say']}"}


async def _commit_mark_sent(payload: dict, value: str | None) -> dict:
    s = get_session()
    order = await _repo.get_order(s.shop_id, payload["order_id"])
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    if order["state"] != "DRAFT":
        return {"ok": True, "orderId": order["id"], "say": t("k2.order.already", s.lang)}
    return await _place(s, order, "manual")


async def order_status(order_id: str | None = None) -> dict:
    """Say where the latest (or given) supplier order stands, including the vendor's reply if any."""
    s = get_session()
    order = (await _repo.get_order(s.shop_id, order_id) if order_id
             else await _repo.latest_order(s.shop_id, ACTIVE_STATES))
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    reply = (order.get("vendorReply") or {}).get("text")
    state_words = order["state"].replace("_", " ").lower()
    say = t("k2.order.status_reply" if reply else "k2.order.status", s.lang,
            supplier=order["supplierName"], state=state_words, text=reply or "")
    return {"ok": True, "orderId": order["id"], "state": order["state"], "say": say}


confirm.register_committer("order.send", _commit_send)
confirm.register_committer("order.mark_sent", _commit_mark_sent)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd services/live && uv run pytest tests/k2/test_orders.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Try it through the tool harness**

This needs the Firestore emulator or the dev project, seeded with `uv run python -m aira_live.business.seed --catalog ../../content/catalog/murugan_dairy.json`.

```bash
cd services/live
uv run python -m aira_live.devtools.call_tool order_create \
  --args '{"supplier":"sakthi vendor","items":[{"product":"sakthi curd","qty":30,"unit":"L"}]}' --shop murugan_dairy
```

Expected: a `confirm` message whose prompt starts with "Order 30 L", and a returned `confirm_id`. Resolve it with `confirm_action`; your test phone gets the WhatsApp order, or the `OPEN_URL` fallback is printed.

- [ ] **Step 6: Commit**

```bash
git add services/live/aira_live/tools/orders.py services/live/tests/k2/test_orders.py
git commit -m "feat(tools): order_create with read-back + WhatsApp send committer, order_status"
```

---

### Task 4: Delivery tools: live count (one by one / crate), verified stock, invoice check

**Files:**
- Modify (replace the foundation stub): `services/live/aira_live/tools/delivery.py`
- Create **only if absent** (models + stubs; Sarmitha's plans own and replace them): `services/live/aira_live/vision/count.py`, `services/live/aira_live/vision/cash.py`
- Test: `services/live/tests/k2/test_delivery.py`

**Interfaces:**
- Consumes:
  - `vision.identify.identify_product(frame, candidates) -> IdentifyResult` and `ProductRef`
  - `vision.count.count_in_frame(frame, item_label) -> CountResult` (`aim_hint` is an i18n key)
  - `vision.invoice.read_invoice(frames) -> InvoiceRead`
  - `reconcile.compare`, `ReconcileResult`, `ledger.apply`, `ledger.on_hand`
  - `outbox.order_state`, `outbox.discrepancies`, `hints.say`, `catalog.resolve`, `catalog.to_selling_units`
  - `Session.frames.wait_for`, `Session.request_frames`
  - `RequestFrameMsg`, `CountMsg`, `StateMsg`, `CueMsg`
- Produces:
  - Live tools: `delivery_start(order_id=None)`, `delivery_count(sku, mode="one_by_one")`, `invoice_check(order_id)`
  - Committer: `"delivery.record_count"`
  - `OnePassCounter`, a pure de-duplicator
  - Delivery doc: `id, orderId, items[{sku, name, unit, expectedQty, countedQty, method, confidence, countIdem}], invoice?, discrepancies[], state, createdAt, updatedAt`

**Counting rule (one by one):**
- A pack counts when the identified SKU matches with confidence ≥ 0.7, **and** either:
  - it is the first pack, **or**
  - an absence (a frame without that SKU) was seen since the last counted pack, **and** at least 1.2 s have passed.
- A pack held continuously never counts twice.
- A one-frame flicker of less than 1.2 s is ignored.
- Murugan is told: "show each pack, then lower it". Every new count is spoken through `CountMsg`, and the final number is read back for a "yes".

- [ ] **Step 1: Create the vision models if Sarmitha's files are not merged yet**

Skip any file that already exists.

```python
# services/live/aira_live/vision/count.py — OWNER: Sarmitha. Models + stub until sarmitha/01 lands.
from pydantic import BaseModel

from aira_live.frames import Frame


class CountResult(BaseModel):
    count: int | None
    confidence: float
    needs_retake: bool
    aim_hint: str          # i18n key, e.g. "vision.aim.move_back"; "" when fine


async def count_in_frame(frame: Frame, item_label: str) -> CountResult:
    raise NotImplementedError("sarmitha/01 implements count_in_frame")
```

```python
# services/live/aira_live/vision/cash.py — OWNER: Sarmitha. Models + stub until sarmitha/02 lands.
from pydantic import BaseModel

from aira_live.frames import Frame


class CashRead(BaseModel):
    notes: list[dict]      # [{denomination: int, count: int}]
    total: int | None
    agreed: bool
    confidence: float
    hint_key: str = ""     # i18n key, e.g. "cash.spread_notes"; "" when the read is usable


async def read_cash(frames: list[Frame]) -> CashRead:
    raise NotImplementedError("sarmitha/02 implements read_cash")
```

- [ ] **Step 2: Write the failing tests**

```python
# services/live/tests/k2/test_delivery.py
from aira_live.business import catalog, hints, ledger, paths
from aira_live.i18n import t
from aira_live.tools import _repo, delivery
from aira_live.tools.delivery import OnePassCounter
from aira_live.vision import count as vcount
from aira_live.vision import identify as vid
from aira_live.vision import invoice as vinv
from aira_live.vision.count import CountResult
from aira_live.vision.identify import IdentifyResult
from aira_live.vision.invoice import InvoiceLine, InvoiceRead

from .conftest import SHOP

CURD = "sakthi_curd_1l"
GHEE = "aavin_ghee_500ml"


def test_counter_holding_same_pack_counts_once():
    c = OnePassCounter(CURD)
    for ts in range(0, 4000, 500):
        c.observe(ts, CURD, 0.9)
    assert c.counted == 1


def test_counter_counts_after_absence_and_gap():
    c = OnePassCounter(CURD)
    for ts, sku in [(0, CURD), (700, None), (1500, CURD), (2200, None), (3000, CURD)]:
        c.observe(ts, sku, 0.9)
    assert c.counted == 3


def test_counter_flicker_is_not_a_new_pack():
    c = OnePassCounter(CURD)
    for ts, sku in [(0, CURD), (300, None), (600, CURD), (1300, CURD)]:
        c.observe(ts, sku, 0.9)
    assert c.counted == 1


def test_counter_ignores_other_sku_and_low_confidence():
    c = OnePassCounter(CURD)
    c.observe(0, "aavin_milk_500ml", 0.95)
    c.observe(2000, CURD, 0.4)
    assert c.counted == 0


async def _placed(store, sku=CURD, qty=3.0, price=50) -> dict:
    p = await store.get(paths.product(SHOP, sku))
    order = {"id": "o1", "supplierId": "sakthi_vendor", "supplierName": "Sakthi curd vendor", "state": "ORDER_PLACED",
             "lang": "en", "idemKey": "k-o1", "updatedAt": "2026-10-12T09:00:00",
             "items": [{"sku": sku, "name": p["name"], "qty": qty, "unit": p["unit"], "agreedPrice": price}]}
    await _repo.save_order(SHOP, order)
    return order


def _identify_by_frame_id(mapping: dict):
    async def fake(frame, candidates):
        return IdentifyResult(sku=mapping.get(frame.id), confidence=0.9, alternatives=[])
    return fake


async def _count_three(session, monkeypatch, frames=None):
    monkeypatch.setattr(delivery, "COUNT_IDLE_S", 0.2)
    monkeypatch.setattr(vid, "identify_product", _identify_by_frame_id({"p1": CURD, "p2": CURD, "p3": CURD}),
                        raising=False)
    session.stream("identify", frames or [("p1", 1000), ("g1", 1800), ("p2", 2600), ("g2", 3400), ("p3", 4200)])
    return await delivery.delivery_count(CURD)


async def test_delivery_start_moves_to_delivery_pending(store, session, published):
    await _placed(store)
    r = await delivery.delivery_start()
    assert (await _repo.get_order(SHOP, "o1"))["state"] == "DELIVERY_PENDING"
    d = await _repo.get_delivery(SHOP, r["deliveryId"])
    assert (d["items"][0]["sku"], d["items"][0]["expectedQty"], d["items"][0]["countedQty"]) == (CURD, 3.0, None)
    assert ("orders", {"shop_id": SHOP, "order_id": "o1", "state": "DELIVERY_PENDING"}) in published
    assert r["say"] == "Delivery from Sakthi curd vendor. Show each pack to the camera, one at a time."


async def test_delivery_count_one_by_one_streams_and_proposes(store, session, proposals, monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    await _count_three(session, monkeypatch)
    counts = session.sent_of("count")
    assert [m.counted for m in counts] == [1, 2, 3, 3] and counts[-1].final is True
    assert any(m.stop for m in session.sent_of("request_frame"))
    p = proposals[0]
    assert (p["kind"], p["committer"], p["payload"]["qty"]) == ("generic", "delivery.record_count", 3.0)


async def test_delivery_commit_is_idempotent(store, session, proposals, monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    await _count_three(session, monkeypatch)
    before = await ledger.on_hand(SHOP, CURD)
    payload = proposals[0]["payload"]
    await delivery._commit_count(payload, None)
    await delivery._commit_count(payload, None)                 # retried confirm
    assert await ledger.on_hand(SHOP, CURD) == before + 3.0
    d = await _repo.get_delivery(SHOP, payload["delivery_id"])
    assert d["items"][0]["countedQty"] == 3.0


async def test_short_count_proposes_discrepancy_and_publishes(store, session, proposals, published, monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    await _count_three(session, monkeypatch, frames=[("p1", 1000), ("g1", 1800), ("p2", 2600)])
    p = proposals[0]
    assert (p["kind"], p["payload"]["qty"]) == ("discrepancy", 2.0)
    await delivery._commit_count(p["payload"], None)
    assert ("discrepancies", {"shop_id": SHOP, "delivery_id": p["payload"]["delivery_id"],
                              "field": f"SHORT_DELIVERY:{CURD}", "expected": 3.0, "actual": 2.0}) in published


async def test_delivery_count_crate_retake_speaks_aim_hint(store, session, proposals, monkeypatch):
    await _placed(store, sku=GHEE, qty=6.0, price=260)
    await delivery.delivery_start()
    session.queue("count", "c1", "c2")

    async def blurry(frame, label):
        return CountResult(count=None, confidence=0.3, needs_retake=True, aim_hint="vision.aim.move_back")

    monkeypatch.setattr(vcount, "count_in_frame", blurry)
    r = await delivery.delivery_count(GHEE, mode="crate")
    assert r["needs_retake"] is True and r["say"] == t("vision.aim.move_back", "en")
    assert proposals == []


async def test_delivery_count_crate_agreeing_reads_propose(store, session, proposals, monkeypatch):
    await _placed(store, sku=GHEE, qty=6.0, price=260)
    await delivery.delivery_start()
    session.queue("count", "c1", "c2")

    async def six(frame, label):
        return CountResult(count=6, confidence=0.9, needs_retake=False, aim_hint="")

    monkeypatch.setattr(vcount, "count_in_frame", six)
    await delivery.delivery_count(GHEE, mode="crate")
    product = {**(await store.get(paths.product(SHOP, GHEE))), "sku": GHEE}
    assert proposals[0]["payload"]["qty"] == catalog.to_selling_units(product, 6, "pack")[0]


async def _counted_delivery(store, session, counted=3.0):
    await _placed(store)
    r = await delivery.delivery_start()
    d = await _repo.get_delivery(SHOP, r["deliveryId"])
    d["items"][0]["countedQty"] = counted
    await _repo.save_delivery(SHOP, d)


def _invoice(qty, total, agreed=True):
    async def fake(frames):
        return InvoiceRead(supplier="Sakthi", date=None, agreed=agreed, disagreements=[] if agreed else ["total"],
                           lines=[InvoiceLine(name="Sakthi Curd 1 L", qty=qty, unit="L", unit_price=50,
                                              line_total=int(qty * 50))], total=total)
    return fake


async def test_invoice_check_match_reconciles(store, session, published, monkeypatch):
    await _counted_delivery(store, session)
    session.queue("invoice", "i1", "i2")
    monkeypatch.setattr(vinv, "read_invoice", _invoice(3, 150), raising=False)
    r = await delivery.invoice_check("o1")
    assert r["state"] == "DELIVERY_RECONCILED" and r["say"] == "The bill matches. Total 150 rupees."
    assert ("orders", {"shop_id": SHOP, "order_id": "o1", "state": "DELIVERY_RECONCILED"}) in published


async def test_invoice_check_mismatch_marks_discrepancy(store, session, published, monkeypatch):
    await _counted_delivery(store, session)
    session.queue("invoice", "i1", "i2")
    monkeypatch.setattr(vinv, "read_invoice", _invoice(4, 200), raising=False)
    r = await delivery.invoice_check("o1")
    name = (await store.get(paths.product(SHOP, CURD)))["name"]
    assert r["state"] == "DISCREPANCY"
    assert r["say"].startswith(hints.say("INVOICE_QTY_MISMATCH", "en", product=name, expected="3", actual="4"))
    assert any(topic == "discrepancies" and p["field"] == f"INVOICE_QTY_MISMATCH:{CURD}" for topic, p in published)


async def test_invoice_unreadable_asks_retake(store, session, monkeypatch):
    await _counted_delivery(store, session)
    session.queue("invoice", "i1", "i2")
    monkeypatch.setattr(vinv, "read_invoice", _invoice(3, 150, agreed=False), raising=False)
    r = await delivery.invoice_check("o1")
    assert r["needs_retake"] is True and r["say"] == hints.say("INVOICE_UNREADABLE", "en")
    assert (await _repo.get_order(SHOP, "o1"))["state"] == "DELIVERY_PENDING"
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `cd services/live && uv run pytest tests/k2/test_delivery.py -q`
Expected: FAIL with `ImportError: cannot import name 'OnePassCounter'`.

- [ ] **Step 4: Implement `tools/delivery.py`**

```python
# services/live/aira_live/tools/delivery.py — OWNER: Kanish (kanish/02); uses Sarmitha's vision
"""Delivery: open → count (verified quantity only) → invoice check. Committer 'delivery.record_count'."""
import asyncio
from dataclasses import dataclass, field
from typing import Literal

from aira_live import confirm, ids
from aira_live.business import audit, catalog, hints, ledger, outbox, paths, reconcile
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso
from aira_live.business.states import transition
from aira_live.business.store import get_store
from aira_live.contracts import CountMsg, CueMsg, RequestFrameMsg, StateMsg
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import get_session
from aira_live.tools import _repo
from aira_live.vision import count as vcount
from aira_live.vision import identify as vid
from aira_live.vision import invoice as vinv

COUNT_IDLE_S = 8.0          # stop counting after this long without a frame
COUNT_MAX_S = 180.0         # hard cap per count session
COUNT_KINDS = {"SHORT_DELIVERY", "OVER_DELIVERY", "UNORDERED_ITEM"}   # published when the count is saved
_active: dict[str, str] = {}  # session_id → delivery_id


@dataclass
class OnePassCounter:
    sku: str
    min_confidence: float = 0.7
    gap_ms: float = 1200.0
    counted: int = 0
    confidences: list[float] = field(default_factory=list)
    _last_count_ts: float = 0.0
    _absent: bool = False

    def observe(self, ts_ms: float, sku: str | None, confidence: float) -> bool:
        """Feed one identified frame. True when it adds a new pack."""
        if sku != self.sku or confidence < self.min_confidence:
            self._absent = True
            return False
        if self.counted == 0 or (self._absent and ts_ms - self._last_count_ts >= self.gap_ms):
            self.counted += 1
            self.confidences.append(confidence)
            self._last_count_ts, self._absent = ts_ms, False
            return True
        self._absent = False                                   # same pack still (or again) in view
        return False

    @property
    def confidence(self) -> float:
        return min(self.confidences) if self.confidences else 0.0


async def _product(shop_id: str, sku: str) -> dict:
    return {**(await get_store().get(paths.product(shop_id, sku))), "sku": sku}


async def _resolve_sku(s, spoken: str, delivery: dict) -> str | None:
    if any(i["sku"] == spoken for i in delivery["items"]):
        return spoken
    ref = await catalog.resolve(s.shop_id, spoken, s.lang)
    return ref.sku if ref else None


async def _current_delivery(s) -> dict | None:
    did = _active.get(s.session_id)
    if did and (d := await _repo.get_delivery(s.shop_id, did)):
        return d
    order = await _repo.latest_order(s.shop_id, ["DELIVERY_PENDING", "DISCREPANCY"])
    return await _repo.delivery_for_order(s.shop_id, order["id"]) if order else None


async def delivery_start(order_id: str | None = None) -> dict:
    """Start checking a delivery that has arrived (latest placed order if no id is given)."""
    s = get_session()
    order = (await _repo.get_order(s.shop_id, order_id) if order_id
             else await _repo.latest_order(s.shop_id, ["ORDER_PLACED", "DELIVERY_PENDING"]))
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    if order["state"] == "ORDER_PLACED":
        order = {**order, "state": transition("order", "ORDER_PLACED", "DELIVERY_PENDING"), "updatedAt": now_iso()}
        await _repo.save_order(s.shop_id, order)
        await outbox.order_state(s.shop_id, order["id"], "DELIVERY_PENDING")
    d = await _repo.delivery_for_order(s.shop_id, order["id"])
    if d is None:
        now = now_iso()
        d = {"id": doc_id_for("d", order["idemKey"]), "orderId": order["id"], "state": "DELIVERY_PENDING",
             "discrepancies": [], "createdAt": now, "updatedAt": now,
             "items": [{"sku": it["sku"], "name": it["name"], "unit": it["unit"], "expectedQty": float(it["qty"]),
                        "countedQty": None, "method": None, "confidence": None, "countIdem": None}
                       for it in order["items"]]}
        await _repo.save_delivery(s.shop_id, d)
    _active[s.session_id] = d["id"]
    await s.send(StateMsg(entity="delivery", id=d["id"], state="DELIVERY_PENDING", summary=order["supplierName"]))
    return {"ok": True, "deliveryId": d["id"], "orderId": order["id"], "skus": [i["sku"] for i in d["items"]],
            "say": t("k2.delivery.started", s.lang, supplier=order["supplierName"])}


async def _count_one_by_one(s, product: dict, expected_packs: int | None) -> tuple[int, float]:
    rows = await get_store().list(paths.products(s.shop_id))
    candidates = [vid.ProductRef(sku=sku, name=p["name"], brand=p["brand"], pack=p["pack"]) for sku, p in rows]
    counter = OnePassCounter(product["sku"])
    latest = s.frames.latest(1, "identify")
    after = latest[0].ts if latest else 0.0
    loop = asyncio.get_running_loop()
    started = loop.time()
    await s.send(RequestFrameMsg(purpose="identify", continuous=True))
    try:
        while loop.time() - started < COUNT_MAX_S:
            got = await s.frames.wait_for(count=1, purpose="identify", after_ts=after, timeout_s=COUNT_IDLE_S)
            if not got:
                break                                           # owner stopped showing packs
            frame = got[0]
            after = frame.ts
            res = await vid.identify_product(frame, candidates)
            if counter.observe(frame.ts, res.sku, res.confidence):
                await s.send(CountMsg(sku=product["sku"], counted=counter.counted, expected=expected_packs,
                                      final=False, confidence=res.confidence))
                if expected_packs is not None and counter.counted >= expected_packs:
                    break
    finally:
        await s.send(RequestFrameMsg(purpose="identify", stop=True))
    return counter.counted, counter.confidence


async def delivery_count(sku: str, mode: Literal["one_by_one", "crate"] = "one_by_one") -> dict:
    """Count delivered packs of ONE product with the camera. one_by_one: the owner shows each pack, then lowers it.
    crate: one photo of a full crate or shelf. Reads the count back; only the confirmed count enters stock."""
    s = get_session()
    d = await _current_delivery(s)
    if d is None:
        return {"ok": False, "say": t("k2.delivery.none", s.lang)}
    real_sku = await _resolve_sku(s, sku, d)
    if real_sku is None:
        return {"ok": False, "say": hints.say("PRODUCT_UNKNOWN", s.lang, product=sku)}
    product = await _product(s.shop_id, real_sku)
    per_pack, unit = catalog.to_selling_units(product, 1, "pack")
    line = next((i for i in d["items"] if i["sku"] == real_sku), None)
    expected = float(line["expectedQty"]) if line else None
    expected_packs = round(expected / per_pack) if expected is not None else None

    if mode == "crate":
        frames = await s.request_frames("count", count=2, interval_ms=700)
        if len(frames) < 2:
            return {"ok": False, "needs_retake": True, "say": t("vision.aim.not_visible", s.lang)}
        reads = [await vcount.count_in_frame(f, product["name"]) for f in frames]
        for r in reads:
            if r.needs_retake or r.aim_hint:
                return {"ok": False, "needs_retake": True, "say": t(r.aim_hint or "vision.aim.show_items", s.lang)}
        if reads[0].count is None or reads[0].count != reads[1].count:
            return {"ok": False, "needs_retake": True, "say": t("vision.aim.spread_out", s.lang)}
        packs, conf = reads[0].count, min(r.confidence for r in reads)
    else:
        packs, conf = await _count_one_by_one(s, product, expected_packs)

    if packs == 0:
        return {"ok": False, "say": t("k2.delivery.nothing_seen", s.lang, product=product["name"])}
    qty = catalog.to_selling_units(product, packs, "pack")[0]
    await s.send(CountMsg(sku=real_sku, counted=packs, expected=expected_packs, final=True, confidence=conf))
    prompt = t("k2.delivery.readback", s.lang, counted=f"{fmt_qty(qty)} {unit}", product=product["name"],
               expected=f"{fmt_qty(expected)} {unit}" if expected is not None else "0")
    kind = "discrepancy" if expected is None or abs(qty - expected) > 1e-9 else "generic"
    payload = {"delivery_id": d["id"], "sku": real_sku, "qty": float(qty), "packs": packs, "method": mode,
               "confidence": conf, "idem": ids.current_idem_key()}
    return await confirm.propose(kind, prompt, payload, "delivery.record_count")


async def _commit_count(payload: dict, value: str | None) -> dict:
    s = get_session()
    d = await _repo.get_delivery(s.shop_id, payload["delivery_id"])
    if d is None:
        return {"ok": False, "say": t("k2.delivery.none", s.lang)}
    product = await _product(s.shop_id, payload["sku"])
    line = next((i for i in d["items"] if i["sku"] == payload["sku"]), None)
    if line and line.get("countIdem") == payload["idem"]:       # retried confirm: nothing new
        return {"ok": True, "say": t("k2.delivery.saved", s.lang, counted=fmt_qty(payload["qty"]),
                                     product=product["name"])}
    await ledger.apply(s.shop_id, payload["sku"], payload["qty"], "DELIVERY", "delivery", d["id"], payload["idem"])
    if line is None:
        line = {"sku": payload["sku"], "name": product["name"], "unit": product["unit"], "expectedQty": 0.0}
        d["items"].append(line)
    line.update(countedQty=payload["qty"], method=payload["method"], confidence=payload["confidence"],
                countIdem=payload["idem"])
    d = {**d, "updatedAt": now_iso()}
    await _repo.save_delivery(s.shop_id, d)
    if abs(payload["qty"] - float(line["expectedQty"])) > 1e-9:
        order = await _repo.get_order(s.shop_id, d["orderId"])
        ordered = [it for it in order["items"] if it["sku"] == payload["sku"]]
        await outbox.discrepancies(s.shop_id, d["id"],
                                   reconcile.compare(ordered, {payload["sku"]: payload["qty"]}, None))
    await audit.log(s.shop_id, "owner", "delivery_counted", "delivery", d["id"], f"{payload['sku']}={payload['qty']}")
    return {"ok": True, "say": t("k2.delivery.saved", s.lang, counted=fmt_qty(payload["qty"]),
                                 product=product["name"])}


async def _say_discrepancies(s, items: list[dict]) -> str:
    out = []
    for item in items[:2]:
        name = item.get("name", "")
        if item.get("sku"):
            name = (await _product(s.shop_id, item["sku"]))["name"]
        out.append(hints.say(item["kind"], s.lang, product=name, name=name,
                             expected=fmt_qty(item["expected"]) if "expected" in item else "",
                             actual=fmt_qty(item["actual"]) if "actual" in item else ""))
    return " ".join(out)


async def invoice_check(order_id: str) -> dict:
    """The owner holds up the supplier's paper bill: read it twice and compare with the order and the counts."""
    s = get_session()
    order = (await _repo.get_order(s.shop_id, order_id) if order_id
             else await _repo.latest_order(s.shop_id, ["DELIVERY_PENDING", "DISCREPANCY"]))
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    d = await _repo.delivery_for_order(s.shop_id, order["id"])
    if d is None or order["state"] not in ("DELIVERY_PENDING", "DISCREPANCY"):
        return {"ok": False, "say": t("k2.delivery.none", s.lang)}
    frames = await s.request_frames("invoice", count=2, interval_ms=600)
    if not frames:
        return {"ok": False, "needs_retake": True, "say": t("vision.aim.show_label", s.lang)}
    inv = await vinv.read_invoice(frames)
    if not inv.agreed:
        return {"ok": False, "needs_retake": True, "say": hints.say("INVOICE_UNREADABLE", s.lang)}
    counted = {i["sku"]: float(i["countedQty"]) for i in d["items"] if i.get("countedQty") is not None}
    result = reconcile.compare(order["items"], counted, inv)
    target = "DELIVERY_RECONCILED" if result.status == "match" else "DISCREPANCY"
    now = now_iso()
    if order["state"] != target:
        order = {**order, "state": transition("order", order["state"], target), "updatedAt": now}
        await _repo.save_order(s.shop_id, order)
        await outbox.order_state(s.shop_id, order["id"], target)
    d = {**d, "state": target, "discrepancies": result.discrepancies, "updatedAt": now,
         "invoice": {"supplier": inv.supplier, "date": inv.date, "total": inv.total, "agreed": inv.agreed,
                     "lines": [line.model_dump() for line in inv.lines]}}
    await _repo.save_delivery(s.shop_id, d)
    invoice_only = [x for x in result.discrepancies if x["kind"] not in COUNT_KINDS]
    if invoice_only:
        await outbox.discrepancies(s.shop_id, d["id"],
                                   reconcile.ReconcileResult(status="discrepancy", discrepancies=invoice_only))
    await audit.log(s.shop_id, "aira", "invoice_checked", "delivery", d["id"], target)
    if result.status == "match":
        await s.send(CueMsg(cue="match"))
        say = t("k2.invoice.match", s.lang, total=inv.total)
    else:
        await s.send(CueMsg(cue="mismatch"))
        say = await _say_discrepancies(s, result.discrepancies)
    return {"ok": True, "orderId": order["id"], "state": target, "discrepancies": result.discrepancies, "say": say}


confirm.register_committer("delivery.record_count", _commit_count)
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `cd services/live && uv run pytest tests/k2/test_delivery.py -q`
Expected: `13 passed`.

- [ ] **Step 6: Try it with real photos through the tool harness**

You need Sarmitha's vision merged and real photos of a curd crate and a bill.

```bash
cd services/live
uv run python -m aira_live.devtools.call_tool delivery_start --args '{}' --shop murugan_dairy
uv run python -m aira_live.devtools.call_tool delivery_count --args '{"sku":"aavin_ghee_500ml","mode":"crate"}' \
  --image ~/aira-photos/ghee_crate_a.jpg --image ~/aira-photos/ghee_crate_b.jpg --shop murugan_dairy
```

Expected: a read-back prompt with the jar count, or a spoken aim hint.

- [ ] **Step 7: Commit**

```bash
git add services/live/aira_live/tools/delivery.py services/live/aira_live/vision/count.py services/live/aira_live/vision/cash.py services/live/tests/k2/test_delivery.py
git commit -m "feat(tools): live delivery counting (one by one / crate), verified stock, invoice reconciliation"
```

---

### Task 5: Payments, sales and stock: `supplier_pay`, `sale_start`, `sale_pay` (cash / UPI soundbox), `stock_query`, `expiry_check`

**Files:**
- Modify (replace the foundation stubs): `services/live/aira_live/tools/payments.py`, `tools/sales.py`, `tools/stock.py`
- Test: `services/live/tests/k2/test_payments_sales.py`

**Interfaces:**
- Consumes:
  - `vision.cash.read_cash(frames) -> CashRead` (`hint_key` is an i18n key)
  - `money.change_due`, `money.notes_for`, `money.InsufficientTender`
  - `soundbox.parse(text) -> SoundboxEvent | None`
  - `payments.record`, `payments.confirm`, `payments.PaymentMismatch`
  - `sales.create`, `sales.complete`, `ledger.on_hand`, `ledger.InsufficientStock`
  - `reconcile.payable`, `expiry.scan`, `clock.today_ist`
  - `live_bridge.on_input_transcript(session_id, callback) -> unsubscribe`
  - `sessions.notify`
  - `orders.resolve_lines`, `orders.product_doc` (Task 3)
- Produces:
  - Live tools: `supplier_pay(order_id, method)`, `sale_start(items)`, `sale_pay(sale_id, method)`, `stock_query(product=None)`, `expiry_check()`
  - Committers: `"supplier_pay.cash"`, `"supplier_pay.upi"`, `"sale.complete"`
  - `sales.active_sale: dict[str, str]` (session_id → pending sale id)

**Flows:**
- **Supplier, cash:** AIRA speaks the note plan (`notes_for`), waits `CASH_PREP_S`, reads the notes twice, and requires the total to equal the amount due (`payable`). It then proposes; the commit confirms the payment with `cash_count` evidence and sets `PAYMENT_CONFIRMED`.
- **Customer, cash:** read the tendered notes → `change_due` → speak the change plan → read the change in hand (it must equal the change due) → propose → commit (`payments.record` + `confirm` + `sales.complete`).
- **Customer, UPI:** subscribe to Live input transcripts. A `soundbox.parse` amount equal to the total gives a proposal with `soundbox` evidence. Any other amount is warned about and listening continues. After `UPI_WAIT_S`, the owner is asked to confirm (`user_confirm` evidence).

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/k2/test_payments_sales.py
import asyncio
from datetime import date

from aira_live.business import clock, hints, ledger, paths
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.tools import _repo
from aira_live.tools import payments as pay
from aira_live.tools import sales as sale_tools
from aira_live.tools import stock
from aira_live.vision import cash as vcash
from aira_live.vision.cash import CashRead

from .conftest import SHOP, use_call

CURD = "sakthi_curd_1l"


def cash_reads(*items):
    """Each item: an int total (good read) or a str hint key (unusable read)."""
    seq = list(items)

    async def fake(frames):
        item = seq.pop(0)
        if isinstance(item, str):
            return CashRead(notes=[], total=None, agreed=False, confidence=0.2, hint_key=item)
        return CashRead(notes=[{"denomination": item, "count": 1}], total=item, agreed=True, confidence=0.9)
    return fake


async def _reconciled(store) -> None:
    name = (await store.get(paths.product(SHOP, CURD)))["name"]
    await _repo.save_order(SHOP, {"id": "o1", "supplierId": "sakthi_vendor", "supplierName": "Sakthi curd vendor",
                                  "state": "DELIVERY_RECONCILED", "lang": "en", "idemKey": "k-o1",
                                  "updatedAt": "2026-10-12T10:00:00",
                                  "items": [{"sku": CURD, "name": name, "qty": 3.0, "unit": "L", "agreedPrice": 50}]})
    await _repo.save_delivery(SHOP, {"id": "d1", "orderId": "o1", "state": "DELIVERY_RECONCILED",
                                     "updatedAt": "2026-10-12T10:00:00", "discrepancies": [],
                                     "items": [{"sku": CURD, "name": name, "unit": "L", "expectedQty": 3.0,
                                                "countedQty": 3.0}]})


async def test_supplier_pay_cash_proposes_after_correct_cash(store, session, proposals, notified, published,
                                                             monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(150))
    session.queue("cash", "c1", "c2")
    r = await pay.supplier_pay("o1", "cash")
    assert notified[0][1].startswith("Pay 150 rupees:")
    assert r["needs_confirm"] is True and proposals[0]["committer"] == "supplier_pay.cash"
    assert (await _repo.get_order(SHOP, "o1"))["state"] == "PAYMENT_PENDING"
    assert ("orders", {"shop_id": SHOP, "order_id": "o1", "state": "PAYMENT_PENDING"}) in published


async def test_supplier_pay_cash_hint_key_retake(store, session, proposals, notified, monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads("cash.spread_notes"))
    session.queue("cash", "c1", "c2")
    r = await pay.supplier_pay("o1", "cash")
    assert r["needs_retake"] is True and r["say"] == t("cash.spread_notes", "en") and proposals == []


async def test_supplier_pay_wrong_total(store, session, proposals, notified, monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(100))
    session.queue("cash", "c1", "c2")
    r = await pay.supplier_pay("o1", "cash")
    assert r["say"] == "I see 100 rupees, but it should be 150." and proposals == []


async def test_supplier_pay_commit_confirms_payment_and_order(store, session, proposals, notified, published,
                                                              monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(150))
    session.queue("cash", "c1", "c2")
    await pay.supplier_pay("o1", "cash")
    payload = proposals[0]["payload"]
    first = await pay._commit_supplier_cash(payload, None)
    again = await pay._commit_supplier_cash(payload, None)
    assert first["say"] == again["say"] == "Payment of 150 rupees to Sakthi curd vendor recorded."
    assert (await store.get(paths.payment(SHOP, payload["payment_id"])))["state"] == "CONFIRMED"
    assert (await _repo.get_order(SHOP, "o1"))["state"] == "PAYMENT_CONFIRMED"
    assert [p for tpc, p in published if tpc == "orders" and p["state"] == "PAYMENT_CONFIRMED"] == [
        {"shop_id": SHOP, "order_id": "o1", "state": "PAYMENT_CONFIRMED"}]


async def _curd_sale(store, liters=2) -> dict:
    return await sale_tools.sale_start([{"product": "curd", "qty": liters, "unit": "L"}])


async def test_sale_start_prices_and_checks_stock(store, session):
    r = await _curd_sale(store)
    price = (await store.get(paths.product(SHOP, CURD)))["price"]
    assert r["total"] == 2 * price and r["say"].endswith(f"Total {2 * price} rupees. Cash or UPI?")
    assert sale_tools.active_sale[session.session_id] == r["saleId"]


async def test_sale_start_insufficient_stock(store, session):
    r = await _curd_sale(store, liters=50)
    name = (await store.get(paths.product(SHOP, CURD)))["name"]
    have = await ledger.on_hand(SHOP, CURD)
    assert r["ok"] is False and r["say"] == hints.say("InsufficientStock", "en", product=name, have=fmt_qty(have))


async def test_sale_pay_cash_with_change(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale(store)
    use_call("tc-2")
    change = 200 - s["total"]
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(200, change))
    session.queue("cash", "c1", "c2", "c3", "c4")
    await pay.sale_pay(s["saleId"], "cash")
    assert notified[0][1].startswith(f"Received 200. Give back {change} rupees:")
    p = proposals[0]
    assert (p["kind"], p["committer"], p["payload"]["evidence"]) == (
        "sale", "sale.complete", {"type": "cash_count", "total": 200})


async def test_sale_pay_cash_hint_key_retake(store, session, proposals, monkeypatch):
    s = await _curd_sale(store)
    monkeypatch.setattr(vcash, "read_cash", cash_reads("cash.spread_notes"))
    session.queue("cash", "c1", "c2")
    r = await pay.sale_pay(s["saleId"], "cash")
    assert r["needs_retake"] is True and r["say"] == t("cash.spread_notes", "en") and proposals == []


async def test_sale_commit_is_idempotent(store, session, proposals, notified, published, monkeypatch):
    s = await _curd_sale(store)
    before = await ledger.on_hand(SHOP, CURD)
    use_call("tc-2")
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(s["total"]))
    session.queue("cash", "c1", "c2")
    await pay.sale_pay(s["saleId"], "cash")
    payload = proposals[0]["payload"]
    first = await pay._commit_sale(payload, None)
    await pay._commit_sale(payload, None)                       # retried confirm
    assert first["say"].startswith("Sale complete.")
    assert await ledger.on_hand(SHOP, CURD) == before - 2.0
    assert len([1 for tpc, p in published if tpc == "sales" and p["sale_id"] == s["saleId"]]) == 1


def _listen(monkeypatch):
    listeners = []

    def fake_subscribe(session_id, cb):
        listeners.append(cb)
        return lambda: listeners.remove(cb)

    monkeypatch.setattr(pay, "_subscribe", fake_subscribe)
    return listeners


async def test_upi_soundbox_correct_amount_proposes(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale(store)
    use_call("tc-2")
    listeners = _listen(monkeypatch)
    task = asyncio.create_task(pay.sale_pay(s["saleId"], "upi"))
    while not listeners:
        await asyncio.sleep(0)
    await listeners[0](f"Received rupees {s['total']} on PhonePe")
    await task
    ev = proposals[-1]["payload"]["evidence"]
    assert (ev["type"], ev["amount"]) == ("soundbox", s["total"])
    assert listeners == []                                       # unsubscribed


async def test_upi_soundbox_wrong_amount_does_not_confirm(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale(store)
    use_call("tc-2")
    monkeypatch.setattr(pay, "UPI_WAIT_S", 0.3)
    listeners = _listen(monkeypatch)
    task = asyncio.create_task(pay.sale_pay(s["saleId"], "upi"))
    while not listeners:
        await asyncio.sleep(0)
    wrong = s["total"] * 10
    await listeners[0](f"Received rupees {wrong} on PhonePe")
    await task
    assert any(text == f"The soundbox said {wrong} rupees, but the bill is {s['total']}." for _, text, _ in notified)
    p = proposals[-1]
    assert p["payload"]["evidence"] == {"type": "user_confirm"}
    assert p["prompt"] == t("k2.sale.upi_timeout_q", "en", total=s["total"])


async def test_stock_query_one_and_all(store, session):
    one = await stock.stock_query("curd")
    have = await ledger.on_hand(SHOP, CURD)
    assert one["qty"] == have and one["say"].endswith(f"{fmt_qty(have)} L in stock.")
    assert (await stock.stock_query())["say"].startswith("Stock: ")


async def test_expiry_check_lists_items_expiring_tomorrow(store, session, monkeypatch):
    monkeypatch.setattr(clock, "today_ist", lambda: date(2026, 10, 13))   # milk seeded 10-12 with 2-day shelf life
    r = await stock.expiry_check()
    assert any(a["sku"] == "aavin_milk_500ml" for a in r["alerts"])
    assert r["say"].endswith("Sell or return these first.")
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `cd services/live && uv run pytest tests/k2/test_payments_sales.py -q`
Expected: FAIL with `NotImplementedError` (from the foundation stubs) or `AttributeError: ... has no attribute '_commit_sale'`.

- [ ] **Step 3: Implement `tools/sales.py` and `tools/stock.py`**

```python
# services/live/aira_live/tools/sales.py — OWNER: Kanish (kanish/02)
"""sale_start: price a customer's request in selling units and open a SALE_PENDING sale."""
from aira_live import ids
from aira_live.business import hints, ledger, sales
from aira_live.business.ledger import InsufficientStock
from aira_live.contracts import StateMsg
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import get_session
from aira_live.tools.orders import resolve_lines

active_sale: dict[str, str] = {}   # session_id → pending sale id


async def sale_start(items: list[dict]) -> dict:
    """A CUSTOMER asks for products, e.g. "2 litres curd and 1 paneer".
    items: [{"product": "curd", "qty": 2, "unit": "L"}] (unit optional). Opens a pending sale and says the total."""
    s = get_session()
    lines, phrases, err = await resolve_lines(s.shop_id, items, s.lang)
    if err:
        return {"ok": False, "say": err}
    for line in lines:
        have = await ledger.on_hand(s.shop_id, line["sku"])
        if have < line["qty"]:
            return {"ok": False, "say": hints.say("InsufficientStock", s.lang, product=line["name"], have=fmt_qty(have))}
    try:
        sale = await sales.create(s.shop_id, [{"sku": ln["sku"], "qty": ln["qty"]} for ln in lines],
                                  ids.current_idem_key())
    except InsufficientStock:
        return {"ok": False, "say": hints.say("InsufficientStock", s.lang, product=lines[0]["name"], have="0")}
    active_sale[s.session_id] = sale["id"]
    await s.send(StateMsg(entity="sale", id=sale["id"], state=sale["state"], summary=str(sale["total"])))
    return {"ok": True, "saleId": sale["id"], "total": sale["total"],
            "say": t("k2.sale.price", s.lang, items=", ".join(phrases), total=sale["total"])}
```

```python
# services/live/aira_live/tools/stock.py — OWNER: Kanish (kanish/02)
"""Recorded stock and expiry, spoken."""
from aira_live.business import catalog, clock, expiry, hints, ledger, paths
from aira_live.business.store import get_store
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import get_session
from aira_live.tools.orders import product_doc


async def stock_query(product: str | None = None) -> dict:
    """How much of a product is in stock (or a short summary of everything, lowest first)."""
    s = get_session()
    if product:
        ref = await catalog.resolve(s.shop_id, product, s.lang)
        if ref is None:
            return {"ok": False, "say": hints.say("PRODUCT_UNKNOWN", s.lang, product=product)}
        p = await product_doc(s.shop_id, ref.sku)
        qty = await ledger.on_hand(s.shop_id, ref.sku)
        return {"ok": True, "sku": ref.sku, "qty": qty,
                "say": t("k2.stock.one", s.lang, product=p["name"], qty=f"{fmt_qty(qty)} {p['unit']}")}
    rows = []
    for sku, p in await get_store().list(paths.products(s.shop_id)):
        qty = await ledger.on_hand(s.shop_id, sku)
        rows.append((qty > float(p.get("reorderThreshold", 0)), p["name"], qty, p["unit"], sku))
    rows.sort()
    listing = ", ".join(f"{name} {fmt_qty(q)} {unit}" for _, name, q, unit, _ in rows[:6])
    return {"ok": True, "items": [{"sku": sku, "qty": q} for _, _, q, _, sku in rows],
            "say": t("k2.stock.all", s.lang, list=listing)}


async def expiry_check() -> dict:
    """Stock that has expired or expires today or tomorrow, soonest first."""
    s = get_session()
    alerts = await expiry.scan(s.shop_id, clock.today_ist())
    if not alerts:
        return {"ok": True, "alerts": [], "say": t("k2.expiry.none", s.lang)}
    when = {"expired": "k2.when.expired", "expires_today": "k2.when.today", "expires_tomorrow": "k2.when.tomorrow"}
    listing = ", ".join(t("k2.expiry.item", s.lang, product=a["name"], qty=fmt_qty(a["qty"]),
                          when=t(when[a["status"]], s.lang)) for a in alerts[:5])
    return {"ok": True, "alerts": alerts, "say": t("k2.expiry.list", s.lang, list=listing)}
```

- [ ] **Step 4: Implement `tools/payments.py`**

```python
# services/live/aira_live/tools/payments.py — OWNER: Kanish (kanish/02)
"""Supplier payments and customer payments. Cash is checked twice by vision, UPI by the shop soundbox.
Committers: 'supplier_pay.cash', 'supplier_pay.upi', 'sale.complete'."""
import asyncio
from typing import Literal

from aira_live import confirm, ids
from aira_live.business import audit, hints, ledger, money, paths, payments, reconcile, sales, soundbox, outbox
from aira_live.business.clock import now_iso
from aira_live.business.payments import PaymentMismatch
from aira_live.business.states import transition
from aira_live.business.store import get_store
from aira_live.contracts import CueMsg, StateMsg
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import get_session
from aira_live.tools import _repo
from aira_live.tools import sales as sale_tools
from aira_live.tools.orders import product_doc
from aira_live.vision import cash as vcash

CASH_PREP_S = 5.0      # time for the owner to take notes out after hearing the plan
UPI_WAIT_S = 60.0      # how long to listen for the soundbox before asking the owner
PAYABLE_STATES = ["DELIVERY_RECONCILED", "DISCREPANCY", "PAYMENT_PENDING"]


def _subscribe(session_id: str, callback):
    from aira_live import live_bridge
    return live_bridge.on_input_transcript(session_id, callback)


async def _notify(s, text: str, cue: str = "ack") -> None:
    from aira_live import sessions
    await sessions.notify(s.shop_id, text, cue)


def _notes_words(notes: list[dict]) -> str:
    return ", ".join(f"{n['count']} × ₹{n['denomination']}" for n in notes)


async def _read_cash(s):
    """Returns (CashRead, None) when usable, else (None, retake_reply). Hints are spoken via i18n keys."""
    frames = await s.request_frames("cash", count=2, interval_ms=500)
    if not frames:
        return None, {"ok": False, "needs_retake": True, "say": t("k2.cash.unclear", s.lang)}
    read = await vcash.read_cash(frames)
    if read.hint_key:
        return None, {"ok": False, "needs_retake": True, "say": t(read.hint_key, s.lang)}
    if not read.agreed or read.total is None:
        return None, {"ok": False, "needs_retake": True, "say": t("k2.cash.unclear", s.lang)}
    return read, None


# ---------------- supplier ----------------

async def supplier_pay(order_id: str, method: Literal["cash", "upi"]) -> dict:
    """Pay the supplier for a checked delivery. cash: AIRA says which notes to take and checks them with the camera.
    upi: the owner pays in his own UPI app and confirms. Call again after the owner shows the notes if asked."""
    s = get_session()
    order = (await _repo.get_order(s.shop_id, order_id) if order_id
             else await _repo.latest_order(s.shop_id, PAYABLE_STATES))
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    if order["state"] not in PAYABLE_STATES:
        return {"ok": False, "say": t("k2.pay.not_ready", s.lang)}
    d = await _repo.delivery_for_order(s.shop_id, order["id"])
    counted = {i["sku"]: float(i["countedQty"]) for i in (d or {}).get("items", []) if i.get("countedQty") is not None}
    amount = reconcile.payable(order["items"], counted)
    existing = await get_store().get(paths.payment(s.shop_id, order["paymentId"])) if order.get("paymentId") else None
    if existing is None or existing["method"] != method:
        p = await payments.record(s.shop_id, "out", method, amount, "order", order["id"], ids.current_idem_key())
        order = {**order, "paymentId": p["id"], "updatedAt": now_iso()}
    if order["state"] != "PAYMENT_PENDING":
        order["state"] = transition("order", order["state"], "PAYMENT_PENDING")
        await _repo.save_order(s.shop_id, order)
        await outbox.order_state(s.shop_id, order["id"], "PAYMENT_PENDING")
    else:
        await _repo.save_order(s.shop_id, order)
    base = {"order_id": order["id"], "payment_id": order["paymentId"], "amount": amount, "idem": ids.current_idem_key()}
    if method == "upi":
        return await confirm.propose("payment", t("k2.pay.upi_q", s.lang, amount=amount, supplier=order["supplierName"]),
                                     base, "supplier_pay.upi")
    await _notify(s, t("k2.pay.plan", s.lang, amount=amount, notes=_notes_words(money.notes_for(amount))))
    await asyncio.sleep(CASH_PREP_S)
    read, retake = await _read_cash(s)
    if retake:
        return retake
    if read.total != amount:
        await s.send(CueMsg(cue="mismatch"))
        return {"ok": False, "needs_retake": True, "say": t("k2.pay.cash_wrong", s.lang, seen=read.total, amount=amount)}
    return await confirm.propose("payment", t("k2.pay.cash_q", s.lang, amount=amount, supplier=order["supplierName"]),
                                 {**base, "total": read.total, "notes": read.notes}, "supplier_pay.cash")


async def _commit_supplier(payload: dict, evidence: dict) -> dict:
    s = get_session()
    order = await _repo.get_order(s.shop_id, payload["order_id"])
    done = t("k2.pay.done", s.lang, amount=payload["amount"], supplier=order["supplierName"])
    if order["state"] == "PAYMENT_CONFIRMED":
        return {"ok": True, "say": done}
    try:
        await payments.confirm(s.shop_id, payload["payment_id"], evidence)
    except PaymentMismatch:
        return {"ok": False, "say": hints.say("PaymentMismatch", s.lang)}
    order = {**order, "state": transition("order", order["state"], "PAYMENT_CONFIRMED"), "updatedAt": now_iso()}
    await _repo.save_order(s.shop_id, order)
    await outbox.order_state(s.shop_id, order["id"], "PAYMENT_CONFIRMED")
    await audit.log(s.shop_id, "owner", "supplier_paid", "order", order["id"], f"{evidence['type']}:{payload['amount']}")
    await s.send(CueMsg(cue="done"))
    return {"ok": True, "say": done}


async def _commit_supplier_cash(payload: dict, value: str | None) -> dict:
    return await _commit_supplier(payload, {"type": "cash_count", "total": payload["total"], "notes": payload["notes"]})


async def _commit_supplier_upi(payload: dict, value: str | None) -> dict:
    return await _commit_supplier(payload, {"type": "user_confirm"})


# ---------------- customer ----------------

async def _sale(s, sale_id: str | None) -> dict | None:
    sid = sale_id or sale_tools.active_sale.get(s.session_id)
    doc = await get_store().get(paths.sale(s.shop_id, sid)) if sid else None
    return {**doc, "id": sid} if doc else None


async def sale_pay(sale_id: str, method: Literal["cash", "upi"]) -> dict:
    """Take the customer's payment for the pending sale. cash: the owner shows the customer's notes, then the change.
    upi: AIRA listens for the shop's payment soundbox. Completing the sale always waits for the owner's yes."""
    s = get_session()
    sale = await _sale(s, sale_id)
    if sale is None or sale["state"] != "SALE_PENDING":
        return {"ok": False, "say": t("k2.sale.none", s.lang)}
    total = int(sale["total"])
    idem = ids.current_idem_key()
    if method == "upi":
        return await _await_soundbox(s, sale, total, idem)
    read, retake = await _read_cash(s)
    if retake:
        return retake
    tendered = int(read.total)
    try:
        change = money.change_due(total, tendered)
    except money.InsufficientTender:
        return {"ok": False, "say": hints.say("InsufficientTender", s.lang, short=total - tendered)}
    if change > 0:
        await _notify(s, t("k2.sale.change", s.lang, tendered=tendered, change=change,
                           notes=_notes_words(money.notes_for(change))))
        await asyncio.sleep(CASH_PREP_S)
        back, retake = await _read_cash(s)
        if retake:
            return retake
        if back.total != change:
            await s.send(CueMsg(cue="mismatch"))
            return {"ok": False, "needs_retake": True,
                    "say": t("k2.sale.change_wrong", s.lang, seen=back.total, change=change)}
    return await confirm.propose("sale", t("k2.sale.cash_q", s.lang, tendered=tendered, change=change),
                                 {"sale_id": sale["id"], "method": "cash", "amount": total,
                                  "evidence": {"type": "cash_count", "total": tendered}, "idem": idem},
                                 "sale.complete")


async def _await_soundbox(s, sale: dict, total: int, idem: str) -> dict:
    got: asyncio.Future = asyncio.get_running_loop().create_future()

    async def on_text(text: str) -> None:
        ev = soundbox.parse(text)
        if ev is None or got.done():
            return
        if ev.amount == total:
            got.set_result((ev, text))
            return
        await s.send(CueMsg(cue="mismatch"))
        await _notify(s, t("k2.sale.upi_wrong", s.lang, heard=ev.amount, total=total), "mismatch")

    unsubscribe = _subscribe(s.session_id, on_text)
    try:
        await _notify(s, t("k2.sale.upi_wait", s.lang))
        ev, text = await asyncio.wait_for(got, timeout=UPI_WAIT_S)
    except TimeoutError:
        return await confirm.propose("payment", t("k2.sale.upi_timeout_q", s.lang, total=total),
                                     {"sale_id": sale["id"], "method": "upi", "amount": total,
                                      "evidence": {"type": "user_confirm"}, "idem": idem}, "sale.complete")
    finally:
        unsubscribe()
    await s.send(CueMsg(cue="match"))
    return await confirm.propose("sale", t("k2.sale.upi_heard_q", s.lang, amount=ev.amount),
                                 {"sale_id": sale["id"], "method": "upi", "amount": total,
                                  "evidence": {"type": "soundbox", "amount": ev.amount, "provider": ev.provider,
                                               "transcript": text}, "idem": idem}, "sale.complete")


async def _commit_sale(payload: dict, value: str | None) -> dict:
    s = get_session()
    sale = await get_store().get(paths.sale(s.shop_id, payload["sale_id"]))
    if sale is None:
        return {"ok": False, "say": t("k2.sale.none", s.lang)}
    if sale["state"] == "SALE_COMPLETED":
        return {"ok": True, "saleId": payload["sale_id"], "say": t("k2.sale.done", s.lang)}
    try:
        p = await payments.record(s.shop_id, "in", payload["method"], payload["amount"], "sale", payload["sale_id"],
                                  payload["idem"])
        await payments.confirm(s.shop_id, p["id"], payload["evidence"])
        sale = await sales.complete(s.shop_id, payload["sale_id"], p["id"], payload["idem"])
    except PaymentMismatch:
        return {"ok": False, "say": hints.say("PaymentMismatch", s.lang)}
    sale_tools.active_sale.pop(s.session_id, None)
    await s.send(StateMsg(entity="sale", id=payload["sale_id"], state="SALE_COMPLETED", summary=str(payload["amount"])))
    await s.send(CueMsg(cue="done"))
    low = []
    for line in sale["items"]:
        prod = await product_doc(s.shop_id, line["sku"])
        left = await ledger.on_hand(s.shop_id, line["sku"])
        if left <= float(prod.get("reorderThreshold", 0)):
            low.append(t("k2.sale.low_stock", s.lang, left=f"{fmt_qty(left)} {prod['unit']}", product=prod["name"]))
    return {"ok": True, "saleId": payload["sale_id"], "lowStock": low,
            "say": " ".join([t("k2.sale.done", s.lang), *low])}


confirm.register_committer("supplier_pay.cash", _commit_supplier_cash)
confirm.register_committer("supplier_pay.upi", _commit_supplier_upi)
confirm.register_committer("sale.complete", _commit_sale)
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `cd services/live && uv run pytest tests/k2/test_payments_sales.py -q`
Expected: `13 passed`.

- [ ] **Step 6: Commit**

```bash
git add services/live/aira_live/tools/payments.py services/live/aira_live/tools/sales.py services/live/aira_live/tools/stock.py services/live/tests/k2/test_payments_sales.py
git commit -m "feat(tools): supplier/customer payments (cash checked, UPI soundbox), sale_start, stock and expiry"
```

---

### Task 6: End-to-end loop test, full-suite check, live phone run, PR

**Files:**
- Test: `services/live/tests/k2/test_e2e_loop.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5, on the real `kanish/01` engine with `MemoryStore`.
- Produces: a regression test of Murugan's whole loop. The demo script on the phone follows the same order.

- [ ] **Step 1: Write the end-to-end test**

```python
# services/live/tests/k2/test_e2e_loop.py
"""Murugan's loop on the real engine (MemoryStore). Only vision, WhatsApp and the soundbox listener are faked."""
import asyncio

import aira_live.messaging as messaging
from aira_live.business import ledger, paths
from aira_live.tools import _repo, delivery, orders
from aira_live.tools import payments as pay
from aira_live.tools import sales as sale_tools
from aira_live.vision import cash as vcash
from aira_live.vision import identify as vid
from aira_live.vision import invoice as vinv
from aira_live.vision.cash import CashRead
from aira_live.vision.identify import IdentifyResult
from aira_live.vision.invoice import InvoiceLine, InvoiceRead

from .conftest import SHOP, use_call

CURD = "sakthi_curd_1l"


async def test_murugan_full_loop(store, session, proposals, notified, published, monkeypatch):
    start = await ledger.on_hand(SHOP, CURD)
    product = await store.get(paths.product(SHOP, CURD))

    # 1. Order 3 L Sakthi curd → WhatsApp
    async def fake_send(shop_id, supplier, order):
        return {"channel": "whatsapp", "messageId": "wamid.E2E"}

    monkeypatch.setattr(messaging, "send_order", fake_send)
    use_call("c1")
    await orders.order_create("sakthi vendor", [{"product": "sakthi curd", "qty": 3, "unit": "L"}])
    oid = proposals[-1]["payload"]["order_id"]
    use_call("c2")
    await orders._commit_send(proposals[-1]["payload"], None)

    # 2. Delivery counted one by one (3 packs)
    use_call("c3")
    await delivery.delivery_start(oid)
    monkeypatch.setattr(delivery, "COUNT_IDLE_S", 0.2)

    async def identify(frame, candidates):
        return IdentifyResult(sku=CURD if frame.id.startswith("p") else None, confidence=0.9, alternatives=[])

    monkeypatch.setattr(vid, "identify_product", identify, raising=False)
    session.stream("identify", [("p1", 1000), ("g1", 1800), ("p2", 2600), ("g2", 3400), ("p3", 4200)])
    use_call("c4")
    await delivery.delivery_count(CURD)
    use_call("c5")
    await delivery._commit_count(proposals[-1]["payload"], None)

    # 3. Invoice matches the order and the count
    agreed = (await _repo.get_order(SHOP, oid))["items"][0]["agreedPrice"]

    async def invoice(frames):
        return InvoiceRead(supplier="Sakthi", date=None, agreed=True, disagreements=[], total=3 * agreed,
                           lines=[InvoiceLine(name=product["name"], qty=3, unit="L", unit_price=agreed,
                                              line_total=3 * agreed)])

    monkeypatch.setattr(vinv, "read_invoice", invoice, raising=False)
    session.queue("invoice", "i1", "i2")
    use_call("c6")
    assert (await delivery.invoice_check(oid))["state"] == "DELIVERY_RECONCILED"

    # 4. Pay the vendor in cash (exact amount checked)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)

    async def cash(frames):
        return CashRead(notes=[{"denomination": 3 * agreed, "count": 1}], total=3 * agreed, agreed=True,
                        confidence=0.9)

    monkeypatch.setattr(vcash, "read_cash", cash)
    session.queue("cash", "k1", "k2")
    use_call("c7")
    await pay.supplier_pay(oid, "cash")
    use_call("c8")
    await pay._commit_supplier_cash(proposals[-1]["payload"], None)
    assert (await _repo.get_order(SHOP, oid))["state"] == "PAYMENT_CONFIRMED"

    # 5. A customer buys 2 L curd and pays by UPI; the soundbox confirms
    use_call("c9")
    sale = await sale_tools.sale_start([{"product": "curd", "qty": 2, "unit": "L"}])
    listeners = []

    def subscribe(session_id, cb):
        listeners.append(cb)
        return lambda: listeners.remove(cb)

    monkeypatch.setattr(pay, "_subscribe", subscribe)
    use_call("c10")
    task = asyncio.create_task(pay.sale_pay(sale["saleId"], "upi"))
    while not listeners:
        await asyncio.sleep(0)
    await listeners[0](f"Received rupees {sale['total']} on PhonePe")
    await task
    use_call("c11")
    done = await pay._commit_sale(proposals[-1]["payload"], None)

    # 6. Stock moved exactly by verified delivery and sale; analytics saw every order state once
    left = await ledger.on_hand(SHOP, CURD)
    assert left == start + 3.0 - 2.0
    assert [p["state"] for topic, p in published if topic == "orders"] == [
        "ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED", "PAYMENT_PENDING", "PAYMENT_CONFIRMED"]
    assert done["say"].startswith("Sale complete.")
    assert ("Order more?" in done["say"]) == (left <= float(product["reorderThreshold"]))
```

- [ ] **Step 2: Run the whole kanish/02 suite and the full service suite**

Run: `cd services/live && uv run pytest tests/k2 -q`
Expected: `52 passed`:

| Test file | Passed |
|---|---|
| `test_messaging.py` | 9 |
| `test_webhooks.py` | 8 |
| `test_orders.py` | 8 |
| `test_delivery.py` | 13 |
| `test_payments_sales.py` | 13 |
| `test_e2e_loop.py` | 1 |

Run: `cd services/live && uv run pytest -q`
Expected: all tests pass, including `tests/business` from `kanish/01`. If anything fails, fix it before opening the PR.

- [ ] **Step 3: Live phone run (D4, with Saravana's Live bridge and Sarmitha's vision merged)**

1. Seed the demo shop.
2. Open the app on the chest-mounted phone and say each line below. The expected outcome is in the right column.

| You say / do | Expect |
|---|---|
| "Order 30 litres Sakthi curd from the Sakthi vendor" | A read-back. Say "yes" → the test phone gets the WhatsApp order |
| Reply "OK 6 pm" from the vendor phone | AIRA speaks "Sakthi curd vendor replied: OK 6 pm" |
| "The delivery has come" → "count the curd", then show 3 packs one at a time | "1… 2… 3", then a read-back → "yes" |
| "Check the bill" (hold the bill flat) | "The bill matches…" or the specific mismatch |
| "Pay the vendor in cash" | The note plan, then show the notes → read-back → "yes" |
| Customer: "2 litres curd" → "UPI" → play a soundbox clip (`bench/data/soundbox/received_120_phonepe.wav`) near the phone | "120 rupees received by UPI. Complete the sale?" → "yes" → "Sale complete." |
| "How much curd is left?" | The ledger value |

3. Record p50 latency for the read-back, the invoice check and the soundbox confirmation in `bench/results/k2_live_run.csv`, with columns `step,ms`.

- [ ] **Step 4: Commit, push and open the PR**

```bash
git add services/live/tests/k2/test_e2e_loop.py
git commit -m "test(k2): Murugan's full loop end to end on the real engine"
git push -u origin kanish/orders-delivery-payments
gh pr create --base main --title "kanish/02: orders, WhatsApp, delivery count, invoice, payments, sales" \
  --body "Implements docs/superpowers/plans/kanish/02-orders-delivery-payments-whatsapp.md. tests/k2: 52 passed."
```

Ask Saravana to review the cross-team notes at the top of this plan: the webhook router mount, the non-blocking tool registration, the `OPEN_URL` handling in the app, and the index collection rules.
