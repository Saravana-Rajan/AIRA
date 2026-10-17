"""Long-press Stop cancels the session's work: in-flight tool calls, pending proposals and the answer being spoken.

A tool result that finishes after Stop is never delivered to Live (a SILENT ``{"cancelled": true}`` takes its
place), never recorded in ``session.tool_results`` and never heard; AIRA stays silent until the owner's next
real turn. Other sessions are untouched. No network: fake tools and the in-memory ``FakeBackend``.
"""
import asyncio
import base64
from types import SimpleNamespace

import pytest
from live_fakes import FakeBackend, wait_until

from aira_live import confirm, tool_runtime
from aira_live.live_bridge import STOP_NOTE, LiveBridge, LiveEvent
from aira_live.session import current_session
from aira_live.tool_runtime import CANCELLED, live_tools, wrap_tool


def _audio(rec) -> list[bytes]:
    return [base64.b64decode(m.pcm24) for m in rec.msgs if m.t == "audio"]


def _captions(rec) -> list[str]:
    return [m.text for m in rec.msgs if m.t == "transcript" and m.role == "aira"]


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def _bridge(s, *backends):
    it = iter(backends)
    bridge = LiveBridge(s, lambda: next(it), "prompt", [])
    bridge.BACKOFF_BASE_S = 0
    return bridge


# ---------- tool runtime: generation + task cancellation ----------
async def test_stop_cancels_a_slow_tool_and_its_result_is_never_recorded(mk_session):
    s, _ = mk_session()
    seen = {}

    async def camera_ask(question: str) -> dict:
        try:
            await asyncio.sleep(30)  # a slow remote vision call
        except asyncio.CancelledError:
            seen["cancelled"] = True
            raise
        return {"say_en": "Three packets."}

    w = wrap_tool(camera_ask, s)
    call = asyncio.create_task(w(question="how many?", tool_context=SimpleNamespace(function_call_id="c1")))
    await wait_until(lambda: tool_runtime.tools_running(s.session_id) == 1)
    tool_runtime.stop(s.session_id)
    result = await asyncio.wait_for(call, 1)
    assert result == CANCELLED and result["say_en"] == ""
    assert seen == {"cancelled": True}
    assert s.tool_results == [] and tool_runtime.tools_running(s.session_id) == 0


async def test_a_late_result_that_finishes_anyway_is_discarded(mk_session):
    """A remote call cannot always be cancelled: whatever it returns after Stop is dropped."""
    s, _ = mk_session()

    def invoice_check() -> dict:  # sync: nothing to cancel, it finishes after the Stop
        tool_runtime.stop(s.session_id)
        return {"say_en": "Invoice total 1200.", "total": 1200}

    assert await wrap_tool(invoice_check, s)() == CANCELLED
    assert s.tool_results == []


async def test_a_non_blocking_late_result_goes_to_live_silent_and_empty(mk_session):
    from google.genai import types

    s, _ = mk_session()
    release = asyncio.Event()

    async def product_find(product: str) -> dict:
        await asyncio.shield(release.wait())
        return {"say_en": "Curd is on the left shelf.", "kind": "path"}  # urgent: would INTERRUPT

    tool = live_tools([product_find], s, frozenset({"product_find"}))[0]
    call = asyncio.create_task(tool.func(product="curd", tool_context=SimpleNamespace(function_call_id="c2")))
    await wait_until(lambda: tool_runtime.tools_running(s.session_id) == 1)
    tool_runtime.stop(s.session_id)
    release.set()
    out = await asyncio.wait_for(call, 1)
    assert isinstance(out, types.FunctionResponse)
    assert out.scheduling == types.FunctionResponseScheduling.SILENT
    assert out.response == CANCELLED and "Curd" not in str(out.response)
    assert s.tool_results == []


async def test_repeated_stop_is_idempotent_and_new_calls_run_normally(mk_session):
    s, _ = mk_session()

    async def stock_query() -> dict:
        return {"say_en": "12 left.", "qty": 12}

    before = tool_runtime.generation(s.session_id)
    tool_runtime.stop(s.session_id)
    tool_runtime.stop(s.session_id)  # a second long-press: no error
    assert tool_runtime.generation(s.session_id) > before
    out = await wrap_tool(stock_query, s)()
    assert out["qty"] == 12 and s.tool_results[0]["result"]["qty"] == 12


async def test_stop_in_one_session_never_touches_another(mk_session):
    s1, _ = mk_session()
    s2, _ = mk_session()
    gate = asyncio.Event()

    async def delivery_count(name: str) -> dict:
        await gate.wait()
        return {"say_en": "24 crates.", "count": 24}

    c1 = asyncio.create_task(wrap_tool(delivery_count, s1)(name="Sakthi"))
    c2 = asyncio.create_task(wrap_tool(delivery_count, s2)(name="Sakthi"))
    await wait_until(lambda: tool_runtime.tools_running(s1.session_id) == 1
                     and tool_runtime.tools_running(s2.session_id) == 1)
    tool_runtime.stop(s1.session_id)
    gate.set()
    assert await asyncio.wait_for(c1, 1) == CANCELLED
    assert (await asyncio.wait_for(c2, 1))["count"] == 24
    assert s1.tool_results == [] and s2.tool_results[0]["result"]["count"] == 24


async def test_session_end_cancels_running_tools_and_clears_state(mk_session):
    s, _ = mk_session()
    seen = []

    async def walk_to(target: str) -> dict:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise
        return {"say_en": "Arrived."}

    call = asyncio.create_task(wrap_tool(walk_to, s)(target="fridge"))
    await wait_until(lambda: tool_runtime.tools_running(s.session_id) == 1)
    tool_runtime.end_session(s.session_id)
    assert await asyncio.wait_for(call, 1) == CANCELLED
    assert seen == ["cancelled"] and s.tool_results == []
    assert tool_runtime.generation(s.session_id) == 0 and s.session_id not in tool_runtime._TASKS


async def test_cancelling_the_caller_still_propagates(mk_session):
    """ADK cancelling its own task (Live closing) is not a Stop: the CancelledError goes up as before."""
    s, _ = mk_session()

    async def camera_ask() -> dict:
        await asyncio.sleep(30)
        return {}

    call = asyncio.create_task(wrap_tool(camera_ask, s)())
    await wait_until(lambda: tool_runtime.tools_running(s.session_id) == 1)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call


# ---------- confirm: Stop cancels pending proposals ----------
@pytest.fixture
def proposals(mk_session):
    s, rec = mk_session()
    token = current_session.set(s)
    previous = confirm._store
    confirm.set_store(confirm.MemoryProposalStore())
    commits = []

    async def commit(payload, value):
        commits.append(payload)
        return {"say_en": "Paid."}

    confirm.register_committer("test.stop_pay", commit)
    yield s, rec, commits
    current_session.reset(token)
    confirm.set_store(previous)


async def test_stop_cancels_a_pending_proposal_so_a_later_yes_never_commits(proposals):
    s, _, commits = proposals
    p = await confirm.propose("payment", "Pay Sakthi 1000?", {"amount": 1000}, "test.stop_pay")
    assert await confirm.cancel_pending(s.session_id) == 1
    r = await confirm.resolve(p["confirm_id"], "yes", None)
    assert r["status"] == "rejected" and commits == []
    assert await confirm.cancel_pending(s.session_id) == 0  # idempotent


async def test_a_proposal_made_by_a_stopped_tool_is_never_stored_or_shown(proposals):
    s, rec, commits = proposals

    async def supplier_pay(amount: int) -> dict:
        tool_runtime.stop(s.session_id)  # the owner long-presses while the tool is still working
        return await confirm.propose("payment", f"Pay {amount}?", {"amount": amount}, "test.stop_pay")

    assert await wrap_tool(supplier_pay, s)(amount=1000) == CANCELLED
    assert [m for m in rec.msgs if m.t == "confirm"] == [] and s.tool_results == []


async def test_stop_cancels_only_that_sessions_proposals(proposals, mk_session):
    s, _, commits = proposals
    other, _ = mk_session()
    p_other = None
    token = current_session.set(other)
    try:
        p_other = await confirm.propose("payment", "Pay 50?", {"amount": 50}, "test.stop_pay")
    finally:
        current_session.reset(token)
    await confirm.propose("payment", "Pay 1000?", {"amount": 1000}, "test.stop_pay")
    assert await confirm.cancel_pending(s.session_id) == 1
    token = current_session.set(other)
    try:
        assert (await confirm.resolve(p_other["confirm_id"], "yes", None))["status"] == "committed"
    finally:
        current_session.reset(token)


# ---------- bridge: silent until the owner's next real turn ----------
async def test_hard_stop_tells_live_and_stays_silent_through_turn_end_and_reconnect(mk_session):
    s, rec = mk_session()
    b1, b2 = FakeBackend(), FakeBackend()
    bridge = _bridge(s, b1, b2)
    await bridge.start()
    b1.push(LiveEvent("audio", audio=b"a1"))
    await wait_until(lambda: _audio(rec))
    await bridge.hard_stop()
    await bridge.hard_stop()  # repeated long-press: no error, one note
    assert b1.sent_text == [STOP_NOTE]
    b1.push(LiveEvent("audio", audio=b"old"))
    b1.push(LiveEvent("interrupted"))
    b1.push(LiveEvent("turn_complete"))
    b1.push(LiveEvent("output_transcript", text="Curd is on the left shelf.", final=True))  # a late result
    b1.push(LiveEvent("audio", audio=b"late"))
    b1.push(LiveEvent("input_transcript", text="ok", final=True))         # one word: noise, not a turn
    b1.push(LiveEvent("input_transcript", text="how much milk", final=False))  # partial: not yet
    b1.push(LiveEvent("audio", audio=b"late2"))
    b1.push(LiveEvent("turn_complete"))
    b1.end()  # a reconnect does not lift the stop either
    await wait_until(lambda: b2.started_with)
    b2.push(LiveEvent("audio", audio=b"late3"))
    b2.push(LiveEvent("input_transcript", text="how much milk", final=True))  # the owner's next question
    b2.push(LiveEvent("output_transcript", text="Ten litres.", final=True))
    b2.push(LiveEvent("audio", audio=b"new"))
    await wait_until(lambda: len(_audio(rec)) == 2)
    assert _audio(rec) == [b"a1", b"new"]
    assert _captions(rec) == ["Ten litres."]
    await bridge.close()


async def test_a_typed_question_or_a_talk_tap_ends_the_stop(mk_session):
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    await bridge.hard_stop()
    await bridge.send_text("[SYSTEM NOTE] internal correction")  # not the owner: stays silent
    b.push(LiveEvent("audio", audio=b"x"))
    await wait_until(lambda: bridge.answering)
    await bridge.send_text("milk?", user_turn=True)
    b.push(LiveEvent("audio", audio=b"x2"))       # the stopped answer, until Live cuts it
    b.push(LiveEvent("interrupted"))
    b.push(LiveEvent("audio", audio=b"typed"))
    await wait_until(lambda: _audio(rec))
    await bridge.hard_stop()
    b.push(LiveEvent("audio", audio=b"y"))
    await wait_until(lambda: bridge.answering)
    bridge.user_turn()  # the phone's talk tap
    b.push(LiveEvent("interrupted"))
    b.push(LiveEvent("audio", audio=b"tapped"))
    await wait_until(lambda: len(_audio(rec)) == 2)
    assert _audio(rec) == [b"typed", b"tapped"]
    await bridge.close()


def test_ws_stop_cancels_tools_and_proposals_of_that_session(monkeypatch, fake_db):
    """main: {t:"stop"} bumps the tool generation, cancels pending proposals and hard-stops the bridge."""
    from aira_live import main as main_mod

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # mock mode: no Live, the stop path still runs
    calls = []

    def fake_stop(sid):
        calls.append(("tools", sid))

    async def fake_cancel(sid):
        calls.append(("proposals", sid))
        return 0

    monkeypatch.setattr(main_mod.tool_runtime, "stop", fake_stop)
    monkeypatch.setattr(main_mod.confirm, "cancel_pending", fake_cancel)
    from fastapi.testclient import TestClient

    hello = {"t": "hello", "uid": "u1", "shopId": "sunrise_dairy", "lang": "en", "verbosity": "short",
             "appVersion": "0.1.0"}
    with TestClient(main_mod.app).websocket_connect("/ws", headers={"Origin": "https://aira-shop.web.app"}) as ws:
        ws.send_json(hello)
        sid = ws.receive_json()["sessionId"]
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    assert ("tools", sid) in calls and ("proposals", sid) in calls


def test_the_phones_talk_event_is_a_valid_client_message():
    """The phone sends {t:"event", name:"talk"} on a talk tap: the contract must accept it, or the server
    would refuse it and a long-press Stop could only be lifted by a two-word question."""
    from aira_live.contracts import EventMsg, parse_client_msg

    msg = parse_client_msg('{"t": "event", "name": "talk", "data": {}}')
    assert isinstance(msg, EventMsg) and msg.name == "talk"


# ---------- every function response carries status / retryable / message ----------
async def test_results_get_status_retryable_and_message_added_never_replaced(mk_session):
    from aira_live.tool_runtime import with_status

    assert with_status({"say_en": "12 left.", "qty": 12}) == {
        "say_en": "12 left.", "qty": 12, "status": "ok", "retryable": False, "message": "12 left."}
    assert with_status({"ok": False, "error": "no_frames", "say": "I cannot see."})["status"] == "error"
    assert with_status({"sure": False, "say_en": "Maybe curd."})["status"] == "no_results"
    kept = {"status": "invalid_argument", "retryable": True, "message": "Ask which supplier.", "x": 1}
    assert with_status(kept) == kept
    s, _ = mk_session()

    async def stock_query() -> dict:
        return {"say_en": "12 left.", "qty": 12}

    out = await wrap_tool(stock_query, s)()
    assert out["status"] == "ok" and out["qty"] == 12 and s.tool_results[0]["result"]["status"] == "ok"
    assert CANCELLED["status"] == "cancelled" and CANCELLED["retryable"] is False and CANCELLED["message"]
