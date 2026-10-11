"""Confirm before commit (saravana/01 Task 2): proposals bound to their session, a TTL, exactly-once commits.

The Firestore path runs against the in-memory ``fake_db`` (tests/conftest.py); nothing touches the network.
"""
import asyncio
import contextlib
import inspect
import time
import uuid
from types import SimpleNamespace

import pytest

from aira_live import confirm
from aira_live.i18n import t
from aira_live.session import current_session
from aira_live.tool_runtime import wrap_tool
from aira_live.tools import TOOL_FUNCS, TOOLS_BY_NAME
from aira_live.tools.core import confirm_action

SHOP = "sunrise_dairy"
PROMPT = "Order 30 L Sakthi curd from Sakthi vendor?"
SENT = "Order sent to Sakthi vendor."


@pytest.fixture
def fs(fake_db):
    """Proposals go to the Firestore store (backed by the in-memory fake); the previous store comes back after."""
    previous = confirm._store
    confirm.set_store(confirm.FirestoreProposalStore())
    yield fake_db
    confirm.set_store(previous)


@contextlib.contextmanager
def acting_as(session):
    token = current_session.set(session)
    try:
        yield session
    finally:
        current_session.reset(token)


async def _propose(kind: str = "order"):
    calls: list = []

    async def commit(payload: dict, value: str | None) -> dict:
        await asyncio.sleep(0.01)  # a real committer awaits the network; lets a second "yes" race it
        calls.append((payload, value))
        return {"ok": True, "say": SENT, "orderId": "o1"}

    name = f"test.flow.{uuid.uuid4().hex[:8]}"
    confirm.register_committer(name, commit)
    p = await confirm.propose(kind, PROMPT, {"qty": 30}, name)
    return p, calls


def _doc(fake, confirm_id: str) -> dict:
    return fake.store[f"shops/{SHOP}/proposals/{confirm_id}"]


# ---------- propose ----------
async def test_propose_stores_a_pending_proposal_bound_to_the_session(fs, mk_session):
    s, rec = mk_session()
    with acting_as(s):
        before = time.time()
        p, calls = await _propose()

    assert p == {"needs_confirm": True, "confirm_id": p["confirm_id"], "say": PROMPT}
    assert p["confirm_id"].startswith("cf_")
    msg = rec.msgs[-1]
    assert (msg.t, msg.id, msg.kind, msg.prompt) == ("confirm", p["confirm_id"], "order", PROMPT)
    doc = _doc(fs, p["confirm_id"])
    assert doc["status"] == "pending" and doc["kind"] == "order" and doc["payload"] == {"qty": 30}
    assert doc["sessionId"] == s.session_id and doc["uid"] == "u1"
    assert before <= doc["createdAt"] <= time.time()
    assert calls == []


async def test_propose_refuses_an_unregistered_committer(fs, mk_session):
    s, rec = mk_session()
    with acting_as(s), pytest.raises(KeyError):
        await confirm.propose("order", "Order?", {}, "never.registered")
    assert rec.msgs == [] and fs.store == {}


# ---------- resolve: exactly once ----------
async def test_voice_and_screen_yes_at_once_commit_exactly_once(fs, mk_session):
    s, _ = mk_session()
    with acting_as(s):
        p, calls = await _propose()
        r1, r2 = await asyncio.gather(  # voice confirm_action + on-screen confirm at the same moment
            confirm_action(p["confirm_id"], "yes"),
            confirm.resolve(p["confirm_id"], "yes", None),
        )
        r3 = await confirm_action(p["confirm_id"], "yes")  # the model retries the tool call later

    assert calls == [({"qty": 30}, None)]
    assert r1 == {"ok": True, "say": SENT, "orderId": "o1", "status": "committed"}
    already = {"ok": True, "say": t("confirm.already", "en"), "status": "committed",
               "result": {"ok": True, "say": SENT, "orderId": "o1"}}
    assert r2 == r3 == already  # a repeat never sounds like a second order
    doc = _doc(fs, p["confirm_id"])
    assert doc["status"] == "committed" and doc["result"] == already["result"] and doc["resolvedAt"] >= doc["createdAt"]


async def test_screen_value_reaches_the_committer(fs, mk_session):
    s, _ = mk_session()
    with acting_as(s):
        p, calls = await _propose()
        await confirm.resolve(p["confirm_id"], "yes", "28")
    assert calls == [({"qty": 30}, "28")]


async def test_no_cancels_and_a_later_yes_never_commits(fs, mk_session):
    s, _ = mk_session(lang="ta")
    with acting_as(s):
        p, calls = await _propose()
        r = await confirm.resolve(p["confirm_id"], "no", None)
        later = await confirm_action(p["confirm_id"], "yes")

    assert r == {"ok": False, "say": t("confirm.cancelled", "ta"), "status": "rejected"}
    assert later == r
    assert calls == [] and _doc(fs, p["confirm_id"])["status"] == "rejected"


async def test_only_an_explicit_yes_commits(fs, mk_session):
    s, _ = mk_session()
    with acting_as(s):
        p, calls = await _propose()
        r = await confirm.resolve(p["confirm_id"], "maybe", None)  # type: ignore[arg-type]
    assert r["status"] == "rejected" and r["ok"] is False and calls == []


# ---------- resolve: bound to the session ----------
async def test_another_session_of_the_same_shop_cannot_resolve(fs, mk_session):
    owner, _ = mk_session()
    other, _ = mk_session()  # e.g. a second judge phone, also in the demo shop
    with acting_as(owner):
        p, calls = await _propose()
    with acting_as(other):
        yes = await confirm_action(p["confirm_id"], "yes")
        no = await confirm.resolve(p["confirm_id"], "no", None)

    unknown = {"ok": False, "say": t("confirm.unknown", "en"), "status": "unknown", "error": "unknown_confirm"}
    assert yes == no == unknown  # same answer as a missing id: nothing about the proposal leaks
    assert calls == [] and _doc(fs, p["confirm_id"])["status"] == "pending"
    with acting_as(owner):  # the owner can still answer it
        r = await confirm_action(p["confirm_id"], "yes")
    assert r["status"] == "committed" and len(calls) == 1


async def test_a_proposal_without_a_session_id_is_never_resolved(mk_session):
    store = confirm.MemoryProposalStore()
    previous = confirm._store
    confirm.set_store(store)
    try:
        await store.put(SHOP, "cf_orphan", {"kind": "order", "prompt": "Order?", "payload": {},
                                            "committer": "test.flow.orphan", "status": "pending"})
        s, _ = mk_session()
        with acting_as(s):
            r = await confirm.resolve("cf_orphan", "yes", None)
        assert r["error"] == "unknown_confirm" and (await store.get(SHOP, "cf_orphan"))["status"] == "pending"
    finally:
        confirm.set_store(previous)


# ---------- resolve: unknown, expired, broken committers ----------
async def test_unknown_confirm_id(fs, mk_session):
    s, _ = mk_session(lang="hi")
    with acting_as(s):
        r = await confirm.resolve("cf_missing", "yes", None)
    assert r == {"ok": False, "say": t("confirm.unknown", "hi"), "status": "unknown", "error": "unknown_confirm"}


async def test_a_proposal_older_than_two_minutes_expires(fs, mk_session):
    assert confirm.PROPOSAL_TTL_S == 120
    s, _ = mk_session()
    with acting_as(s):
        p, calls = await _propose()
        _doc(fs, p["confirm_id"])["createdAt"] -= 121  # the owner never answered
        r = await confirm_action(p["confirm_id"], "yes")
        again = await confirm.resolve(p["confirm_id"], "yes", None)

    expired = {"ok": False, "say": t("confirm.expired", "en"), "status": "expired", "error": "expired_confirm"}
    assert r == again == expired
    assert calls == [] and _doc(fs, p["confirm_id"])["status"] == "expired"


async def test_a_fresh_proposal_is_not_expired(fs, mk_session):
    s, _ = mk_session()
    with acting_as(s):
        p, calls = await _propose()
        _doc(fs, p["confirm_id"])["createdAt"] -= 100  # still inside the two minutes
        r = await confirm_action(p["confirm_id"], "yes")
    assert r["status"] == "committed" and len(calls) == 1


async def test_a_committer_missing_at_resolve_time_keeps_the_proposal_pending(fs, mk_session):
    s, _ = mk_session()
    with acting_as(s):
        p, _ = await _propose()
        _doc(fs, p["confirm_id"])["committer"] = "test.flow.gone"  # e.g. its module is not imported here
        r = await confirm_action(p["confirm_id"], "yes")
    assert r == {"ok": False, "say": t("error.internal", "en"), "status": "pending", "error": "no_committer"}
    assert _doc(fs, p["confirm_id"])["status"] == "pending"


async def test_a_non_dict_committer_result_is_wrapped(fs, mk_session):
    async def commit(payload: dict, value: str | None):
        return "Done."

    confirm.register_committer("test.flow.str", commit)
    s, _ = mk_session()
    with acting_as(s):
        p = await confirm.propose("generic", "Proceed?", {}, "test.flow.str")
        r = await confirm.resolve(p["confirm_id"], "yes", None)
    assert r == {"ok": True, "say": "Done.", "status": "committed"}


async def test_locks_are_dropped_once_a_proposal_is_answered(fs, mk_session):
    s, _ = mk_session()
    with acting_as(s):
        p, _ = await _propose()
        await asyncio.gather(*(confirm.resolve(p["confirm_id"], "yes", None) for _ in range(3)))
    assert (SHOP, p["confirm_id"]) not in confirm._LOCKS


# ---------- the confirm_action Live tool ----------
async def test_confirm_action_runs_in_the_session_set_by_the_live_tool_wrapper(fs, mk_session):
    s, _ = mk_session()
    with acting_as(s):
        p, calls = await _propose()
    tool = wrap_tool(confirm_action, s)  # how LiveBridge hands it to Gemini: no session set by the caller
    r = await tool(confirm_id=p["confirm_id"], answer="yes", tool_context=SimpleNamespace(function_call_id="fc-1"))
    assert r["status"] == "committed" and len(calls) == 1
    assert s.tool_results[-1] == {"tool": "confirm_action", "call_id": "fc-1", "result": r}


def test_confirm_action_is_a_registered_live_tool():
    assert confirm_action in TOOL_FUNCS and TOOLS_BY_NAME["confirm_action"] is confirm_action
    assert list(inspect.signature(confirm_action).parameters) == ["confirm_id", "answer"]
    doc = (confirm_action.__doc__ or "").lower()
    assert "owner" in doc and "needs_confirm" in doc and "customer" in doc


@pytest.mark.parametrize("lang", ["en", "ta", "hi"])
def test_every_confirm_text_is_spoken_in_every_language(lang):
    for key in ("confirm.unknown", "confirm.expired", "confirm.cancelled", "confirm.already"):
        text = t(key, lang)
        assert text and text != key, (key, lang)
        if lang != "en":
            assert text != t(key, "en"), (key, lang)
