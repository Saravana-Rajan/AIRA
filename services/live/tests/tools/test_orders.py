"""Supplier order tools (kanish/02 Task 3): order_create (read-back -> confirm.propose) and order_status, plus the
committers "order.send" and "order.mark_sent".

Everything real runs: the engine (catalog, states, outbox, audit) on a MemoryStore seeded from the real Sam
Dairy catalog, the foundation's confirm/session/ids, and messaging. Only the analytics publisher is faked, and
`messaging.send_order` where a test needs a specific channel result. No test reaches Meta, Telegram or Firestore.
"""
from pathlib import Path

import pytest

import aira_live.messaging as messaging
from aira_live import confirm
from aira_live.business import hints, outbox, paths, seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.contracts import ConfirmMsg, StateMsg
from aira_live.i18n import t
from aira_live.session import Session, current_session, current_tool_call_id
from aira_live.tools import _repo, orders

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "content" / "catalog" / "murugan_dairy.json"
SHOP = "sunrise_dairy"
SUPPLIER_PHONES = {
    "SUPPLIER_AAVIN_PHONE": "+919000000001",
    "SUPPLIER_SAKTHI_PHONE": "+919000000002",
    "SUPPLIER_ARUN_PHONE": "+919000000003",
}
MESSAGING_ENV = (
    "WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_TEMPLATE",
    "WHATSAPP_GRAPH_VERSION", "TELEGRAM_TOKEN", "TELEGRAM_WEBHOOK_SECRET",
)
CURD_30L = [{"product": "sakthi curd", "qty": 30, "unit": "L"}]


# ---------------------------------------------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    for name in MESSAGING_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AIRA_DEMO_SHOP", SHOP)


@pytest.fixture(autouse=True)
def published():
    """Fake analytics publisher: records [(topic, payload), ...]."""
    sent: list[tuple[str, dict]] = []

    async def fake(topic: str, payload: dict) -> str:
        sent.append((topic, payload))
        return f"fake-{len(sent)}"

    outbox.set_publisher(fake)
    yield sent
    outbox.set_publisher(None)


@pytest.fixture
async def store():
    """MemoryStore with the real demo catalog (supplier phones from SUPPLIER_PHONES), installed as get_store()."""
    s = MemoryStore()
    set_store(s)
    await seed.seed_into(s, seed.load_catalog(CATALOG), env=SUPPLIER_PHONES)
    yield s
    set_store(None)


class _Phone:
    """Stands in for the phone's WebSocket: records every server message."""

    def __init__(self):
        self.msgs: list = []

    async def __call__(self, msg) -> None:
        self.msgs.append(msg)

    def of(self, kind: type) -> list:
        return [m for m in self.msgs if isinstance(m, kind)]


@pytest.fixture
def phone():
    return _Phone()


@pytest.fixture
def session(phone):
    """A real Session for the demo shop, current for the test, inside Live tool call "tc-1"."""
    s = Session("owner1", SHOP, "en", "short", phone)
    tok_s, tok_c = current_session.set(s), current_tool_call_id.set("tc-1")
    yield s
    current_tool_call_id.reset(tok_c)
    current_session.reset(tok_s)


def use_call(call_id: str) -> None:
    """Simulate a new Live tool call (so a new idempotency key)."""
    current_tool_call_id.set(call_id)


@pytest.fixture
def proposals(monkeypatch):
    """The real confirm.propose on an in-memory proposal store, recording every proposal made."""
    made: list[dict] = []
    real = confirm.propose
    previous = confirm._store
    confirm.set_store(confirm.MemoryProposalStore())

    async def recording(kind, prompt, payload, committer):
        out = await real(kind, prompt, payload, committer)
        made.append({"kind": kind, "prompt": prompt, "payload": payload, "committer": committer,
                     "confirm_id": out["confirm_id"]})
        return out

    monkeypatch.setattr(confirm, "propose", recording)
    yield made
    confirm.set_store(previous)


def fake_send(result: dict, calls: list | None = None):
    async def send(shop_id, supplier, order):
        if calls is not None:
            calls.append((shop_id, supplier["id"], order["id"]))
        return result
    return send


async def _name(store, sku: str) -> str:
    return (await store.get(paths.product(SHOP, sku)))["name"]


async def _drafted(proposals, supplier: str = "sakthi vendor", items: list | None = None) -> dict:
    await orders.order_create(supplier, items or CURD_30L)
    return proposals[-1]["payload"]


async def _orders(store) -> list:
    return await store.list(f"shops/{SHOP}/orders")


# ---------------------------------------------------------------------------------------------------- order_create

async def test_order_create_resolves_and_proposes(store, session, proposals, phone):
    r = await orders.order_create("sakthi vendor", CURD_30L)
    assert r["needs_confirm"] is True and r["say"] == proposals[0]["prompt"]
    p = proposals[0]
    assert (p["kind"], p["committer"]) == ("order", "order.send")
    o = await _repo.get_order(SHOP, p["payload"]["order_id"])
    assert (o["state"], o["supplierId"], o["supplierName"], o["lang"]) == ("DRAFT", "sakthi_vendor",
                                                                          "Sakthi curd vendor", "en")
    assert o["idemKey"] == p["payload"]["idem"] and o["channel"] == "telegram"     # the catalog's channel
    assert o["createdAt"] and o["updatedAt"]
    line = o["items"][0]
    assert (line["sku"], line["qty"], line["unit"]) == ("sakthi_curd_1l", 30.0, "L")
    assert line["name"] == await _name(store, "sakthi_curd_1l")
    assert line["agreedPrice"] == 50 and isinstance(line["agreedPrice"], int)
    assert p["prompt"] == f"Order 30 L {await _name(store, 'sakthi_curd_1l')} from Sakthi curd vendor?"
    shown = phone.of(ConfirmMsg)[-1]
    assert (shown.id, shown.kind, shown.prompt) == (r["confirm_id"], "order", p["prompt"])


async def test_order_create_converts_kg_paneer_and_reads_both_forms(store, session, proposals):
    await orders.order_create("aavin vendor", [{"product": "paneer", "qty": 5, "unit": "kg"}])
    line = (await _repo.get_order(SHOP, proposals[0]["payload"]["order_id"]))["items"][0]
    assert (line["sku"], line["qty"], line["unit"]) == ("aavin_paneer_200g", 25.0, "pack")
    assert proposals[0]["prompt"] == "Order 25 pack Aavin Paneer 200 g (25 × 200 g pack = 5 kg) from Aavin vendor?"


async def test_order_create_counted_packs_of_a_litre_product_read_both_forms(store, session, proposals):
    await orders.order_create("aavin vendor", [{"product": "aavin milk", "qty": 4, "unit": "packets"}])
    line = (await _repo.get_order(SHOP, proposals[0]["payload"]["order_id"]))["items"][0]
    assert (line["sku"], line["qty"], line["unit"]) == ("aavin_milk_500ml", 2.0, "L")
    assert "2 L Aavin Milk 500 ml (4 × 500 ml pouch = 2 L)" in proposals[0]["prompt"]


async def test_order_create_ml_of_a_litre_product_counts_packs(store, session, proposals):
    await orders.order_create("sakthi vendor", [{"product": "sakthi curd half litre", "qty": 1500, "unit": "ml"}])
    line = (await _repo.get_order(SHOP, proposals[0]["payload"]["order_id"]))["items"][0]
    assert (line["sku"], line["qty"], line["unit"]) == ("sakthi_curd_500ml", 1.5, "L")
    assert "1.5 L Sakthi Curd 500 ml (3 × 500 ml pack = 1500 ml)" in proposals[0]["prompt"]


async def test_order_create_accepts_name_alias_and_default_unit(store, session, proposals):
    await orders.order_create("sakthi vendor", [{"name": "sakthi curd", "qty": 12}])
    line = (await _repo.get_order(SHOP, proposals[0]["payload"]["order_id"]))["items"][0]
    assert (line["sku"], line["qty"], line["unit"]) == ("sakthi_curd_1l", 12.0, "L")
    assert "×" not in proposals[0]["prompt"]


async def test_order_create_spoken_unit_words_are_normalised(store, session, proposals):
    await orders.order_create("sakthi vendor", [{"product": "sakthi curd", "qty": 30, "unit": "litres"}])
    line = (await _repo.get_order(SHOP, proposals[0]["payload"]["order_id"]))["items"][0]
    assert (line["qty"], line["unit"]) == (30.0, "L") and "×" not in proposals[0]["prompt"]
    use_call("tc-2")
    await orders.order_create("aavin vendor", [{"product": "paneer", "qty": 5, "unit": "Kgs"}])
    line = (await _repo.get_order(SHOP, proposals[1]["payload"]["order_id"]))["items"][0]
    assert (line["qty"], line["unit"]) == (25.0, "pack") and "= 5 kg" in proposals[1]["prompt"]
    use_call("tc-3")
    await orders.order_create("arun distributor", [{"product": "ice cream", "qty": 4, "unit": "boxes"}])
    line = (await _repo.get_order(SHOP, proposals[2]["payload"]["order_id"]))["items"][0]
    assert (line["sku"], line["qty"], line["unit"]) == ("arun_icecream_box", 4.0, "box")
    assert "×" not in proposals[2]["prompt"]


async def test_order_create_several_lines_read_back_together(store, session, proposals):
    await orders.order_create("aavin vendor", [{"product": "aavin milk", "qty": 20, "unit": "L"},
                                               {"product": "paneer", "qty": 10, "unit": "pack"}])
    o = await _repo.get_order(SHOP, proposals[0]["payload"]["order_id"])
    assert [(x["sku"], x["qty"]) for x in o["items"]] == [("aavin_milk_500ml", 20.0), ("aavin_paneer_200g", 10.0)]
    assert proposals[0]["prompt"] == ("Order 20 L Aavin Milk 500 ml, 10 pack Aavin Paneer 200 g "
                                      "from Aavin vendor?")


async def test_order_create_speaks_the_owners_language(store, session, proposals):
    session.lang = "ta"
    await orders.order_create("sakthi vendor", CURD_30L)
    o = await _repo.get_order(SHOP, proposals[0]["payload"]["order_id"])
    assert o["lang"] == "ta"
    items = f"30 L {await _name(store, 'sakthi_curd_1l')}"
    assert proposals[0]["prompt"] == t("k2.order.read_back", "ta", items=items, supplier="Sakthi curd vendor")


async def test_order_create_unknown_supplier_says_hint(store, session, proposals):
    r = await orders.order_create("xyz traders", [{"product": "milk", "qty": 10, "unit": "L"}])
    assert r["ok"] is False and r["say"] == hints.say("SUPPLIER_UNKNOWN", "en", supplier="xyz traders")
    assert proposals == [] and await _orders(store) == []


async def test_order_create_unknown_product_says_hint_and_saves_nothing(store, session, proposals):
    r = await orders.order_create("sakthi vendor", [{"product": "bread", "qty": 3, "unit": "pack"}])
    assert r["ok"] is False and r["say"] == hints.say("PRODUCT_UNKNOWN", "en", product="bread")
    assert proposals == [] and await _orders(store) == []


async def test_order_create_product_the_supplier_does_not_sell_is_refused(store, session, proposals):
    r = await orders.order_create("sakthi vendor", [{"product": "paneer", "qty": 5, "unit": "pack"}])
    assert r["ok"] is False
    assert r["say"] == t("k2.order.wrong_supplier", "en", supplier="Sakthi curd vendor",
                         product=await _name(store, "aavin_paneer_200g"))
    assert proposals == [] and await _orders(store) == []


@pytest.mark.parametrize("item", [
    {"product": "sakthi curd", "qty": 1.5, "unit": "L"},       # not a whole number of 1 L packs
    {"product": "aavin milk", "qty": 2, "unit": "kg"},         # milk is not sold by weight
    {"product": "sakthi curd", "qty": 0, "unit": "L"},
    {"product": "sakthi curd", "qty": -3, "unit": "L"},
    {"product": "sakthi curd", "qty": "lots", "unit": "L"},
    {"product": "sakthi curd", "unit": "L"},
])
async def test_order_create_unusable_quantity_asks_again_and_saves_nothing(store, session, proposals, item):
    supplier = "aavin vendor" if "milk" in item["product"] else "sakthi vendor"
    r = await orders.order_create(supplier, [item])
    assert r["ok"] is False and r["say"].startswith("I can't order ")
    assert proposals == [] and await _orders(store) == []


async def test_order_create_without_items_proposes_nothing(store, session, proposals):
    for items in ([], None):
        r = await orders.order_create("sakthi vendor", items)
        assert r["ok"] is False and r["say"] == t("k2.order.no_items", "en")
    assert proposals == [] and await _orders(store) == []


async def test_order_create_retry_reuses_draft(store, session, proposals):
    for _ in range(2):                                          # the same Live tool call, retried
        await orders.order_create("sakthi vendor", CURD_30L)
    assert proposals[0]["payload"] == proposals[1]["payload"]
    assert len(await _orders(store)) == 1


async def test_order_create_new_call_makes_new_draft(store, session, proposals):
    await orders.order_create("sakthi vendor", CURD_30L)
    use_call("tc-2")
    await orders.order_create("sakthi vendor", CURD_30L)
    assert proposals[0]["payload"]["order_id"] != proposals[1]["payload"]["order_id"]
    assert len(await _orders(store)) == 2


# ---------------------------------------------------------------------------------------------------- committers

async def test_owners_yes_sends_the_order_through_the_registered_committer(store, session, proposals, monkeypatch):
    calls: list = []
    monkeypatch.setattr(messaging, "send_order", fake_send({"channel": "telegram", "messageId": "77:12"}, calls))
    r = await orders.order_create("sakthi vendor", CURD_30L)
    assert calls == []                                           # nothing is sent before the owner's yes
    use_call("tc-confirm")
    done = await confirm.resolve(r["confirm_id"], "yes")
    assert (done["status"], done["say"]) == ("committed", "Order sent to Sakthi curd vendor.")
    o = await _repo.get_order(SHOP, proposals[0]["payload"]["order_id"])
    assert (o["state"], o["channel"], o["channelMsgId"]) == ("ORDER_PLACED", "telegram", "77:12")
    again = await confirm.resolve(r["confirm_id"], "yes")
    assert again["status"] == "committed" and len(calls) == 1


async def test_owners_no_sends_nothing(store, session, proposals, monkeypatch):
    calls: list = []
    monkeypatch.setattr(messaging, "send_order", fake_send({"channel": "telegram", "messageId": "77:12"}, calls))
    r = await orders.order_create("sakthi vendor", CURD_30L)
    out = await confirm.resolve(r["confirm_id"], "no")
    assert out["status"] == "rejected" and calls == []
    assert (await _repo.get_order(SHOP, proposals[0]["payload"]["order_id"]))["state"] == "DRAFT"


async def test_commit_send_places_order_and_publishes(store, session, proposals, published, phone, monkeypatch):
    payload = await _drafted(proposals)
    calls: list = []
    monkeypatch.setattr(messaging, "send_order", fake_send({"channel": "whatsapp", "messageId": "wamid.9"}, calls))
    use_call("tc-confirm")
    r = await orders._commit_send(payload, None)
    o = await _repo.get_order(SHOP, payload["order_id"])
    assert calls == [(SHOP, "sakthi_vendor", o["id"])]
    assert (o["state"], o["channel"], o["channelMsgId"]) == ("ORDER_PLACED", "whatsapp", "wamid.9")
    assert o["placedAt"] and o["updatedAt"] >= o["createdAt"]
    assert ("orders", {"shop_id": SHOP, "order_id": o["id"], "state": "ORDER_PLACED"}) in published
    assert r == {"ok": True, "orderId": o["id"], "state": "ORDER_PLACED", "say": "Order sent to Sakthi curd vendor."}
    msg = phone.of(StateMsg)[-1]
    assert (msg.entity, msg.id, msg.state) == ("order", o["id"], "ORDER_PLACED")
    assert msg.summary == f"30 L {await _name(store, 'sakthi_curd_1l')}"
    logs = [d for _, d in await store.list(f"shops/{SHOP}/auditLogs")]
    assert [(a["actor"], a["action"], a["refId"]) for a in logs] == [("owner", "order_placed", o["id"])]


async def test_commit_send_passes_the_stored_supplier(store, session, proposals, monkeypatch):
    payload = await _drafted(proposals)
    seen: list = []

    async def send(shop_id, supplier, order):
        seen.append(supplier)
        return {"channel": "whatsapp", "messageId": "wamid.1"}

    monkeypatch.setattr(messaging, "send_order", send)
    await orders._commit_send(payload, None)
    sup = seen[0]
    assert (sup["id"], sup["phone"], sup["channel"]) == ("sakthi_vendor", "+919000000002", "telegram")


async def test_commit_send_twice_is_idempotent(store, session, proposals, published, monkeypatch):
    payload = await _drafted(proposals)
    calls: list = []
    monkeypatch.setattr(messaging, "send_order", fake_send({"channel": "whatsapp", "messageId": "wamid.9"}, calls))
    await orders._commit_send(payload, None)
    again = await orders._commit_send(payload, None)
    assert len(calls) == 1 and again["say"] == "That order was already sent."
    assert sum(1 for topic, _ in published if topic == "orders") == 1


async def test_commit_send_fallback_keeps_draft_and_opens_url(store, session, proposals, phone, published,
                                                              monkeypatch):
    payload = await _drafted(proposals)
    url = "https://wa.me/919000000002?text=x"
    monkeypatch.setattr(messaging, "send_order", fake_send({"channel": "whatsapp_link", "fallbackUrl": url}))
    r = await orders._commit_send(payload, None)
    assert (await _repo.get_order(SHOP, payload["order_id"]))["state"] == "DRAFT"
    assert phone.of(StateMsg)[-1].summary == f"OPEN_URL {url}"
    assert published == []                                       # nothing is placed yet
    assert r["needs_confirm"] is True and r["fallbackUrl"] == url
    follow = proposals[-1]
    assert (follow["kind"], follow["committer"]) == ("generic", "order.mark_sent")
    assert follow["payload"] == {"order_id": payload["order_id"], "idem": payload["idem"]}
    assert r["confirm_id"] == follow["confirm_id"]
    assert r["say"] == ("The order message is ready. Double-tap Send, then tell me when it is sent. "
                        "Did you send the order to Sakthi curd vendor?")
    done = await orders._commit_mark_sent(follow["payload"], None)
    o = await _repo.get_order(SHOP, payload["order_id"])
    assert (o["state"], o["channel"]) == ("ORDER_PLACED", "manual")
    assert done["say"] == "Order sent to Sakthi curd vendor."
    assert ("orders", {"shop_id": SHOP, "order_id": o["id"], "state": "ORDER_PLACED"}) in published
    again = await orders._commit_mark_sent(follow["payload"], None)
    assert again["say"] == "That order was already sent."


async def test_commit_send_without_tokens_uses_the_real_click_to_chat_fallback(store, session, proposals, phone,
                                                                               monkeypatch):
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: True)
    payload = await _drafted(proposals)
    r = await orders._commit_send(payload, None)
    assert r["fallbackUrl"].startswith("https://wa.me/919000000002?text=")
    assert phone.of(StateMsg)[-1].summary == f"OPEN_URL {r['fallbackUrl']}"
    assert (await _repo.get_order(SHOP, payload["order_id"]))["state"] == "DRAFT"
    assert proposals[-1]["committer"] == "order.mark_sent"


async def test_commit_send_call_channel_says_items_and_keeps_draft(store, session, proposals, phone, monkeypatch):
    payload = await _drafted(proposals, "arun distributor", [{"product": "ice cream", "qty": 4, "unit": "box"}])
    monkeypatch.setattr(messaging, "send_order", fake_send({"channel": "call", "fallbackUrl": "tel:+919000000003"}))
    r = await orders._commit_send(payload, None)
    assert (await _repo.get_order(SHOP, payload["order_id"]))["state"] == "DRAFT"
    assert phone.of(StateMsg)[-1].summary == "OPEN_URL tel:+919000000003"
    box = await _name(store, "arun_icecream_box")
    assert r["say"] == (f"Calling Arun ice cream distributor. Tell them: 4 box {box}. "
                        "Did you send the order to Arun ice cream distributor?")
    assert proposals[-1]["committer"] == "order.mark_sent"


async def test_commit_send_missing_order_says_none(store, session):
    r = await orders._commit_send({"order_id": "o_missing", "idem": "x"}, None)
    assert r["ok"] is False and r["say"] == "No open order found."
    r = await orders._commit_mark_sent({"order_id": "o_missing", "idem": "x"}, None)
    assert r["ok"] is False and r["say"] == "No open order found."


# ---------------------------------------------------------------------------------------------------- order_status

async def test_order_status_reports_vendor_reply(store, session):
    await _repo.save_order(SHOP, {"id": "o9", "supplierId": "sakthi_vendor", "supplierName": "Sakthi curd vendor",
                                  "state": "ORDER_PLACED", "items": [], "lang": "en",
                                  "vendorReply": {"text": "OK, 6 pm"}, "updatedAt": "2026-10-12T09:00:00"})
    r = await orders.order_status()
    assert r["say"] == "Order to Sakthi curd vendor: order placed. They said: OK, 6 pm"
    assert (r["ok"], r["orderId"], r["state"]) == (True, "o9", "ORDER_PLACED")


async def test_order_status_latest_active_and_by_id(store, session):
    await _repo.save_order(SHOP, {"id": "o1", "supplierId": "aavin_vendor", "supplierName": "Aavin vendor",
                                  "state": "DELIVERY_PENDING", "items": [], "updatedAt": "2026-10-12T08:00:00"})
    await _repo.save_order(SHOP, {"id": "o2", "supplierId": "sakthi_vendor", "supplierName": "Sakthi curd vendor",
                                  "state": "DRAFT", "items": [], "updatedAt": "2026-10-12T10:00:00"})
    await _repo.save_order(SHOP, {"id": "o3", "supplierId": "aavin_vendor", "supplierName": "Aavin vendor",
                                  "state": "CLOSED", "items": [], "updatedAt": "2026-10-12T11:00:00"})
    latest = await orders.order_status()
    assert latest["orderId"] == "o1" and latest["say"] == "Order to Aavin vendor: delivery pending."
    by_id = await orders.order_status("o2")
    assert by_id["say"] == "Order to Sakthi curd vendor: not sent yet."


async def test_order_status_speaks_every_state_in_every_language(store, session):
    for lang in ("en", "ta", "hi"):
        session.lang = lang
        for state in ("DRAFT", "ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED", "DISCREPANCY",
                      "PAYMENT_PENDING", "PAYMENT_CONFIRMED", "CLOSED"):
            await _repo.save_order(SHOP, {"id": "o1", "supplierId": "aavin_vendor", "supplierName": "Aavin vendor",
                                          "state": state, "items": [], "updatedAt": "2026-10-12T08:00:00"})
            r = await orders.order_status("o1")
            words = t(f"k2.order_state.{state}", lang)
            assert words != f"k2.order_state.{state}", (lang, state)
            assert r["say"] == t("k2.order.status", lang, supplier="Aavin vendor", state=words)


async def test_order_status_none(store, session):
    r = await orders.order_status()
    assert r["ok"] is False and r["say"] == "No open order found."
    missing = await orders.order_status("nope")
    assert missing["ok"] is False and missing["say"] == "No open order found."


async def test_commit_send_telegram_fallback_says_the_bot_is_not_linked(store, session, proposals, phone, monkeypatch):
    """A Telegram vendor without a chat id (or no bot token) never fails silently: AIRA says so, then hands over the link."""
    payload = await _drafted(proposals)
    url = "https://wa.me/919000000002?text=x"
    monkeypatch.setattr(messaging, "send_order",
                        fake_send({"channel": "whatsapp_link", "fallbackUrl": url, "reason": "telegram_not_linked"}))
    r = await orders._commit_send(payload, None)
    assert (await _repo.get_order(SHOP, payload["order_id"]))["state"] == "DRAFT"
    assert phone.of(StateMsg)[-1].summary == f"OPEN_URL {url}"
    assert r["say"] == ("Sakthi curd vendor is not on the AIRA Telegram bot yet. "
                        "The order message is ready. Double-tap Send, then tell me when it is sent. "
                        "Did you send the order to Sakthi curd vendor?")
    assert r["reason"] == "telegram_not_linked"
