"""Payments, sales and stock tools (kanish/02 Task 5): supplier_pay, sale_start, sale_pay (cash / UPI soundbox),
stock_query, expiry_check and the committers "supplier_pay.cash", "supplier_pay.upi", "sale.complete".

The engine (catalog, ledger, sales, payments, money, soundbox, reconcile, expiry, outbox) runs for real on a
MemoryStore seeded from the real Sunrise Dairy catalog, with the foundation's confirm/session/ids. Only the cash
reader (Gemini), the Live transcript subscription, sessions.notify and the analytics publisher are faked. The phone
is a fake that answers request_frame messages with queued frames.
"""
import asyncio
from datetime import date
from pathlib import Path

import pytest

from aira_live import confirm, sessions
from aira_live.business import clock, hints, ledger, outbox, paths, seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.contracts import ConfirmMsg, RequestFrameMsg
from aira_live.frames import Frame
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import Session, current_session, current_tool_call_id
from aira_live.tools import _repo, stock
from aira_live.tools import payments as pay
from aira_live.tools import sales as sale_tools
from aira_live.vision import cash as vcash
from aira_live.vision.cash import CashRead

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "shared" / "catalog" / "sunrise_dairy.json"
SHOP = "sunrise_dairy"
CURD = "sakthi_curd_1l"
MILK = "aavin_milk_500ml"


# ---------------------------------------------------------------------------------------------------- fixtures

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
    """Seeded catalog plus starting stock through the ledger: 10 L Sakthi curd 1 L, 4 L Aavin milk 500 ml
    (lot expiring 2026-10-14)."""
    s = MemoryStore()
    set_store(s)
    await seed.seed_into(s, seed.load_catalog(CATALOG), env={})
    await ledger.apply(SHOP, CURD, 10.0, "ADJUST", "seed", "seed", "seed-curd", expiry_date="2026-10-20",
                       actor="system")
    await ledger.apply(SHOP, MILK, 4.0, "ADJUST", "seed", "seed", "seed-milk", expiry_date="2026-10-14",
                       actor="system")
    yield s
    set_store(None)


class _Phone:
    """The phone's WebSocket: records server messages and answers request_frame with queued frames."""

    def __init__(self):
        self.msgs: list = []
        self.session: Session | None = None
        self.queued: dict[str, list[str]] = {}
        self._ts = 10_000.0

    async def __call__(self, msg) -> None:
        self.msgs.append(msg)
        if not isinstance(msg, RequestFrameMsg) or msg.stop:
            return
        want = msg.burst.count if msg.burst else 1
        q = self.queued.get(msg.purpose) or []
        for _ in range(min(want, len(q))):
            self._ts += 700
            self.session.frames.add(Frame(q.pop(0), "", 640, 480, self._ts, msg.purpose))

    def queue(self, purpose: str, *frame_ids: str) -> None:
        self.queued.setdefault(purpose, []).extend(frame_ids)


@pytest.fixture
def phone():
    return _Phone()


@pytest.fixture
def session(phone):
    s = Session("owner1", SHOP, "en", "short", phone)
    phone.session = s
    tok_s, tok_c = current_session.set(s), current_tool_call_id.set("tc-1")
    yield s
    current_tool_call_id.reset(tok_c)
    current_session.reset(tok_s)
    sale_tools.active_sale.pop(s.session_id, None)


def use_call(call_id: str) -> None:
    """A new Live tool call (a new idempotency key) inside the same test."""
    current_tool_call_id.set(call_id)


@pytest.fixture
def proposals(monkeypatch):
    made: list[dict] = []
    real = confirm.propose
    previous = confirm._store
    confirm.set_store(confirm.MemoryProposalStore())

    async def recording(kind, prompt, payload, committer, facts=None):
        out = await real(kind, prompt, payload, committer, facts=facts)
        made.append({"kind": kind, "prompt": prompt, "payload": payload, "committer": committer,
                     "facts": facts or {}})
        return out

    monkeypatch.setattr(confirm, "propose", recording)
    yield made
    confirm.set_store(previous)


@pytest.fixture
def notified(monkeypatch):
    said: list[tuple[str, str, str]] = []

    async def fake_notify(shop_id: str, text: str, cue: str = "ack", urgent: bool = False) -> int:
        said.append((shop_id, text, cue))
        return 1

    monkeypatch.setattr(sessions, "notify", fake_notify)
    from aira_live.tools import _speak

    async def fake_speak_to(sess, text: str, cue: str = "ack", urgent: bool = False) -> bool:
        await fake_notify(sess.shop_id, text, cue)
        return True

    monkeypatch.setattr(_speak, "speak_to", fake_speak_to)
    monkeypatch.setattr(_speak, "speak_to_owners", fake_notify)
    return said


def cash_reads(*items):
    """Each item: an int total (good read) or a str hint key (unusable read)."""
    seq = list(items)

    async def fake(frames):
        assert frames, "the cash reader must get frames"
        item = seq.pop(0)
        if isinstance(item, str):
            return CashRead(notes=[], total=None, agreed=False, confidence=0.2, hint_key=item)
        return CashRead(notes=[{"denomination": item, "count": 1}], total=item, agreed=True, confidence=0.9)
    return fake


def _listen(monkeypatch):
    listeners = []

    def fake_subscribe(session_id, cb):
        listeners.append(cb)
        return lambda: listeners.remove(cb)

    monkeypatch.setattr(pay, "_subscribe", fake_subscribe)
    return listeners


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


# ---------------------------------------------------------------------------------------------------- supplier

async def test_supplier_pay_cash_proposes_after_correct_cash(store, session, phone, proposals, notified,
                                                             published, monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(150))
    phone.queue("cash", "c1", "c2")
    r = await pay.supplier_pay("o1", "cash")
    assert notified[0][1].startswith("Pay 150 rupees:")
    assert r["needs_confirm"] is True and proposals[0]["committer"] == "supplier_pay.cash"
    assert proposals[0]["kind"] == "payment"
    assert (r["amount"], r["supplier"], r["method"], r["orderId"]) == (150, "Sakthi curd vendor", "cash", "o1")
    assert r["notes"] == [{"denomination": 150, "count": 1}] and "say" not in r
    # the model proposes, the backend commits: nothing about money or state moves before the owner's yes
    assert (await _repo.get_order(SHOP, "o1"))["state"] == "DELIVERY_RECONCILED"
    assert not [k for k in store.data if "/payments/" in k] and not [p for tpc, p in published if tpc == "orders"]


async def test_supplier_pay_no_leaves_no_pending_payment_or_state(store, session, proposals, notified, published):
    await _reconciled(store)
    r = await pay.supplier_pay("o1", "upi")
    out = await confirm.resolve(r["confirm_id"], "no")
    assert out["status"] == "rejected"
    order = await _repo.get_order(SHOP, "o1")
    assert order["state"] == "DELIVERY_RECONCILED" and "paymentId" not in order
    assert not [k for k in store.data if "/payments/" in k] and not [p for tpc, p in published if tpc == "orders"]
    # the owner can simply ask again
    use_call("tc-2")
    again = await pay.supplier_pay("o1", "upi")
    assert again["needs_confirm"] is True


async def test_supplier_pay_cash_hint_key_retake(store, session, phone, proposals, notified, monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads("cash.spread_notes"))
    phone.queue("cash", "c1", "c2")
    r = await pay.supplier_pay("o1", "cash")
    assert (r["ok"], r["needs_retake"], r["error"], r["hint"]) == (False, True, "CASH_UNCLEAR", "cash.spread_notes")
    assert r["say_en"] == pay.CASH_HINTS_EN["cash.spread_notes"] and proposals == []


async def test_supplier_pay_no_frames_asks_again(store, session, phone, proposals, notified, monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(pay, "FRAME_TIMEOUT_S", 0.05)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(150))
    r = await pay.supplier_pay("o1", "cash")
    assert (r["needs_retake"], r["error"], r["hint"]) == (True, "CASH_UNCLEAR", None)
    assert r["say_en"] == pay.CASH_UNCLEAR_EN and proposals == []


async def test_supplier_pay_wrong_total(store, session, phone, proposals, notified, monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(100))
    phone.queue("cash", "c1", "c2")
    r = await pay.supplier_pay("o1", "cash")
    assert (r["ok"], r["needs_retake"], r["error"], r["seen"], r["amount"]) == (False, True, "CASH_WRONG", 100, 150)
    assert r["say_en"] == "Seen 100 rupees, but it should be 150." and proposals == []


async def test_supplier_pay_not_ready_before_reconcile(store, session, proposals):
    await _reconciled(store)
    order = await _repo.get_order(SHOP, "o1")
    await _repo.save_order(SHOP, {**order, "state": "DELIVERY_PENDING"})
    r = await pay.supplier_pay("o1", "cash")
    assert r == pay.NOT_READY and r["error"] == "NOT_READY" and proposals == []
    assert await pay.supplier_pay("o1", "card") == pay.METHOD_Q


async def test_supplier_pay_commit_confirms_payment_and_order(store, session, phone, proposals, notified,
                                                              published, monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(150))
    phone.queue("cash", "c1", "c2")
    await pay.supplier_pay("o1", "cash")
    payload = proposals[0]["payload"]
    first = await pay._commit_supplier_cash(payload, None)
    again = await pay._commit_supplier_cash(payload, None)
    assert first["say_en"] == again["say_en"] == "Payment of 150 rupees to Sakthi curd vendor recorded."
    assert (first["amount"], first["supplier"], first["method"]) == (150, "Sakthi curd vendor", "cash")
    assert "already" not in first and again["already"] is True
    assert first["paymentId"] == again["paymentId"]
    paid = await store.get(paths.payment(SHOP, first["paymentId"]))
    assert (paid["state"], paid["direction"], paid["method"], paid["amount"]) == ("CONFIRMED", "out", "cash", 150)
    assert paid["evidence"]["type"] == "cash_count"
    order = await _repo.get_order(SHOP, "o1")
    assert order["state"] == "PAYMENT_CONFIRMED" and order["paymentId"] == first["paymentId"]
    assert [p["state"] for tpc, p in published if tpc == "orders"] == ["PAYMENT_PENDING", "PAYMENT_CONFIRMED"]
    assert len([k for k in store.data if "/payments/" in k]) == 1  # the retried confirm records nothing twice


async def test_supplier_pay_upi_commits_through_confirm_yes(store, session, proposals, notified):
    await _reconciled(store)
    r = await pay.supplier_pay("o1", "upi")
    assert r["say_en"] == "Pay 150 rupees to Sakthi curd vendor from your UPI app, then say yes."
    assert (r["amount"], r["supplier"], r["method"]) == (150, "Sakthi curd vendor", "upi")
    assert proposals[0]["committer"] == "supplier_pay.upi"
    out = await confirm.resolve(r["confirm_id"], "yes")
    assert out["ok"] is True
    paid = await store.get(paths.payment(SHOP, out["paymentId"]))
    assert (paid["state"], paid["method"], paid["evidence"]["type"]) == ("CONFIRMED", "upi", "user_confirm")
    assert (await _repo.get_order(SHOP, "o1"))["state"] == "PAYMENT_CONFIRMED"


# ---------------------------------------------------------------------------------------------------- sales

async def _curd_sale(liters=2) -> dict:
    return await sale_tools.sale_start([{"product": "sakthi curd 1 litre", "qty": liters, "unit": "L"}])


async def test_sale_start_prices_and_checks_stock(store, session, phone):
    r = await _curd_sale()
    product = await store.get(paths.product(SHOP, CURD))
    price = product["price"]
    assert r["total"] == 2 * price and r["say_en"].endswith(f", total {2 * price} rupees. Cash or UPI?")
    assert r["items"] == [{"product": product["name"], "qty": 2.0, "unit": "L"}] and "say" not in r
    assert sale_tools.active_sale[session.session_id] == r["saleId"]
    assert (await store.get(paths.sale(SHOP, r["saleId"])))["state"] == "SALE_PENDING"


async def test_sale_start_insufficient_stock(store, session):
    r = await _curd_sale(liters=50)
    name = (await store.get(paths.product(SHOP, CURD)))["name"]
    have = await ledger.on_hand(SHOP, CURD)
    assert (r["ok"], r["error"], r["product"], r["have"], r["onHand"]) == (
        False, "InsufficientStock", name, fmt_qty(have), have)
    assert r["say_en"] == hints.english("InsufficientStock", product=name, have=fmt_qty(have))


async def test_sale_start_unknown_product(store, session):
    r = await sale_tools.sale_start([{"product": "chocolate cake", "qty": 1}])
    assert r == hints.fact("PRODUCT_UNKNOWN", product="chocolate cake")
    assert (r["ok"], r["error"], r["product"]) == (False, "PRODUCT_UNKNOWN", "chocolate cake")
    assert await sale_tools.sale_start([]) == sale_tools.NO_ITEMS


async def test_sale_pay_cash_with_change(store, session, phone, proposals, notified, monkeypatch):
    s = await _curd_sale()
    use_call("tc-2")
    change = 200 - s["total"]
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(200, change))
    phone.queue("cash", "c1", "c2", "c3", "c4")
    await pay.sale_pay(s["saleId"], "cash")
    assert notified[0][1].startswith(f"Received 200. Give back {change} rupees:")
    p = proposals[0]
    assert (p["kind"], p["committer"], p["payload"]["evidence"]) == (
        "sale", "sale.complete", {"type": "cash_count", "total": 200})
    assert p["facts"] == {"saleId": s["saleId"], "total": s["total"], "method": "cash", "tendered": 200,
                          "change": change}


async def test_sale_pay_cash_wrong_change(store, session, phone, proposals, notified, monkeypatch):
    s = await _curd_sale()
    change = 200 - s["total"]
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(200, change + 50))
    phone.queue("cash", "c1", "c2", "c3", "c4")
    r = await pay.sale_pay(s["saleId"], "cash")
    assert (r["ok"], r["needs_retake"], r["error"], r["seen"], r["change"]) == (
        False, True, "CHANGE_WRONG", change + 50, change)
    assert r["say_en"] == f"That change is {change + 50} rupees. It should be {change}." and proposals == []


async def test_sale_pay_cash_short_tender(store, session, phone, proposals, notified, monkeypatch):
    s = await _curd_sale()
    monkeypatch.setattr(vcash, "read_cash", cash_reads(50))
    phone.queue("cash", "c1", "c2")
    r = await pay.sale_pay(s["saleId"], "cash")
    assert (r["ok"], r["error"], r["short"], r["tendered"], r["total"]) == (
        False, "InsufficientTender", s["total"] - 50, 50, s["total"])
    assert r["say_en"] == hints.english("InsufficientTender", short=s["total"] - 50) and proposals == []


async def test_sale_pay_cash_hint_key_retake(store, session, phone, proposals, monkeypatch):
    s = await _curd_sale()
    monkeypatch.setattr(vcash, "read_cash", cash_reads("cash.spread_notes"))
    phone.queue("cash", "c1", "c2")
    r = await pay.sale_pay(s["saleId"], "cash")
    assert (r["needs_retake"], r["error"], r["hint"]) == (True, "CASH_UNCLEAR", "cash.spread_notes")
    assert r["say_en"] == pay.CASH_HINTS_EN["cash.spread_notes"] and proposals == []


async def test_sale_pay_without_open_sale(store, session, proposals):
    r = await pay.sale_pay("", "cash")
    assert r == pay.SALE_NONE and r["error"] == "SALE_NONE" and proposals == []


async def test_sale_commit_is_idempotent(store, session, phone, proposals, notified, published, monkeypatch):
    s = await _curd_sale()
    before = await ledger.on_hand(SHOP, CURD)
    use_call("tc-2")
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(s["total"]))
    phone.queue("cash", "c1", "c2")
    await pay.sale_pay(s["saleId"], "cash")
    payload = proposals[0]["payload"]
    first = await pay._commit_sale(payload, None)
    again = await pay._commit_sale(payload, None)               # retried confirm
    assert first["say_en"].startswith("Sale complete.") and first["total"] == s["total"]
    assert again["already"] is True and again["saleId"] == s["saleId"]
    assert await ledger.on_hand(SHOP, CURD) == before - 2.0
    assert len([1 for tpc, p in published if tpc == "sales" and p["sale_id"] == s["saleId"]]) == 1
    assert (await store.get(paths.sale(SHOP, s["saleId"])))["state"] == "SALE_COMPLETED"
    assert session.session_id not in sale_tools.active_sale


async def test_sale_commit_warns_low_stock(store, session, phone, proposals, notified, monkeypatch):
    s = await _curd_sale(liters=6)                              # 10 L → 4 L, at or below the reorder threshold
    monkeypatch.setattr(vcash, "read_cash", cash_reads(s["total"]))
    phone.queue("cash", "c1", "c2")
    use_call("tc-2")
    await pay.sale_pay(s["saleId"], "cash")
    r = await pay._commit_sale(proposals[0]["payload"], None)
    name = (await store.get(paths.product(SHOP, CURD)))["name"]
    assert r["lowStock"] == [{"sku": CURD, "product": name, "left": 4.0, "unit": "L"}] == r["stock"]
    assert r["say_en"] == f"Sale complete. Only 4 L left of {name}. Order more?"


async def test_upi_soundbox_correct_amount_proposes(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale()
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
    r = await pay._commit_sale(proposals[-1]["payload"], None)
    assert r["ok"] is True
    sale = await store.get(paths.sale(SHOP, s["saleId"]))
    assert sale["state"] == "SALE_COMPLETED"
    paid = await store.get(paths.payment(SHOP, sale["paymentId"]))
    assert (paid["method"], paid["state"], paid["evidence"]["type"]) == ("upi", "CONFIRMED", "soundbox")


async def test_upi_soundbox_wrong_amount_does_not_confirm(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale()
    use_call("tc-2")
    monkeypatch.setattr(pay, "UPI_WAIT_S", 0.3)
    listeners = _listen(monkeypatch)
    task = asyncio.create_task(pay.sale_pay(s["saleId"], "upi"))
    while not listeners:
        await asyncio.sleep(0)
    short = s["total"] - 20
    await listeners[0](f"Received rupees {short} on PhonePe")
    assert not task.done()
    await task
    assert any(text == "20 rupees short, ask for 20 more." for _, text, _ in notified)
    p = proposals[-1]
    assert p["payload"]["evidence"]["type"] == "user_confirm"
    assert p["prompt"] == f"Only {short} of {s['total']} rupees received by UPI, 20 short. Complete the sale anyway?"
    assert p["facts"] == {"saleId": s["saleId"], "total": s["total"], "method": "upi", "paid": short, "short": 20,
                          "approx": True}
    assert listeners == []


async def test_sale_pay_proposals_reach_the_phone(store, session, phone, proposals, notified, monkeypatch):
    s = await _curd_sale()
    monkeypatch.setattr(pay, "UPI_WAIT_S", 0.05)
    _listen(monkeypatch)
    r = await pay.sale_pay(s["saleId"], "upi")
    assert r["needs_confirm"] is True
    assert [m.id for m in phone.msgs if isinstance(m, ConfirmMsg)] == [r["confirm_id"]]


# ---------------------------------------------------------------------------------------------------- stock

async def test_stock_query_one_and_all(store, session):
    one = await stock.stock_query("sakthi curd 1 litre")
    have = await ledger.on_hand(SHOP, CURD)
    assert (one["sku"], one["qty"], one["unit"]) == (CURD, have, "L") and have == 10.0
    everything = await stock.stock_query()
    assert everything["ok"] is True
    assert {i["sku"]: i["qty"] for i in everything["items"]}[CURD] == 10.0


async def test_stock_query_unknown_product(store, session):
    r = await stock.stock_query("chocolate cake")
    assert (r["ok"], r["error"], r["product"]) == (False, "PRODUCT_UNKNOWN", "chocolate cake")


async def test_expiry_check_lists_items_expiring_tomorrow(store, session, monkeypatch):
    monkeypatch.setattr(clock, "today_ist", lambda: date(2026, 10, 13))   # milk lot expires 2026-10-14
    r = await stock.expiry_check()
    assert [(a["sku"], a["status"]) for a in r["alerts"]] == [(MILK, "expires_tomorrow")]


async def test_expiry_check_nothing_due(store, session, monkeypatch):
    monkeypatch.setattr(clock, "today_ist", lambda: date(2026, 10, 1))
    r = await stock.expiry_check()
    assert r["ok"] is True and r["alerts"] == []


# ---------------------------------------------------------------------------------------------------- video script lines

async def test_a_completed_sale_says_how_much_is_left(store, session, phone, proposals, notified, monkeypatch):
    """The video: "Sale complete. Stock: 24 litres." """
    s = await _curd_sale()                                      # 2 L of 10 L
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(s["total"]))
    phone.queue("cash", "c1", "c2")
    use_call("tc-2")
    await pay.sale_pay(s["saleId"], "cash")
    r = await pay._commit_sale(proposals[0]["payload"], None)
    assert r["say_en"] == "Sale complete. Sakthi Curd 1 L: 8 L left."
    assert r["stock"] == [{"sku": CURD, "product": "Sakthi Curd 1 L", "left": 8.0, "unit": "L"}]
    assert r["lowStock"] == [] and (r["total"], r["method"]) == (s["total"], "cash")


async def test_the_stock_summary_names_what_is_low(store, session, monkeypatch):
    """The video: "Aavin milk 500 ml is low: 8 left." The facts: every product with its quantity and whether it is
    at or below its reorder level; an empty product has qty 0."""
    monkeypatch.setattr(stock, "LOW_LIMIT", 20)
    r = await stock.stock_query()
    by_sku = {i["sku"]: i for i in r["items"]}
    assert (by_sku[MILK]["qty"], by_sku[MILK]["unit"], by_sku[MILK]["low"]) == (4.0, "L", True)
    assert (by_sku["aavin_butter_100g"]["qty"], by_sku["aavin_butter_100g"]["low"]) == (0.0, True)


async def test_expiry_counts_pouches_in_packs(store, session, monkeypatch):
    """The video: "Six curd packs expire tomorrow." 4 L of 500 ml pouches is 8 packs: the ledger fact is 4 L."""
    monkeypatch.setattr(clock, "today_ist", lambda: date(2026, 10, 13))
    r = await stock.expiry_check()
    a = r["alerts"][0]
    assert (a["sku"], float(a["qty"]), a["unit"]) == (MILK, 4.0, "L")


async def test_two_milk_is_two_pouches_not_two_litres(store, session):
    """Bug sweep: a customer's "2 milk" was charged as 2 litres (₹88) instead of 2 pouches (₹44)."""
    r = await sale_tools.sale_start([{"product": "aavin milk 500 ml", "qty": 2}])
    assert r["total"] == 44


# ---------------------------------------------------------------------------------------------------- billing speech
# These lines go to Sam's earphone only (speak_to): he may miscount, so AIRA says the running total, the change
# with the notes to hand back, and whether a UPI payment is short or over. Nothing completes the sale until the
# paid total equals the bill or Sam explicitly confirms the shortfall.

async def _curd_and_milk() -> dict:
    return await sale_tools.sale_start([{"product": "sakthi curd 1 litre", "qty": 2, "unit": "L"},
                                        {"product": "aavin milk 500 ml", "qty": 1}])


async def _upi(monkeypatch, sale_id: str):
    listeners = _listen(monkeypatch)
    task = asyncio.create_task(pay.sale_pay(sale_id, "upi"))
    while not listeners:
        await asyncio.sleep(0)
    return task, listeners


async def test_sale_start_says_items_and_running_total(store, session):
    r = await _curd_and_milk()
    assert r["total"] == 2 * 60 + 22
    assert r["items"] == [{"product": "Sakthi Curd 1 L", "qty": 2.0, "unit": "L"},
                          {"product": "Aavin Milk 500 ml", "qty": 0.5, "unit": "L"}]
    assert r["say_en"] == ("2 L Sakthi Curd 1 L and 0.5 L Aavin Milk 500 ml (1 × 500 ml pouch = 0.5 L), "
                           "total 142 rupees. Cash or UPI?")


async def test_sale_add_merges_into_the_open_sale_and_says_the_running_total(store, session):
    first = await _curd_sale()
    use_call("tc-2")
    r = await sale_tools.sale_add([{"product": "aavin milk 500 ml", "qty": 1}])
    assert r["ok"] is True and r["total"] == 142 and r["saleId"] != first["saleId"]
    assert [i["product"] for i in r["items"]] == ["Sakthi Curd 1 L", "Aavin Milk 500 ml"]
    assert r["say_en"] == ("2 L Sakthi Curd 1 L and 0.5 L Aavin Milk 500 ml (1 × 500 ml pouch = 0.5 L), "
                           "total 142 rupees so far. Cash or UPI?")
    assert sale_tools.active_sale[session.session_id] == r["saleId"]
    assert (await store.get(paths.sale(SHOP, first["saleId"])))["state"] == "SALE_VOID"
    assert (await store.get(paths.sale(SHOP, r["saleId"])))["state"] == "SALE_PENDING"


async def test_sale_add_without_open_sale_starts_one(store, session):
    r = await sale_tools.sale_add([{"product": "aavin milk 500 ml", "qty": 2}])
    assert r["ok"] is True and r["total"] == 44 and sale_tools.active_sale[session.session_id] == r["saleId"]


async def test_sale_add_insufficient_stock_keeps_the_open_sale(store, session):
    first = await _curd_sale()
    use_call("tc-2")
    r = await sale_tools.sale_add([{"product": "sakthi curd 1 litre", "qty": 20, "unit": "L"}])
    assert r["ok"] is False
    assert sale_tools.active_sale[session.session_id] == first["saleId"]
    assert (await store.get(paths.sale(SHOP, first["saleId"])))["state"] == "SALE_PENDING"


async def test_cash_change_line_names_the_notes_to_give_back(store, session, phone, proposals, notified,
                                                            monkeypatch):
    s = await _curd_sale()                                      # 120
    use_call("tc-2")
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(200, 80))
    phone.queue("cash", "c1", "c2", "c3", "c4")
    await pay.sale_pay(s["saleId"], "cash")
    assert notified[0][1] == ("Received 200. Give back 80 rupees: one 50, one 20, one 10. "
                              "Show the change to the camera.")
    assert proposals[0]["prompt"] == "Received 200, change 80. Complete the sale?"
    assert (proposals[0]["facts"]["tendered"], proposals[0]["facts"]["change"]) == (200, 80)


async def test_cash_change_line_counts_repeated_notes(store, session, phone, proposals, notified, monkeypatch):
    s = await _curd_sale()                                      # 120
    use_call("tc-2")
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(160, 40))
    phone.queue("cash", "c1", "c2", "c3", "c4")
    await pay.sale_pay(s["saleId"], "cash")
    assert notified[0][1].startswith("Received 160. Give back 40 rupees: two 20s.")


def test_notes_words_are_english_whatever_the_language():
    notes = [{"denomination": 50, "count": 1}, {"denomination": 10, "count": 3}, {"denomination": 20, "count": 12}]
    assert pay._notes_words(notes) == "one 50, three 10s, 12 20s"
    assert pay._notes_words(notes, "ta") == pay._notes_words(notes, "hi") == pay._notes_words(notes, "en")


async def test_upi_exact_amount_proposes_with_soundbox_evidence(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale()
    use_call("tc-2")
    task, listeners = await _upi(monkeypatch, s["saleId"])
    await listeners[0]("Received rupees 120 on PhonePe")
    r = await task
    assert r["needs_confirm"] is True
    ev = proposals[-1]["payload"]["evidence"]
    assert (ev["type"], ev["amount"], ev["provider"], ev["payments"]) == ("soundbox", 120, "PhonePe", [120])
    assert proposals[-1]["prompt"] == "120 rupees received by UPI. Complete the sale?"
    assert proposals[-1]["facts"] == {"saleId": s["saleId"], "total": 120, "method": "upi", "paid": 120}
    assert (r["paid"], r["total"], r["method"]) == (120, 120, "upi")
    assert not any(c == "mismatch" for _, _, c in notified)


async def test_upi_short_payment_says_how_much_more_and_keeps_waiting(store, session, proposals, notified,
                                                                      monkeypatch):
    s = await _curd_sale()
    use_call("tc-2")
    monkeypatch.setattr(pay, "UPI_WAIT_S", 0.3)
    task, listeners = await _upi(monkeypatch, s["saleId"])
    await listeners[0]("Received rupees 110 on PhonePe")
    assert not task.done()
    assert notified[-1][1:] == ("10 rupees short, ask for 10 more.", "mismatch")
    r = await task                                              # soundbox went quiet: Sam decides
    assert r["needs_confirm"] is True
    p = proposals[-1]
    assert p["prompt"] == "Only 110 of 120 rupees received by UPI, 10 short. Complete the sale anyway?"
    assert p["payload"]["evidence"] == {"type": "user_confirm", "heard": 110, "payments": [110]}
    assert listeners == []


async def test_upi_two_partial_payments_add_up_to_the_bill(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale()
    use_call("tc-2")
    task, listeners = await _upi(monkeypatch, s["saleId"])
    await listeners[0]("Received rupees 50 on PhonePe")
    assert not task.done() and notified[-1][1] == "70 rupees short, ask for 70 more."
    await listeners[0]("Received rupees 70 on Google Pay")
    r = await task
    assert r["needs_confirm"] is True
    ev = proposals[-1]["payload"]["evidence"]
    assert (ev["type"], ev["amount"], ev["payments"], ev["provider"]) == ("soundbox", 120, [50, 70], None)
    assert proposals[-1]["prompt"] == "120 rupees received by UPI. Complete the sale?"
    r = await pay._commit_sale(proposals[-1]["payload"], None)
    assert r["ok"] is True
    assert (await store.get(paths.sale(SHOP, s["saleId"])))["state"] == "SALE_COMPLETED"


async def test_upi_over_payment_says_what_to_return_and_asks(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale()
    use_call("tc-2")
    task, listeners = await _upi(monkeypatch, s["saleId"])
    await listeners[0]("Received rupees 140 on PhonePe")
    r = await task
    assert notified[-1][1:] == ("20 rupees extra received, return 20.", "mismatch")
    assert r["needs_confirm"] is True
    p = proposals[-1]
    assert p["prompt"] == "140 rupees received by UPI, 20 extra. Return 20 and complete the sale?"
    assert (r["paid"], r["extra"], r["total"]) == (140, 20, 120)
    ev = p["payload"]["evidence"]
    assert (ev["type"], ev["amount"], ev["heard"], ev["extra"], ev["payments"]) == ("soundbox", 120, 140, 20, [140])
    done = await pay._commit_sale(p["payload"], None)
    assert done["ok"] is True


async def test_upi_short_then_over_across_partials(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale()
    use_call("tc-2")
    task, listeners = await _upi(monkeypatch, s["saleId"])
    await listeners[0]("Received rupees 100 on PhonePe")
    await listeners[0]("Received rupees 50 on PhonePe")
    await task
    assert notified[-2][1] == "20 rupees short, ask for 20 more."
    assert notified[-1][1] == "30 rupees extra received, return 30."
    assert proposals[-1]["payload"]["evidence"]["payments"] == [100, 50]


async def test_upi_no_sale_completes_before_the_bill_is_covered(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale()
    use_call("tc-2")
    monkeypatch.setattr(pay, "UPI_WAIT_S", 0.2)
    task, listeners = await _upi(monkeypatch, s["saleId"])
    await listeners[0]("Received rupees 100 on PhonePe")
    await listeners[0]("Received rupees 10 on PhonePe")
    assert not task.done() and proposals == []
    r = await task
    assert (await store.get(paths.sale(SHOP, s["saleId"])))["state"] == "SALE_PENDING"
    assert proposals[-1]["payload"]["evidence"]["type"] == "user_confirm"
    assert (r["paid"], r["short"], r["approx"]) == (110, 10, True)


async def test_upi_silent_soundbox_asks_the_owner(store, session, proposals, notified, monkeypatch):
    s = await _curd_sale()
    use_call("tc-2")
    monkeypatch.setattr(pay, "UPI_WAIT_S", 0.05)
    _listen(monkeypatch)
    r = await pay.sale_pay(s["saleId"], "upi")
    assert r["needs_confirm"] is True and (r["paid"], r["heard"], r["total"]) == (0, False, 120)
    assert proposals[-1]["prompt"] == "The soundbox was not heard. Did you receive 120 rupees by UPI?"
    assert proposals[-1]["payload"]["evidence"] == {"type": "user_confirm"}
