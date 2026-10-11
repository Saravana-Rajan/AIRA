"""confirm: the model proposes, the backend commits (exactly once)."""
import asyncio

import pytest

from aira_live import confirm
from aira_live.session import Session, current_session


@pytest.fixture
def session():
    sent = []

    async def send(m):
        sent.append(m)

    s = Session(uid="u1", shop_id="sunrise_dairy", lang="en", verbosity="short", send=send)
    s.sent = sent
    token = current_session.set(s)
    confirm.set_store(confirm.MemoryProposalStore())
    yield s
    current_session.reset(token)


async def test_propose_sends_confirm_and_resolve_commits_once(session):
    calls = []

    async def commit(payload, value):
        calls.append(payload)
        return {"say": "Order sent.", "orderId": "o1"}

    confirm.register_committer("test.order", commit)
    p = await confirm.propose("order", "Order 30 L Sakthi curd?", {"sku": "sakthi_curd_1l", "qty": 30}, "test.order")
    assert p["needs_confirm"] is True and p["say"] == "Order 30 L Sakthi curd?"
    assert session.sent[-1].t == "confirm" and session.sent[-1].kind == "order"
    assert session.sent[-1].id == p["confirm_id"]
    r1 = await confirm.resolve(p["confirm_id"], "yes", None)
    r2 = await confirm.resolve(p["confirm_id"], "yes", None)
    assert r1["status"] == "committed" and r1["orderId"] == "o1"
    assert r2["status"] == "committed" and r2["result"]["orderId"] == "o1"
    assert r2["say"] == "That was already done."
    assert len(calls) == 1


async def test_concurrent_resolves_commit_exactly_once(session):
    calls = []

    async def commit(payload, value):
        calls.append(value)
        await asyncio.sleep(0.02)
        return {"say": "Paid."}

    confirm.register_committer("test.pay", commit)
    p = await confirm.propose("payment", "Pay 1000?", {"amount": 1000}, "test.pay")
    results = await asyncio.gather(*(confirm.resolve(p["confirm_id"], "yes", "1000") for _ in range(3)))
    assert len(calls) == 1 and calls[0] == "1000"
    assert all(r["status"] == "committed" for r in results)


async def test_proposal_is_stored_pending_then_committed(session):
    store = confirm.MemoryProposalStore()
    confirm.set_store(store)

    async def commit(payload, value):
        return {"say": "Done.", "saleId": "s1"}

    confirm.register_committer("test.sale", commit)
    p = await confirm.propose("sale", "Complete the sale?", {"saleId": "s1"}, "test.sale")
    doc = await store.get("sunrise_dairy", p["confirm_id"])
    assert doc["status"] == "pending" and doc["committer"] == "test.sale" and doc["kind"] == "sale"
    assert doc["payload"] == {"saleId": "s1"} and doc["sessionId"] == session.session_id
    await confirm.resolve(p["confirm_id"], "yes")
    doc = await store.get("sunrise_dairy", p["confirm_id"])
    assert doc["status"] == "committed" and doc["result"]["saleId"] == "s1"


async def test_no_cancels_and_unknown_id(session):
    async def commit(payload, value):
        raise AssertionError("must not commit")

    confirm.register_committer("test.never", commit)
    p = await confirm.propose("payment", "Pay 1000?", {}, "test.never")
    r = await confirm.resolve(p["confirm_id"], "no", None)
    assert r["status"] == "rejected" and r["say"] == "Okay, cancelled."
    again = await confirm.resolve(p["confirm_id"], "yes", None)
    assert again["status"] == "rejected"
    u = await confirm.resolve("missing", "yes", None)
    assert u["error"] == "unknown_confirm"


async def test_failed_commit_stays_pending_and_can_be_retried(session):
    attempts = []

    async def commit(payload, value):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("network")
        return {"say": "Sent."}

    confirm.register_committer("test.flaky", commit)
    p = await confirm.propose("order", "Send order?", {}, "test.flaky")
    with pytest.raises(RuntimeError):
        await confirm.resolve(p["confirm_id"], "yes")
    r = await confirm.resolve(p["confirm_id"], "yes")
    assert r["status"] == "committed" and len(attempts) == 2


async def test_unknown_kind_maps_to_generic(session):
    async def commit(payload, value):
        return {"say": "ok"}

    confirm.register_committer("test.x", commit)
    await confirm.propose("weird", "Proceed?", {}, "test.x")
    assert session.sent[-1].kind == "generic"
