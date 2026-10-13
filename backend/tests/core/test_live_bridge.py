"""LiveBridge: Live events -> ServerMsgs, barge-in, hard stop, reconnect, soundbox hook, safety check.

No network: every test drives the in-memory ``FakeBackend``. The one real-Gemini test at the bottom is opt-in
(``AIRA_LIVE_TESTS=1`` plus ``GEMINI_API_KEY``) and skipped by default.
"""
import asyncio
import base64
import os
import sys
from types import SimpleNamespace

import pytest
from live_fakes import FakeBackend, wait_until

from aira_live import live_bridge, safety
from aira_live.system_lines import line as t
from aira_live.live_bridge import AdkLiveBackend, LiveBridge, LiveEvent, on_input_transcript, translate


# ---------- helpers ----------
class Broken(FakeBackend):
    async def start(self, *a):
        raise ConnectionError("live down")


def _bridge(s, *backends, max_reconnects=5, tools=None):
    it = iter(backends)
    bridge = LiveBridge(s, lambda: next(it), "prompt", tools or [])
    bridge.BACKOFF_BASE_S = 0
    bridge.MAX_RECONNECTS = max_reconnects
    return bridge


def _of(rec, kind: str) -> list:
    return [m for m in rec.msgs if m.t == kind]


def _cues(rec) -> list[str]:
    return [m.cue for m in _of(rec, "cue")]


def _audio(rec) -> list[bytes]:
    return [base64.b64decode(m.pcm24) for m in _of(rec, "audio")]


def _result(tool: str, result: dict) -> dict:
    return {"tool": tool, "call_id": f"c-{tool}", "result": result}


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


# ---------- plan tests ----------
async def test_events_become_messages_and_soundbox_listener_fires(mk_session):
    s, rec = mk_session()
    heard = []

    async def cb(text):
        heard.append(text)

    unsub = on_input_transcript(s.session_id, cb)
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    b.push(LiveEvent("input_transcript", text="received rupees 60", final=True))
    b.push(LiveEvent("audio", audio=b"\x01\x02"))
    b.push(LiveEvent("output_transcript", text="Payment received.", final=True))
    await wait_until(lambda: _of(rec, "audio") and heard and len(_of(rec, "transcript")) == 2)
    assert _audio(rec) == [b"\x01\x02"]
    assert [(m.role, m.text, m.final) for m in _of(rec, "transcript")] == [
        ("user", "received rupees 60", True), ("aira", "Payment received.", True)]
    assert heard == ["received rupees 60"]
    unsub()
    b.push(LiveEvent("input_transcript", text="received rupees 90", final=True))
    await wait_until(lambda: len(_of(rec, "transcript")) == 3)
    await settle()
    assert heard == ["received rupees 60"]  # unsubscribed listener no longer fires
    await bridge.close()


async def test_client_audio_and_text_reach_the_backend(mk_session):
    s, _ = mk_session()
    b = FakeBackend()
    tools = [object()]
    bridge = _bridge(s, b, tools=tools)
    await bridge.start()
    await bridge.send_audio(base64.b64encode(b"\x00\x01").decode())
    await bridge.send_text("hello")
    await bridge.speak_notice("Sakthi vendor confirmed: delivery at 6 pm.")
    assert b.sent_audio == [b"\x00\x01"] and b.sent_text[0] == "hello"
    assert b.sent_text[1].startswith("[SYSTEM NOTE] Tell the owner now, in the language he is speaking")
    assert b.sent_text[1].endswith(": Sakthi vendor confirmed: delivery at 6 pm.")
    assert b.system_prompt == "prompt" and b.tools is tools and b.started_with == [None]
    await bridge.close()
    assert b.closed
    await bridge.send_audio(base64.b64encode(b"late").decode())  # after close: dropped, never raises
    await bridge.send_audio("%%% not base64 %%%")
    assert b.sent_audio == [b"\x00\x01"]


async def test_barge_in_cue_and_hard_stop_mutes_until_next_question(mk_session):
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    b.push(LiveEvent("audio", audio=b"a1"))
    await wait_until(lambda: _of(rec, "audio"))
    bridge.hard_stop()                                   # long-press on the phone
    b.push(LiveEvent("audio", audio=b"a2"))              # rest of the stopped answer -> dropped
    b.push(LiveEvent("output_transcript", text="and the curd", final=False))  # its caption -> dropped too
    b.push(LiveEvent("input_transcript", text="two litres curd", final=True))
    b.push(LiveEvent("audio", audio=b"a3"))              # answer to the new question -> played
    b.push(LiveEvent("interrupted"))
    await wait_until(lambda: "interrupted" in _cues(rec))
    assert _audio(rec) == [b"a1", b"a3"]
    assert [m.role for m in _of(rec, "transcript")] == ["user"]
    await bridge.close()


async def test_go_away_reconnects_with_handle_and_listeners_survive(mk_session):
    s, rec = mk_session()
    heard = []

    async def cb(text):
        heard.append(text)

    unsub = on_input_transcript(s.session_id, cb)
    b1, b2 = FakeBackend(), FakeBackend()
    bridge = _bridge(s, b1, b2)
    await bridge.start()
    b1.push(LiveEvent("resumption_handle", handle="h1"))
    b1.push(LiveEvent("go_away"))
    await wait_until(lambda: b2.started_with == ["h1"])
    b2.push(LiveEvent("input_transcript", text="₹60 received", final=True))
    await wait_until(lambda: heard == ["₹60 received"])
    assert b1.closed and "reconnect" in _cues(rec)
    unsub()
    await bridge.close()


async def test_gives_up_after_max_reconnects_with_spoken_error(mk_session):
    s, rec = mk_session()
    b1 = FakeBackend()
    bridge = _bridge(s, b1, Broken(), Broken(), Broken(), max_reconnects=2)
    await bridge.start()
    b1.end()  # stream drops
    await wait_until(lambda: _of(rec, "error"))
    err = _of(rec, "error")[0]
    assert err.code == "network" and err.say == t("live.unavailable", "en") and "AI" in err.say
    assert _cues(rec).count("reconnect") == 1  # one earcon per outage, not per attempt
    await bridge.close()


async def test_wrong_money_number_triggers_warn_and_correction(mk_session):
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    s.tool_results.append(_result("sale_start", {"total": 60, "say": "Total ₹60."}))
    b.push(LiveEvent("output_transcript", text="Total is ₹600.", final=True))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: any("600" in x for x in b.sent_text))
    assert "warn" in _cues(rec)
    assert b.sent_text == [safety.correction_text([600], "en")]
    assert s.tool_results == []  # cleared for the next turn
    await bridge.close()


def test_translate_adk_event():
    ev = SimpleNamespace(
        interrupted=False,
        input_transcription=SimpleNamespace(text="hi", finished=True),
        output_transcription=None,
        content=SimpleNamespace(parts=[SimpleNamespace(inline_data=SimpleNamespace(mime_type="audio/pcm;rate=24000",
                                                                                    data=b"x"))]),
        live_session_resumption_update=SimpleNamespace(new_handle="h9"),
        go_away=None,
        turn_complete=True,
    )
    out = translate(ev)
    assert [e.kind for e in out] == ["input_transcript", "audio", "resumption_handle", "turn_complete"]
    assert out[0].final is True and out[1].audio == b"x" and out[2].handle == "h9"
    assert out[3].more is False  # no interaction_status reported: turn_complete is terminal


# ---------- robustness ----------
def test_translate_go_away_interrupted_and_ignores_non_audio_parts():
    ev = SimpleNamespace(
        interrupted=True,
        input_transcription=None,
        output_transcription=SimpleNamespace(text="Tot", finished=False),
        content=SimpleNamespace(parts=[SimpleNamespace(inline_data=None, text="call"),
                                       SimpleNamespace(inline_data=SimpleNamespace(mime_type="image/jpeg", data=b"j"))]),
        live_session_resumption_update=SimpleNamespace(new_handle=None),
        go_away=SimpleNamespace(time_left="10s"),
        turn_complete=False,
    )
    out = translate(ev)
    assert [e.kind for e in out] == ["interrupted", "output_transcript", "go_away"]
    assert out[1].final is False and out[1].text == "Tot"


def test_translate_marks_turns_of_an_unfinished_interaction():
    from google.genai import types

    def tc(status):
        return SimpleNamespace(turn_complete=True, interaction_status=status)

    assert translate(tc(types.InteractionStatus.IN_PROGRESS))[0].more is True
    assert translate(tc("IN_PROGRESS"))[0].more is True
    assert translate(tc(types.InteractionStatus.IDLE))[0].more is False
    assert translate(tc(None))[0].more is False


async def test_multi_turn_answer_stays_muted_and_is_checked_once_idle(mk_session):
    """Newer Live models may answer one prompt in several turns (interaction_status IN_PROGRESS)."""
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    bridge.hard_stop()
    b.push(LiveEvent("audio", audio=b"old1"))
    b.push(LiveEvent("turn_complete", more=True))       # the stopped answer goes on in another turn
    b.push(LiveEvent("audio", audio=b"old2"))           # -> still dropped
    b.push(LiveEvent("turn_complete"))                  # interaction idle -> mute lifts
    b.push(LiveEvent("audio", audio=b"new"))
    await wait_until(lambda: _of(rec, "audio"))
    assert _audio(rec) == [b"new"]
    # A tool ran in turn 1; the wrong total is spoken in turn 2 of the same answer.
    s.tool_results.append(_result("sale_start", {"total": 60}))
    b.push(LiveEvent("turn_complete", more=True))
    b.push(LiveEvent("output_transcript", text="Total is ₹600.", final=True))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: b.sent_text)
    assert "warn" in _cues(rec) and b.sent_text == [safety.correction_text([600], "en")]
    assert s.tool_results == []
    await bridge.close()


async def test_streamed_fragments_give_listeners_one_full_utterance_and_no_false_warn(mk_session):
    s, rec = mk_session()
    heard = []

    async def cb(text):
        heard.append(text)

    on_input_transcript(s.session_id, cb)
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    # ADK streams fragments (final=False) and then the whole utterance (final=True).
    b.push(LiveEvent("input_transcript", text="received", final=False))
    b.push(LiveEvent("input_transcript", text=" rupees 60", final=False))
    b.push(LiveEvent("input_transcript", text="received rupees 60", final=True))
    await wait_until(lambda: heard)
    s.tool_results.append(_result("sale_start", {"total": 60, "say": "Total ₹60."}))
    b.push(LiveEvent("output_transcript", text="Total is ₹6", final=False))   # a number split across chunks
    b.push(LiveEvent("output_transcript", text="0.", final=False))
    b.push(LiveEvent("turn_complete"))
    # a backend that never marks a transcript final is flushed at turn end
    b.push(LiveEvent("input_transcript", text="paid", final=False))
    b.push(LiveEvent("input_transcript", text=" by UPI", final=False))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: len(heard) == 2)
    await settle()
    assert heard == ["received rupees 60", "paid by UPI"]
    assert "warn" not in _cues(rec) and b.sent_text == []
    await bridge.close()


async def test_hard_stop_also_lifts_when_the_stopped_turn_completes(mk_session):
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    bridge.hard_stop()
    b.push(LiveEvent("audio", audio=b"old"))
    b.push(LiveEvent("turn_complete"))
    b.push(LiveEvent("audio", audio=b"notice"))  # e.g. a vendor notice spoken later
    await wait_until(lambda: _of(rec, "audio"))
    assert _audio(rec) == [b"notice"]
    await bridge.close()


async def test_a_long_press_while_idle_never_swallows_the_next_notice(mk_session):
    """Long-press with nothing playing (e.g. to stop guidance): the next vendor notice is still heard."""
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    bridge.hard_stop()
    await bridge.speak_notice("Sakthi vendor confirmed: delivery at 6 pm.")
    b.push(LiveEvent("output_transcript", text="Sakthi vendor delivers at 6 pm.", final=True))
    b.push(LiveEvent("audio", audio=b"notice"))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: _of(rec, "audio"))
    assert _audio(rec) == [b"notice"]
    # Live's words are the notice's one caption (the server never captions the facts itself).
    assert [m.text for m in _of(rec, "transcript")] == ["Sakthi vendor delivers at 6 pm."]
    await bridge.close()


async def test_text_sent_during_a_stopped_answer_is_heard_once_that_answer_is_cut(mk_session):
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    b.push(LiveEvent("audio", audio=b"a1"))
    await wait_until(lambda: _of(rec, "audio"))
    bridge.hard_stop()
    await bridge.send_text("How many curd packets are left?")  # typed while the stopped answer still streams
    b.push(LiveEvent("audio", audio=b"a2"))                    # rest of the stopped answer -> dropped
    b.push(LiveEvent("interrupted"))                           # the new prompt cut the old answer
    b.push(LiveEvent("audio", audio=b"a3"))                    # answer to the typed question -> played
    await wait_until(lambda: len(_of(rec, "audio")) == 2)
    assert _audio(rec) == [b"a1", b"a3"]
    await bridge.close()


async def test_a_reconnect_ends_the_hard_stop(mk_session):
    """The stopped answer died with the old connection; what Live says after reconnecting is new."""
    s, rec = mk_session()
    b1, b2 = FakeBackend(), FakeBackend()
    bridge = _bridge(s, b1, b2)
    await bridge.start()
    b1.push(LiveEvent("audio", audio=b"old"))
    await wait_until(lambda: _of(rec, "audio"))
    bridge.hard_stop()
    b1.end()  # the connection drops in the middle of the stopped answer
    await wait_until(lambda: b2.started_with == [None])
    b2.push(LiveEvent("audio", audio=b"new"))
    await wait_until(lambda: len(_of(rec, "audio")) == 2)
    assert _audio(rec) == [b"old", b"new"]
    await bridge.close()


async def test_unheard_words_of_a_stopped_answer_are_not_checked(mk_session):
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    s.tool_results.append(_result("sale_start", {"total": 60}))
    bridge.hard_stop()
    b.push(LiveEvent("output_transcript", text="Total is ₹600.", final=True))  # muted: the owner never heard it
    b.push(LiveEvent("turn_complete"))
    b.push(LiveEvent("audio", audio=b"next"))
    await wait_until(lambda: _of(rec, "audio"))
    await settle()
    assert "warn" not in _cues(rec) and b.sent_text == [] and s.tool_results == []
    await bridge.close()


async def test_number_words_go_to_the_llm_verifier(mk_session, monkeypatch):
    s, rec = mk_session(lang="ta")
    seen = {}

    async def fake_verify(spoken, results):
        seen["args"] = (spoken, results)
        return safety.SafetyVerdict(ok=False, unexpected=[600])

    monkeypatch.setattr(safety, "llm_verify", fake_verify)
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    s.tool_results.append(_result("sale_start", {"total": 60}))
    b.push(LiveEvent("output_transcript", text="மொத்தம் அறுநூறு ரூபாய்", final=True))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: b.sent_text)
    assert seen["args"] == ("மொத்தம் அறுநூறு ரூபாய்", [{"total": 60}])
    assert "warn" in _cues(rec) and b.sent_text == [safety.correction_text([600], "ta")]
    await bridge.close()


async def test_safety_skips_turns_without_tools_and_allows_recent_results(mk_session):
    s, rec = mk_session()
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    # turn 1: sale_start ran and AIRA read the total back
    s.tool_results.append(_result("sale_start", {"total": 60, "say": "Total ₹60."}))
    b.push(LiveEvent("output_transcript", text="Total ₹60.", final=True))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: s.tool_results == [])
    # turn 2: no tool ran -> no check (prices may come from the shop catalog in the prompt)
    b.push(LiveEvent("output_transcript", text="Milk is ₹30.", final=True))
    b.push(LiveEvent("turn_complete"))
    b.push(LiveEvent("audio", audio=b"t2"))
    await wait_until(lambda: _of(rec, "audio"))
    # turn 3: a different tool ran; repeating the earlier total is still backed by a recent result
    s.tool_results.append(_result("sale_pay", {"change": 40, "say": "Change ₹40."}))
    b.push(LiveEvent("output_transcript", text="₹60 paid, change ₹40.", final=True))
    b.push(LiveEvent("turn_complete"))
    b.push(LiveEvent("audio", audio=b"t3"))
    await wait_until(lambda: len(_of(rec, "audio")) == 2)
    await settle()
    assert "warn" not in _cues(rec) and b.sent_text == []
    await bridge.close()


async def test_failing_listener_or_safety_never_drops_the_connection(mk_session, monkeypatch):
    s, rec = mk_session()

    async def bad(text):
        raise RuntimeError("listener bug")

    def boom(*a, **k):
        raise RuntimeError("safety bug")

    on_input_transcript(s.session_id, bad)
    monkeypatch.setattr(safety, "check_numbers", boom)
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    s.tool_results.append(_result("sale_start", {"total": 60}))
    b.push(LiveEvent("input_transcript", text="hello", final=True))
    b.push(LiveEvent("output_transcript", text="Total ₹60.", final=True))
    b.push(LiveEvent("turn_complete"))
    b.push(LiveEvent("audio", audio=b"still here"))
    await wait_until(lambda: _of(rec, "audio"))
    assert b.started_with == [None] and "reconnect" not in _cues(rec)
    assert s.tool_results == []
    await bridge.close()


async def test_a_closed_phone_socket_never_drops_the_live_connection(mk_session):
    s, _ = mk_session()
    sent = []

    async def flaky_send(msg):
        if msg.t == "audio" and not sent:
            sent.append("boom")
            raise RuntimeError("websocket closed")
        sent.append(msg)

    s._send = flaky_send
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    b.push(LiveEvent("audio", audio=b"lost"))
    b.push(LiveEvent("audio", audio=b"ok"))
    await wait_until(lambda: len(sent) == 2)
    assert b.started_with == [None] and base64.b64decode(sent[1].pcm24) == b"ok"
    await bridge.close()


async def test_text_sent_while_reconnecting_is_delivered_after_reconnect(mk_session):
    s, rec = mk_session()
    gate = asyncio.Event()

    class Slow(FakeBackend):
        async def start(self, *a):
            await gate.wait()
            await super().start(*a)

    b1, b2 = FakeBackend(), Slow()
    bridge = _bridge(s, b1, b2)
    await bridge.start()
    b1.push(LiveEvent("go_away"))
    await wait_until(lambda: "reconnect" in _cues(rec))
    await bridge.speak_notice("Aavin vendor replied: coming at 7.")
    await bridge.send_audio(base64.b64encode(b"lost").decode())  # realtime audio is dropped meanwhile
    gate.set()
    await wait_until(lambda: b2.sent_text)
    assert "coming at 7" in b2.sent_text[0] and b1.sent_text == [] and b2.sent_audio == []
    await bridge.send_audio(base64.b64encode(b"live").decode())
    assert b2.sent_audio == [b"live"]
    await bridge.close()


async def test_close_drops_the_sessions_listeners(mk_session):
    s, _ = mk_session()

    async def cb(text):
        pass

    on_input_transcript(s.session_id, cb)
    bridge = _bridge(s, FakeBackend())
    await bridge.start()
    await bridge.close()
    assert s.session_id not in live_bridge._listeners
    await bridge.close()  # idempotent


async def test_failed_first_connect_raises_from_start(mk_session):
    s, _ = mk_session()
    bridge = _bridge(s, Broken())
    with pytest.raises(ConnectionError):
        await bridge.start()
    await bridge.close()


async def test_adk_backend_wires_run_live(mk_session, monkeypatch):
    """AdkLiveBackend against a stub ADK: right model, resumption handle, BIDI audio, transcripts, queue calls."""
    calls = {}

    class Stub:
        def __init__(self, *a, **kw):
            self.args, self.kw = a, kw
            for k, v in kw.items():
                setattr(self, k, v)

    class Agent(Stub):
        pass

    class RunConfig(Stub):
        pass

    class LiveRequestQueue:
        def __init__(self):
            self.realtime, self.content, self.closed = [], [], False
            calls["queue"] = self

        def send_realtime(self, blob):
            self.realtime.append(blob)

        def send_content(self, content):
            self.content.append(content)

        def close(self):
            self.closed = True

    class SessionService:
        async def create_session(self, *, app_name, user_id):
            calls["create_session"] = (app_name, user_id)
            return SimpleNamespace(id="adk-s1")

    class InMemoryRunner:
        def __init__(self, agent, app_name):
            calls["runner"] = (agent, app_name)
            self.session_service = SessionService()

        def run_live(self, **kw):
            calls["run_live"] = kw

            async def gen():
                yield SimpleNamespace(interrupted=False, input_transcription=None, output_transcription=None,
                                      content=SimpleNamespace(parts=[SimpleNamespace(
                                          inline_data=SimpleNamespace(mime_type="audio/pcm", data=b"pcm"))]),
                                      live_session_resumption_update=None, go_away=None, turn_complete=True)
            return gen()

    gtypes = SimpleNamespace(**{n: type(n, (Stub,), {}) for n in (
        "AudioTranscriptionConfig", "SessionResumptionConfig", "ContextWindowCompressionConfig", "SlidingWindow",
        "Blob", "Content", "Part")})
    mods = {
        "google.adk.agents": SimpleNamespace(Agent=Agent, LiveRequestQueue=LiveRequestQueue),
        "google.adk.agents.run_config": SimpleNamespace(RunConfig=RunConfig,
                                                        StreamingMode=SimpleNamespace(BIDI="bidi")),
        "google.adk.runners": SimpleNamespace(InMemoryRunner=InMemoryRunner),
        "google.genai": SimpleNamespace(types=gtypes),
        "google.genai.types": gtypes,
    }
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)

    s, _ = mk_session()
    backend = AdkLiveBackend(s)
    tool = object()
    await backend.start("SYSTEM PROMPT {not a template}", [tool], "h7")
    agent, app_name = calls["runner"]
    from aira_live.models import model_for
    assert agent.model == model_for("live") and agent.tools == [tool]
    assert calls["create_session"] == (app_name, "u1")
    rc = calls["run_live"]["run_config"]
    assert rc.streaming_mode == "bidi" and rc.response_modalities == ["AUDIO"]
    assert rc.session_resumption.handle == "h7"
    assert rc.input_audio_transcription is not None and rc.output_audio_transcription is not None
    assert calls["run_live"]["session_id"] == "adk-s1" and calls["run_live"]["live_request_queue"] is calls["queue"]
    # The prompt is passed as a provider so ADK never treats {braces} in it as session-state templates.
    instruction = agent.instruction
    assert callable(instruction) and instruction(None) == "SYSTEM PROMPT {not a template}"

    await backend.send_audio(b"\x00\x01")
    await backend.send_text("hi")
    q = calls["queue"]
    assert q.realtime[0].data == b"\x00\x01" and q.realtime[0].mime_type == "audio/pcm;rate=16000"
    assert q.content[0].role == "user" and q.content[0].parts[0].text == "hi"
    got = [ev async for ev in backend.events()]
    assert [e.kind for e in got] == ["audio", "turn_complete"] and got[0].audio == b"pcm"
    await backend.close()
    assert q.closed


# ---------- opt-in: real Gemini Live (skipped by default) ----------
@pytest.mark.skipif(not (os.getenv("AIRA_LIVE_TESTS") == "1" and os.getenv("GEMINI_API_KEY")),
                    reason="real Gemini Live: set AIRA_LIVE_TESTS=1 and GEMINI_API_KEY")
async def test_real_live_model_answers_a_text_turn_with_audio(mk_session):
    s, rec = mk_session()
    bridge = LiveBridge(s, lambda: AdkLiveBackend(s), "You are AIRA, a shop assistant. Answer in one short sentence.",
                        [])
    await bridge.start()
    try:
        await bridge.send_text("Say hello to the shopkeeper.")
        await wait_until(lambda: _of(rec, "audio") and _of(rec, "transcript"), timeout=45)
    finally:
        await bridge.close()
    assert not _of(rec, "error")
