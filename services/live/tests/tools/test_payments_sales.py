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
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import Session, current_session, current_tool_call_id
from aira_live.tools import _repo, stock
from aira_live.tools import payments as pay
from aira_live.tools import sales as sale_tools
from aira_live.vision import cash as vcash
from aira_live.vision.cash import CashRead

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "content" / "catalog" / "murugan_dairy.json"
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

    async def recording(kind, prompt, payload, committer):
        out = await real(kind, prompt, payload, committer)
        made.append({"kind": kind, "prompt": prompt, "payload": payload, "committer": committer})
        return out

    monkeypatch.setattr(confirm, "propose", recording)
    yield made
    confirm.set_store(previous)


@pytest.fixture
def notified(monkeypatch):
    said: list[tuple[str, str, str]] = []

    async def fake_notify(shop_id: str, text: str, cue: str = "ack") -> int:
        said.append((shop_id, text, cue))
        return 1

    monkeypatch.setattr(sessions, "notify", fake_notify)
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
    assert r["needs_retake"] is True and r["say"] == t("cash.spread_notes", "en") and proposals == []


async def test_supplier_pay_no_frames_asks_again(store, session, phone, proposals, notified, monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(pay, "FRAME_TIMEOUT_S", 0.05)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(150))
    r = await pay.supplier_pay("o1", "cash")
    assert r["needs_retake"] is True and r["say"] == t("k2.cash.unclear", "en") and proposals == []


async def test_supplier_pay_wrong_total(store, session, phone, proposals, notified, monkeypatch):
    await _reconciled(store)
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(100))
    phone.queue("cash", "c1", "c2")
    r = await pay.supplier_pay("o1", "cash")
    assert r["say"] == "I see 100 rupees, but it should be 150." and proposals == []


async def test_supplier_pay_not_ready_before_reconcile(store, session, proposals):
    await _reconciled(store)
    order = await _repo.get_order(SHOP, "o1")
    await _repo.save_order(SHOP, {**order, "state": "DELIVERY_PENDING"})
    r = await pay.supplier_pay("o1", "cash")
    assert r == {"ok": False, "say": t("k2.pay.not_ready", "en")} and proposals == []


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
    assert first["say"] == again["say"] == "Payment of 150 rupees to Sakthi curd vendor recorded."
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
    assert r["say"] == t("k2.pay.upi_q", "en", amount=150, supplier="Sakthi curd vendor")
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
    price = (await store.get(paths.product(SHOP, CURD)))["price"]
    assert r["total"] == 2 * price and r["say"].endswith(f"Total {2 * price} rupees. Cash or UPI?")
    assert sale_tools.active_sale[session.session_id] == r["saleId"]
    assert (await store.get(paths.sale(SHOP, r["saleId"])))["state"] == "SALE_PENDING"


async def test_sale_start_insufficient_stock(store, session):
    r = await _curd_sale(liters=50)
    name = (await store.get(paths.product(SHOP, CURD)))["name"]
    have = await ledger.on_hand(SHOP, CURD)
    assert r["ok"] is False and r["say"] == hints.say("InsufficientStock", "en", product=name, have=fmt_qty(have))


async def test_sale_start_unknown_product(store, session):
    r = await sale_tools.sale_start([{"product": "chocolate cake", "qty": 1}])
    assert r["ok"] is False and r["say"] == hints.say("PRODUCT_UNKNOWN", "en", product="chocolate cake")


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


async def test_sale_pay_cash_wrong_change(store, session, phone, proposals, notified, monkeypatch):
    s = await _curd_sale()
    change = 200 - s["total"]
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)
    monkeypatch.setattr(vcash, "read_cash", cash_reads(200, change + 50))
    phone.queue("cash", "c1", "c2", "c3", "c4")
    r = await pay.sale_pay(s["saleId"], "cash")
    assert r["say"] == t("k2.sale.change_wrong", "en", seen=change + 50, change=change) and proposals == []


async def test_sale_pay_cash_short_tender(store, session, phone, proposals, notified, monkeypatch):
    s = await _curd_sale()
    monkeypatch.setattr(vcash, "read_cash", cash_reads(50))
    phone.queue("cash", "c1", "c2")
    r = await pay.sale_pay(s["saleId"], "cash")
    assert r["say"] == hints.say("InsufficientTender", "en", short=s["total"] - 50) and proposals == []


async def test_sale_pay_cash_hint_key_retake(store, session, phone, proposals, monkeypatch):
    s = await _curd_sale()
    monkeypatch.setattr(vcash, "read_cash", cash_reads("cash.spread_notes"))
    phone.queue("cash", "c1", "c2")
    r = await pay.sale_pay(s["saleId"], "cash")
    assert r["needs_retake"] is True and r["say"] == t("cash.spread_notes", "en") and proposals == []


async def test_sale_pay_without_open_sale(store, session, proposals):
    r = await pay.sale_pay("", "cash")
    assert r == {"ok": False, "say": t("k2.sale.none", "en")} and proposals == []


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
    await pay._commit_sale(payload, None)                       # retried confirm
    assert first["say"].startswith("Sale complete.")
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
    assert r["lowStock"] == [t("k2.sale.low_stock", "en", left="4 L", product=name)]


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
    wrong = s["total"] * 10
    await listeners[0](f"Received rupees {wrong} on PhonePe")
    await task
    assert any(text == f"The soundbox said {wrong} rupees, but the bill is {s['total']}." for _, text, _ in notified)
    p = proposals[-1]
    assert p["payload"]["evidence"] == {"type": "user_confirm"}
    assert p["prompt"] == t("k2.sale.upi_timeout_q", "en", total=s["total"])
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
    assert one["qty"] == have == 10.0 and one["say"].endswith(f"{fmt_qty(have)} L in stock.")
    everything = await stock.stock_query()
    assert everything["say"].startswith("Stock: ")
    assert {i["sku"]: i["qty"] for i in everything["items"]}[CURD] == 10.0


async def test_stock_query_unknown_product(store, session):
    r = await stock.stock_query("chocolate cake")
    assert r["ok"] is False and r["say"] == hints.say("PRODUCT_UNKNOWN", "en", product="chocolate cake")


async def test_expiry_check_lists_items_expiring_tomorrow(store, session, monkeypatch):
    monkeypatch.setattr(clock, "today_ist", lambda: date(2026, 10, 13))   # milk lot expires 2026-10-14
    r = await stock.expiry_check()
    assert [a["sku"] for a in r["alerts"]] == [MILK]
    assert r["say"].endswith("Sell or return these first.") and t("k2.when.tomorrow", "en") in r["say"]


async def test_expiry_check_nothing_due(store, session, monkeypatch):
    monkeypatch.setattr(clock, "today_ist", lambda: date(2026, 10, 1))
    r = await stock.expiry_check()
    assert r == {"ok": True, "alerts": [], "say": t("k2.expiry.none", "en")}
