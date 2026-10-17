import pytest

from aira_live.business import outbox, paths, states, store
from aira_live.business._ids import doc_id_for
from aira_live.business.states import InvalidTransition, transition
from aira_live.business.store import MemoryStore
from aira_live.business.text import normalize_digits, tokens


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
    assert states.ORDER_STATES == ["DRAFT", "ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED",
                                   "DISCREPANCY", "PAYMENT_PENDING", "PAYMENT_CONFIRMED", "CLOSED"]
    assert states.SALE_STATES == ["SALE_PENDING", "SALE_COMPLETED", "SALE_VOID"]
    assert states.PAYMENT_STATES == ["PENDING", "CONFIRMED", "FAILED"]


def test_unknown_entity_raises():
    with pytest.raises(InvalidTransition):
        transition("refund", "A", "B")
    assert states.allowed("refund", "A") == set()
    assert states.allowed("sale", "SALE_PENDING") == {"SALE_COMPLETED", "SALE_VOID"}


async def test_memory_store_txn_atomic_on_error():
    s = MemoryStore()
    await s.set("shops/x/inventory/a", {"onHand": 1.0})

    async def boom(txn):
        txn.set("shops/x/inventory/a", {"onHand": 99.0})
        raise RuntimeError("fail mid-transaction")

    with pytest.raises(RuntimeError):
        await s.run(boom)
    assert (await s.get("shops/x/inventory/a")) == {"onHand": 1.0}


async def test_memory_store_txn_reads_own_writes_and_commits():
    s = MemoryStore()

    async def bump(txn):
        txn.set("shops/x/inventory/a", {"onHand": 3.0})
        doc = await txn.get("shops/x/inventory/a")
        return doc["onHand"]

    assert await s.run(bump) == 3.0
    assert (await s.get("shops/x/inventory/a")) == {"onHand": 3.0}
    assert await s.get("shops/x/inventory/missing") is None


async def test_memory_store_list_children_only():
    s = MemoryStore()
    await s.set("shops/x/products/a", {"n": 1})
    await s.set("shops/x/products/b", {"n": 2})
    await s.set("shops/x/products/b/sub/c", {"n": 3})
    await s.set("shops/y/products/z", {"n": 4})
    assert await s.list("shops/x/products") == [("a", {"n": 1}), ("b", {"n": 2})]


def test_get_store_returns_injected_store():
    mem = MemoryStore()
    try:
        store.set_store(mem)
        assert store.get_store() is mem
    finally:
        store.set_store(None)


def test_doc_id_for_deterministic():
    assert doc_id_for("s", "sess1:call9") == doc_id_for("s", "sess1:call9")
    assert doc_id_for("s", "sess1:call9") != doc_id_for("s", "sess1:call10")
    assert doc_id_for("s", "k").startswith("s_") and len(doc_id_for("s", "k")) == 18


def test_paths_and_text_helpers():
    assert paths.inventory("sunrise_dairy", "aavin_milk_1l") == "shops/sunrise_dairy/inventory/aavin_milk_1l"
    assert paths.product("sunrise_dairy", "sakthi_curd_1l") == "shops/sunrise_dairy/products/sakthi_curd_1l"
    assert paths.supplier("sunrise_dairy", "aavin_vendor") == "shops/sunrise_dairy/suppliers/aavin_vendor"
    assert paths.audit("sunrise_dairy", "a1") == "shops/sunrise_dairy/auditLogs/a1"
    assert paths.idem("k").startswith("idempotency/") and len(paths.idem("k")) == len("idempotency/") + 40
    assert normalize_digits("௨ और ५") == "2 और 5"
    assert tokens("சக்தி தயிர், ௨ லிட்டர்") == ["சக்தி", "தயிர்", "2", "லிட்டர்"]
    assert tokens("₹60 received!") == ["₹", "60", "received"]


async def test_outbox_payloads_and_failure_is_swallowed():
    sent: list[tuple[str, dict]] = []

    async def fake(topic: str, payload: dict) -> str:
        sent.append((topic, payload))
        return "fake"

    async def broken(topic: str, payload: dict) -> str:
        raise RuntimeError("publisher down")

    class _Result:
        discrepancies = [{"kind": "SHORT", "sku": "sakthi_curd_1l", "expected": 30, "actual": 28},
                         {"kind": "INVOICE_UNREADABLE"}]

    try:
        outbox.set_publisher(fake)
        await outbox.inventory_movement("sunrise_dairy", "t1", "sakthi_curd_1l", -2, "SALE")
        await outbox.sale_lines("sunrise_dairy", "s1", [{"sku": "sakthi_curd_1l", "qty": 2, "unitPrice": 60}])
        await outbox.order_state("sunrise_dairy", "o1", "ORDER_PLACED")
        await outbox.discrepancies("sunrise_dairy", "d1", _Result())
        assert sent[0] == ("inventory-movements", {"shop_id": "sunrise_dairy", "txn_id": "t1",
                                                   "sku": "sakthi_curd_1l", "delta": -2.0, "reason": "SALE"})
        assert sent[1][0] == "sales" and sent[1][1]["unit_price"] == 60 and sent[1][1]["qty"] == 2.0
        assert sent[2] == ("orders", {"shop_id": "sunrise_dairy", "order_id": "o1", "state": "ORDER_PLACED"})
        assert sent[3] == ("discrepancies", {"shop_id": "sunrise_dairy", "delivery_id": "d1",
                                             "field": "SHORT:sakthi_curd_1l", "expected": 30, "actual": 28})
        assert sent[4][1]["field"] == "INVOICE_UNREADABLE" and sent[4][1]["expected"] is None
        outbox.set_publisher(broken)
        await outbox.order_state("sunrise_dairy", "o1", "CLOSED")   # logged, never raised
    finally:
        outbox.set_publisher(None)
