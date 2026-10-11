"""Sam's whole shop loop end to end (kanish/02 Task 6), on the real engine with a MemoryStore.

Order 3 L Sakthi curd -> vendor message -> delivery counted one pack at a time -> invoice checked -> vendor paid in
cash -> a customer buys 2 L curd by UPI, confirmed by the soundbox -> "how much curd is left?".

Everything real runs: the engine (catalog, ledger, states, reconcile, payments, sales, money, soundbox, outbox), the
foundation's confirm/session/ids/frames and the k2 tools. Only these are faked: the vision calls (Gemini), the
outgoing vendor message, the Live transcript subscription, sessions.notify and the analytics publisher. The phone
is a fake that answers request_frame messages with queued or streamed frames. Nothing reaches the network.
"""
import asyncio
from pathlib import Path

import pytest

import aira_live.messaging as messaging
from aira_live import confirm, sessions
from aira_live.business import ledger, outbox, paths, seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.contracts import RequestFrameMsg
from aira_live.frames import Frame
from aira_live.session import Session, current_session, current_tool_call_id
from aira_live.tools import _repo, delivery, orders, stock
from aira_live.tools import payments as pay
from aira_live.tools import sales as sale_tools
from aira_live.vision import cash as vcash
from aira_live.vision import identify as vid
from aira_live.vision import invoice as vinv
from aira_live.vision.cash import CashRead
from aira_live.vision.identify import IdentifyResult
from aira_live.vision.invoice import InvoiceLine, InvoiceRead

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "content" / "catalog" / "murugan_dairy.json"
SHOP = "sunrise_dairy"
CURD = "sakthi_curd_1l"


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
    s = MemoryStore()
    set_store(s)
    await seed.seed_into(s, seed.load_catalog(CATALOG), env={})
    yield s
    set_store(None)


class _Phone:
    """The phone's WebSocket: records server messages; answers request_frame with queued frames (bursts) or a
    timed stream (continuous)."""

    def __init__(self):
        self.msgs: list = []
        self.session: Session | None = None
        self.queued: dict[str, list[str]] = {}
        self.streams: dict[str, list[tuple[str, float]]] = {}
        self._tasks: list[asyncio.Task] = []
        self._ts = 100_000.0

    async def __call__(self, msg) -> None:
        self.msgs.append(msg)
        if not isinstance(msg, RequestFrameMsg) or msg.stop:
            return
        if msg.continuous and msg.purpose in self.streams:
            self._tasks.append(asyncio.create_task(self._feed(msg.purpose, self.streams.pop(msg.purpose))))
            return
        want = msg.burst.count if msg.burst else 1
        q = self.queued.get(msg.purpose) or []
        for _ in range(min(want, len(q))):
            self._ts += 700
            self.session.frames.add(Frame(q.pop(0), "", 640, 480, self._ts, msg.purpose))

    async def _feed(self, purpose: str, frames: list[tuple[str, float]]) -> None:
        for fid, ts in frames:
            await asyncio.sleep(0.01)
            self.session.frames.add(Frame(fid, "", 640, 480, ts, purpose))

    def queue(self, purpose: str, *frame_ids: str) -> None:
        self.queued.setdefault(purpose, []).extend(frame_ids)


@pytest.fixture
def phone():
    return _Phone()


@pytest.fixture
def session(phone):
    s = Session("owner1", SHOP, "en", "short", phone)
    phone.session = s
    tok_s, tok_c = current_session.set(s), current_tool_call_id.set("tc-0")
    yield s
    current_tool_call_id.reset(tok_c)
    current_session.reset(tok_s)
    sale_tools.active_sale.pop(s.session_id, None)
    delivery._active.pop(s.session_id, None)


def use_call(call_id: str) -> None:
    """A new Live tool call (a new idempotency key)."""
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


# ---------------------------------------------------------------------------------------------------- the loop

async def test_sunrise_full_loop(store, session, phone, proposals, notified, published, monkeypatch):
    start = await ledger.on_hand(SHOP, CURD)
    product = await store.get(paths.product(SHOP, CURD))

    # 1. "Order 3 litres Sakthi curd from the Sakthi vendor" -> read-back -> yes -> sent to the vendor
    sent_orders = []

    async def fake_send(shop_id, supplier, order):
        sent_orders.append((shop_id, supplier["id"], order["id"]))
        return {"channel": "whatsapp", "messageId": "wamid.E2E"}

    monkeypatch.setattr(messaging, "send_order", fake_send)
    use_call("c1")
    r = await orders.order_create("sakthi vendor", [{"product": "sakthi curd", "qty": 3, "unit": "L"}])
    assert r["needs_confirm"] is True and proposals[-1]["committer"] == "order.send"
    oid = proposals[-1]["payload"]["order_id"]
    assert (await _repo.get_order(SHOP, oid))["state"] == "DRAFT"
    assert sent_orders == []                                   # nothing goes out before the owner's yes
    use_call("c2")
    await orders._commit_send(proposals[-1]["payload"], None)
    assert sent_orders == [(SHOP, "sakthi_vendor", oid)]
    assert (await _repo.get_order(SHOP, oid))["state"] == "ORDER_PLACED"
    assert await ledger.on_hand(SHOP, CURD) == start          # an order is not a delivery

    # 2. The delivery comes: counted one pack at a time (3 packs, with gaps between them)
    use_call("c3")
    await delivery.delivery_start(oid)
    monkeypatch.setattr(delivery, "COUNT_IDLE_S", 0.2)

    async def identify(frame, candidates):
        return IdentifyResult(sku=CURD if frame.id.startswith("p") else None, confidence=0.9, alternatives=[])

    monkeypatch.setattr(vid, "identify_product", identify)
    phone.streams["identify"] = [("p1", 1000), ("g1", 1800), ("p2", 2600), ("g2", 3400), ("p3", 4200)]
    use_call("c4")
    r = await delivery.delivery_count(CURD)
    assert proposals[-1]["committer"] == "delivery.record_count" and proposals[-1]["payload"]["qty"] == 3.0
    assert await ledger.on_hand(SHOP, CURD) == start          # the count waits for the owner's yes
    use_call("c5")
    await delivery._commit_count(proposals[-1]["payload"], None)
    await delivery._commit_count(proposals[-1]["payload"], None)   # a retried confirm adds nothing
    assert await ledger.on_hand(SHOP, CURD) == start + 3.0

    # 3. "Check the bill": the invoice matches the order and the count
    agreed = (await _repo.get_order(SHOP, oid))["items"][0]["agreedPrice"]

    async def invoice(frames):
        assert frames
        return InvoiceRead(supplier="Sakthi", date=None, agreed=True, disagreements=[], total=3 * agreed,
                           lines=[InvoiceLine(name=product["name"], qty=3, unit="L", unit_price=agreed,
                                              line_total=3 * agreed)])

    monkeypatch.setattr(vinv, "read_invoice", invoice)
    phone.queue("invoice", "i1", "i2")
    use_call("c6")
    assert (await delivery.invoice_check(oid))["state"] == "DELIVERY_RECONCILED"

    # 4. "Pay the vendor in cash": the note plan, the notes checked, read-back, yes
    monkeypatch.setattr(pay, "CASH_PREP_S", 0)

    async def cash(frames):
        assert frames
        return CashRead(notes=[{"denomination": 3 * agreed, "count": 1}], total=3 * agreed, agreed=True,
                        confidence=0.9)

    monkeypatch.setattr(vcash, "read_cash", cash)
    phone.queue("cash", "k1", "k2")
    use_call("c7")
    r = await pay.supplier_pay(oid, "cash")
    assert r["needs_confirm"] is True and proposals[-1]["committer"] == "supplier_pay.cash"
    assert proposals[-1]["payload"]["amount"] == 3 * agreed
    use_call("c8")
    await pay._commit_supplier_cash(proposals[-1]["payload"], None)
    assert (await _repo.get_order(SHOP, oid))["state"] == "PAYMENT_CONFIRMED"

    # 5. A customer buys 2 L curd and pays by UPI; the soundbox announces the exact total
    use_call("c9")
    sale = await sale_tools.sale_start([{"product": "curd", "qty": 2, "unit": "L"}])
    assert sale["total"] == 2 * int(product["price"])
    assert await ledger.on_hand(SHOP, CURD) == start + 3.0    # nothing leaves stock before the sale completes
    listeners = []

    def subscribe(session_id, cb):
        listeners.append(cb)
        return lambda: listeners.remove(cb)

    monkeypatch.setattr(pay, "_subscribe", subscribe)
    use_call("c10")
    task = asyncio.create_task(pay.sale_pay(sale["saleId"], "upi"))
    while not listeners:
        await asyncio.sleep(0)
    await listeners[0](f"Received rupees {sale['total'] * 10} on PhonePe")   # wrong amount: warn, keep listening
    assert not task.done() and notified[-1][2] == "mismatch"
    await listeners[0](f"Received rupees {sale['total']} on PhonePe")
    r = await task
    assert r["needs_confirm"] is True and proposals[-1]["committer"] == "sale.complete"
    assert proposals[-1]["payload"]["evidence"]["type"] == "soundbox"
    use_call("c11")
    done = await pay._commit_sale(proposals[-1]["payload"], None)
    again = await pay._commit_sale(proposals[-1]["payload"], None)   # a retried confirm completes nothing twice

    # 6. Stock moved exactly by the verified delivery and the sale; analytics saw every order state once
    left = await ledger.on_hand(SHOP, CURD)
    assert left == start + 3.0 - 2.0
    assert again["ok"] is True
    assert [p["state"] for topic, p in published if topic == "orders"] == [
        "ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED", "PAYMENT_PENDING", "PAYMENT_CONFIRMED"]
    assert done["say"].startswith("Sale complete.")
    assert ("Order more?" in done["say"]) == (left <= float(product["reorderThreshold"]))

    # 7. "How much curd is left?" says the ledger value
    use_call("c12")
    q = await stock.stock_query("sakthi curd")
    assert q["sku"] == CURD and q["qty"] == left
