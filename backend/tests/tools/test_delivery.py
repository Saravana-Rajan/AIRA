"""Delivery tools (kanish/02 Task 4): delivery_start, delivery_count (one by one / crate), invoice_check and the
committer "delivery.record_count".

The engine (catalog, ledger, states, reconcile, outbox) runs for real on a MemoryStore seeded from the real Sam
Dairy catalog, with the foundation's confirm/session/ids. Only the vision calls (Gemini) and the analytics
publisher are faked. The phone is a fake that answers request_frame messages with queued frames.
"""
import asyncio
from pathlib import Path

import pytest

from aira_live import confirm
from aira_live.business import catalog, hints, ledger, outbox, paths, seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.contracts import CountMsg, RequestFrameMsg
from aira_live.frames import Frame
from aira_live.session import Session, current_session, current_tool_call_id
from aira_live.tools import _repo, delivery
from aira_live.tools.delivery import OnePassCounter
from aira_live.vision import count as vcount
from aira_live.vision import identify as vid
from aira_live.vision import invoice as vinv
from aira_live.vision.count import CountResult
from aira_live.vision.identify import IdentifyResult
from aira_live.vision.invoice import InvoiceLine, InvoiceRead

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "shared" / "catalog" / "sunrise_dairy.json"
SHOP = "sunrise_dairy"
CURD = "sakthi_curd_1l"
GHEE = "aavin_ghee_500ml"


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
    """The phone's WebSocket: records server messages and answers request_frame with queued frames."""

    def __init__(self):
        self.msgs: list = []
        self.session: Session | None = None
        self.queued: dict[str, list[str]] = {}
        self.streams: dict[str, list[tuple[str, float]]] = {}
        self._tasks: list[asyncio.Task] = []
        self._ts = 10_000.0

    async def __call__(self, msg) -> None:
        self.msgs.append(msg)
        if not isinstance(msg, RequestFrameMsg) or msg.stop:
            return
        if msg.continuous and msg.purpose in self.streams:
            self._tasks.append(asyncio.create_task(self._feed(msg.purpose, self.streams.pop(msg.purpose))))
        elif msg.purpose in self.queued:
            for fid in self.queued.pop(msg.purpose):
                self._ts += 700
                self.session.frames.add(Frame(fid, "", 640, 480, self._ts, msg.purpose))

    async def _feed(self, purpose: str, frames: list[tuple[str, float]]) -> None:
        for fid, ts in frames:
            await asyncio.sleep(0.01)
            self.session.frames.add(Frame(fid, "", 640, 480, ts, purpose))

    def of(self, kind: type) -> list:
        return [m for m in self.msgs if isinstance(m, kind)]


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


@pytest.fixture
def proposals(monkeypatch):
    made: list[dict] = []
    real = confirm.propose
    previous = confirm._store
    confirm.set_store(confirm.MemoryProposalStore())

    async def recording(kind, prompt, payload, committer, facts=None):
        out = await real(kind, prompt, payload, committer, facts=facts)
        made.append({"kind": kind, "prompt": prompt, "payload": payload, "committer": committer, "facts": facts})
        return out

    monkeypatch.setattr(confirm, "propose", recording)
    yield made
    confirm.set_store(previous)


# ---------------------------------------------------------------------------------------------------- helpers

async def _placed(store, sku=CURD, qty=3.0, price=50) -> dict:
    p = await store.get(paths.product(SHOP, sku))
    order = {"id": "o1", "supplierId": "sakthi_vendor", "supplierName": "Sakthi curd vendor", "state": "ORDER_PLACED",
             "lang": "en", "idemKey": "k-o1", "updatedAt": "2026-10-12T09:00:00",
             "items": [{"sku": sku, "name": p["name"], "qty": qty, "unit": p["unit"], "agreedPrice": price}]}
    await _repo.save_order(SHOP, order)
    return order


def _identify_by_frame_id(mapping: dict):
    async def fake(frame, candidates):
        assert any(c.sku == CURD for c in candidates)
        return IdentifyResult(sku=mapping.get(frame.id), confidence=0.9, alternatives=[])
    return fake


async def _count_three(phone, monkeypatch, frames=None):
    monkeypatch.setattr(delivery, "COUNT_IDLE_S", 0.2)
    monkeypatch.setattr(vid, "identify_product", _identify_by_frame_id({"p1": CURD, "p2": CURD, "p3": CURD}))
    phone.streams["identify"] = frames or [("p1", 1000), ("g1", 1800), ("p2", 2600), ("g2", 3400), ("p3", 4200)]
    return await delivery.delivery_count(CURD)


# ---------------------------------------------------------------------------------------------------- counter

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


# ---------------------------------------------------------------------------------------------------- tools

async def test_delivery_start_moves_to_delivery_pending(store, session, published):
    await _placed(store)
    r = await delivery.delivery_start()
    assert (await _repo.get_order(SHOP, "o1"))["state"] == "DELIVERY_PENDING"
    d = await _repo.get_delivery(SHOP, r["deliveryId"])
    assert (d["items"][0]["sku"], d["items"][0]["expectedQty"], d["items"][0]["countedQty"]) == (CURD, 3.0, None)
    assert ("orders", {"shop_id": SHOP, "order_id": "o1", "state": "DELIVERY_PENDING"}) in published
    assert r["supplier"] == "Sakthi curd vendor"
    assert r["say_en"] == "Delivery from Sakthi curd vendor. Show each pack to the camera, one at a time."


async def test_delivery_start_without_order_says_none(store, session):
    r = await delivery.delivery_start()
    assert r["ok"] is False and r["error"] == "NO_OPEN_ORDER" and r["say_en"]


async def test_delivery_count_without_delivery_says_none(store, session):
    r = await delivery.delivery_count(CURD)
    assert r["ok"] is False and r["error"] == "NO_DELIVERY" and r["say_en"]


async def test_delivery_count_one_by_one_streams_and_proposes(store, session, phone, proposals, monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    await _count_three(phone, monkeypatch)
    counts = phone.of(CountMsg)
    assert [m.counted for m in counts] == [1, 2, 3, 3] and counts[-1].final is True
    assert any(m.stop for m in phone.of(RequestFrameMsg))
    p = proposals[0]
    assert (p["kind"], p["committer"], p["payload"]["qty"]) == ("generic", "delivery.record_count", 3.0)
    assert (p["facts"]["product"], p["facts"]["counted"], p["facts"]["expected"]) == ("Sakthi Curd 1 L", 3, 3)


async def test_delivery_count_resolves_spoken_name(store, session, phone, proposals, monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    monkeypatch.setattr(delivery, "COUNT_IDLE_S", 0.2)
    monkeypatch.setattr(vid, "identify_product", _identify_by_frame_id({"p1": CURD}))
    phone.streams["identify"] = [("p1", 1000)]
    await delivery.delivery_count("sakthi curd")
    assert proposals[0]["payload"]["sku"] == CURD and proposals[0]["kind"] == "discrepancy"


async def test_delivery_count_nothing_seen_does_not_propose(store, session, phone, proposals, monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    r = await _count_three(phone, monkeypatch, frames=[("g1", 1000)])
    assert r["ok"] is False and r["error"] == "NOTHING_SEEN" and r["product"] == "Sakthi Curd 1 L"
    assert proposals == []


async def test_delivery_commit_is_idempotent(store, session, phone, proposals, monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    await _count_three(phone, monkeypatch)
    before = await ledger.on_hand(SHOP, CURD)
    payload = proposals[0]["payload"]
    await delivery._commit_count(payload, None)
    await delivery._commit_count(payload, None)                 # retried confirm
    assert await ledger.on_hand(SHOP, CURD) == before + 3.0
    d = await _repo.get_delivery(SHOP, payload["delivery_id"])
    assert d["items"][0]["countedQty"] == 3.0


async def test_short_count_proposes_discrepancy_and_publishes(store, session, phone, proposals, published,
                                                             monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    await _count_three(phone, monkeypatch, frames=[("p1", 1000), ("g1", 1800), ("p2", 2600)])
    p = proposals[0]
    assert (p["kind"], p["payload"]["qty"]) == ("discrepancy", 2.0)
    await delivery._commit_count(p["payload"], None)
    assert ("discrepancies", {"shop_id": SHOP, "delivery_id": p["payload"]["delivery_id"],
                              "field": f"SHORT_DELIVERY:{CURD}", "expected": 3.0, "actual": 2.0}) in published


async def test_delivery_count_crate_retake_speaks_aim_hint(store, session, phone, proposals, monkeypatch):
    await _placed(store, sku=GHEE, qty=6.0, price=260)
    await delivery.delivery_start()
    phone.queued["count"] = ["c1", "c2"]

    async def blurry(frame, label):
        return CountResult(count=None, confidence=0.3, needs_retake=True, aim_hint="vision.aim.move_back")

    monkeypatch.setattr(vcount, "count_in_frame", blurry)
    r = await delivery.delivery_count(GHEE, mode="crate")
    assert r["needs_retake"] is True and r["aim_hint"] == "vision.aim.move_back" and r["say_en"]
    assert proposals == []


async def test_delivery_count_crate_disagreeing_reads_retake(store, session, phone, proposals, monkeypatch):
    await _placed(store, sku=GHEE, qty=6.0, price=260)
    await delivery.delivery_start()
    phone.queued["count"] = ["c1", "c2"]
    reads = iter([6, 5])

    async def flaky(frame, label):
        return CountResult(count=next(reads), confidence=0.9, needs_retake=False, aim_hint="")

    monkeypatch.setattr(vcount, "count_in_frame", flaky)
    r = await delivery.delivery_count(GHEE, mode="crate")
    assert r["needs_retake"] is True and proposals == []


async def test_delivery_count_crate_agreeing_reads_propose(store, session, phone, proposals, monkeypatch):
    await _placed(store, sku=GHEE, qty=6.0, price=260)
    await delivery.delivery_start()
    phone.queued["count"] = ["c1", "c2"]

    async def six(frame, label):
        return CountResult(count=6, confidence=0.9, needs_retake=False, aim_hint="")

    monkeypatch.setattr(vcount, "count_in_frame", six)
    await delivery.delivery_count(GHEE, mode="crate")
    product = {**(await store.get(paths.product(SHOP, GHEE))), "sku": GHEE}
    assert proposals[0]["payload"]["qty"] == catalog.to_selling_units(product, 6, "pack")[0]
    assert proposals[0]["kind"] == "generic"


# ---------------------------------------------------------------------------------------------------- invoice

async def _counted_delivery(store, counted=3.0):
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


async def test_invoice_check_match_reconciles(store, session, phone, published, monkeypatch):
    await _counted_delivery(store)
    phone.queued["invoice"] = ["i1", "i2"]
    monkeypatch.setattr(vinv, "read_invoice", _invoice(3, 150))
    r = await delivery.invoice_check("o1")
    assert r["state"] == "DELIVERY_RECONCILED" and r["match"] is True and r["total"] == 150
    assert r["say_en"] == "The bill matches. Total 150 rupees."
    assert ("orders", {"shop_id": SHOP, "order_id": "o1", "state": "DELIVERY_RECONCILED"}) in published


async def test_invoice_check_mismatch_marks_discrepancy(store, session, phone, published, monkeypatch):
    await _counted_delivery(store)
    phone.queued["invoice"] = ["i1", "i2"]
    monkeypatch.setattr(vinv, "read_invoice", _invoice(4, 200))
    r = await delivery.invoice_check("o1")
    name = (await store.get(paths.product(SHOP, CURD)))["name"]
    assert r["state"] == "DISCREPANCY" and r["match"] is False
    first = r["discrepancies"][0]
    assert (first["kind"], first["product"], first["expected"], first["actual"]) == ("INVOICE_QTY_MISMATCH", name, 3, 4)
    assert r["say_en"].startswith(hints.english("INVOICE_QTY_MISMATCH", product=name, expected="3", actual="4"))
    assert any(topic == "discrepancies" and p["field"] == f"INVOICE_QTY_MISMATCH:{CURD}" for topic, p in published)


async def test_invoice_unreadable_asks_retake(store, session, phone, monkeypatch):
    await _counted_delivery(store)
    phone.queued["invoice"] = ["i1", "i2"]
    monkeypatch.setattr(vinv, "read_invoice", _invoice(3, 150, agreed=False))
    r = await delivery.invoice_check("o1")
    assert r["needs_retake"] is True and r["error"] == "INVOICE_UNREADABLE" and r["say_en"]
    assert (await _repo.get_order(SHOP, "o1"))["state"] == "DELIVERY_PENDING"


# ---------------------------------------------------------------------------------------------------- judge and video fixes

async def _count_packs(phone, monkeypatch, n: int, call_id: str):
    """One one-by-one count of n packs, as its own Live tool call (so its own idempotency key)."""
    current_tool_call_id.set(call_id)
    monkeypatch.setattr(delivery, "COUNT_IDLE_S", 0.2)
    ids = {f"p{i}": CURD for i in range(1, n + 1)}
    monkeypatch.setattr(vid, "identify_product", _identify_by_frame_id(ids))
    t0 = 100_000 * int(call_id.rsplit("-", 1)[-1])            # each count's frames are newer than the last count's
    frames = []
    for i in range(1, n + 1):
        frames += [(f"p{i}", t0 + 1600 * i), (f"g{i}", t0 + 800 + 1600 * i)]
    phone.streams["identify"] = frames
    return await delivery.delivery_count(CURD)


async def test_a_recount_corrects_stock_instead_of_adding_it_again(store, session, phone, proposals, monkeypatch):
    """The video's "Count again": two confirmed counts must leave stock at the second count, not the sum."""
    await _placed(store)
    await delivery.delivery_start()
    before = await ledger.on_hand(SHOP, CURD)
    await _count_packs(phone, monkeypatch, 3, "tc-count-1")
    await delivery._commit_count(proposals[-1]["payload"], None)
    await _count_packs(phone, monkeypatch, 2, "tc-count-2")
    await delivery._commit_count(proposals[-1]["payload"], None)
    assert await ledger.on_hand(SHOP, CURD) == before + 2.0
    d = await _repo.get_delivery(SHOP, proposals[-1]["payload"]["delivery_id"])
    assert d["items"][0]["countedQty"] == 2.0


async def test_a_short_count_names_the_shortfall_and_offers_a_recount(store, session, phone, proposals, monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    r = await _count_packs(phone, monkeypatch, 2, "tc-count-1")
    assert proposals[-1]["prompt"] == ("I counted 2 packs of Sakthi Curd 1 L. The order was 3. "
                                       "1 may be missing. Save 2, or shall I count again?")
    assert r["needs_confirm"] is True and r["say_en"] == proposals[-1]["prompt"]
    assert (r["product"], r["counted"], r["expected"], r["missing"]) == ("Sakthi Curd 1 L", 2, 3, 1)


async def test_the_same_number_again_says_still(store, session, phone, proposals, monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    await _count_packs(phone, monkeypatch, 2, "tc-count-1")
    await delivery._commit_count(proposals[-1]["payload"], None)
    r = await _count_packs(phone, monkeypatch, 2, "tc-count-2")
    assert proposals[-1]["prompt"].startswith("Still 2. ") and r["still"] is True and r["counted"] == 2


async def test_a_confirmed_short_count_marks_the_order_and_lets_the_vendor_be_paid(store, session, phone, proposals,
                                                                                  monkeypatch):
    from aira_live.tools import payments
    await _placed(store)
    await delivery.delivery_start()
    await _count_packs(phone, monkeypatch, 2, "tc-count-1")
    r = await delivery._commit_count(proposals[-1]["payload"], None)
    assert (r["ok"], r["counted"], r["expected"], r["missing"]) == (True, 2, 3, 1)
    assert r["say_en"] == "Recorded: 2 packs received, 1 short."
    assert (await _repo.get_order(SHOP, "o1"))["state"] == "DISCREPANCY"
    d = await _repo.get_delivery(SHOP, proposals[-1]["payload"]["delivery_id"])
    assert [(x["kind"], x["expected"], x["actual"]) for x in d["discrepancies"]] == [("SHORT_DELIVERY", 3.0, 2.0)]
    assert (await _repo.latest_order(SHOP, payments.PAYABLE_STATES))["id"] == "o1"


async def test_a_full_count_marks_the_delivery_checked(store, session, phone, proposals, monkeypatch):
    await _placed(store)
    await delivery.delivery_start()
    await _count_packs(phone, monkeypatch, 3, "tc-count-1")
    r = await delivery._commit_count(proposals[-1]["payload"], None)
    assert (r["ok"], r["product"], r["counted"], r["expected"]) == (True, "Sakthi Curd 1 L", 3, 3)
    assert "missing" not in r and r["say_en"] == "Recorded: 3 packs of Sakthi Curd 1 L received."
    assert (await _repo.get_order(SHOP, "o1"))["state"] == "DELIVERY_RECONCILED"


async def test_a_wrong_bill_can_be_checked_after_a_correct_one(store, session, phone, proposals, monkeypatch):
    """Judge scenes 4 then 5: the second bill used to get "No open order found"."""
    await _placed(store)
    await delivery.delivery_start()
    await _count_packs(phone, monkeypatch, 3, "tc-count-1")
    await delivery._commit_count(proposals[-1]["payload"], None)
    phone.queued["invoice"] = ["i1", "i2"]
    monkeypatch.setattr(vinv, "read_invoice", _invoice(3, 150))
    ok = await delivery.invoice_check("")
    assert ok["state"] == "DELIVERY_RECONCILED"
    phone.queued["invoice"] = ["i3", "i4"]
    monkeypatch.setattr(vinv, "read_invoice", _invoice(4, 200))
    wrong = await delivery.invoice_check("")
    assert wrong["state"] == "DISCREPANCY"


async def test_a_wrong_bill_says_its_total_and_what_it_should_be(store, session, phone, monkeypatch):
    """The video: "The bill says 30 litres, ₹1,050. Only 28 arrived. The bill should be ₹980." """
    await _counted_delivery(store, counted=2.0)
    phone.queued["invoice"] = ["i1", "i2"]
    monkeypatch.setattr(vinv, "read_invoice", _invoice(3, 150))
    r = await delivery.invoice_check("o1")
    assert (r["total"], r["payable"]) == (150, 100)
    assert r["say_en"].endswith("The bill total is 150 rupees. It should be 100 rupees.")


def test_one_blurry_frame_while_holding_a_pack_does_not_count_it_twice():
    """Bug sweep: the phone streams 2 frames a second; one glare frame on a held pack made AIRA say "2"."""
    c = OnePassCounter(CURD)
    for ts, conf in [(0, 0.9), (500, 0.9), (1000, 0.9), (1500, 0.65), (2000, 0.9), (2500, 0.9)]:
        c.observe(ts, CURD, conf)
    assert c.counted == 1
    c2 = OnePassCounter(CURD)
    for ts, sku in [(0, CURD), (500, CURD), (1000, None), (1500, CURD), (2000, CURD)]:
        c2.observe(ts, sku, 0.9)
    assert c2.counted == 1


async def test_counting_carries_on_past_the_ordered_number(store, session, phone, proposals, monkeypatch):
    """Bug sweep: 5 packs arrived against 3 ordered, counting stopped at 3 and 2 packs never entered stock."""
    await _placed(store)                                        # 3 L of 1 L packs ordered
    await delivery.delivery_start()
    await _count_packs(phone, monkeypatch, 5, "tc-count-1")
    assert proposals[-1]["payload"]["packs"] == 5


async def test_a_new_order_is_never_counted_against_an_old_paid_delivery(store, session, phone, proposals, monkeypatch):
    await _placed(store)
    first = await delivery.delivery_start()
    await _count_packs(phone, monkeypatch, 3, "tc-count-1")
    await delivery._commit_count(proposals[-1]["payload"], None)
    paid = await _repo.get_order(SHOP, "o1")
    await _repo.save_order(SHOP, {**paid, "state": "PAYMENT_CONFIRMED"})
    p = await store.get(paths.product(SHOP, CURD))
    await _repo.save_order(SHOP, {"id": "o2", "supplierId": "sakthi_vendor", "supplierName": "Sakthi curd vendor",
                                  "state": "ORDER_PLACED", "lang": "en", "idemKey": "k-o2", "updatedAt": "2026-10-13T09:00:00",
                                  "items": [{"sku": CURD, "name": p["name"], "qty": 6.0, "unit": "L", "agreedPrice": 50}]})
    r = await _count_packs(phone, monkeypatch, 6, "tc-count-2")              # Gemini skipped delivery_start
    assert r["ok"] is False and r["error"] == "NO_DELIVERY"
    old = await _repo.get_delivery(SHOP, first["deliveryId"])
    assert old["items"][0]["countedQty"] == 3.0                               # the paid delivery is untouched
