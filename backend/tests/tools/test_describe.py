"""Describe mode: live narration of new objects while the owner walks (spec next-describe-mode §2)."""
import asyncio

import pytest

from aira_live import prompt, sessions
from aira_live.contracts import RequestFrameMsg, TranscriptMsg
from aira_live.frames import Frame
from aira_live.i18n import t
from aira_live.session import current_session
from aira_live.tools import TOOLS_BY_NAME, describe
from aira_live.vision import observers
from aira_live.vision.gemini import VisionError

TINY = "/9j/4AAQSkZJRg=="


def _frame(ts: float) -> Frame:
    return Frame(id=f"d{ts}", jpeg_b64=TINY, w=640, h=480, ts=ts, purpose="describe")


class Clock:
    def __init__(self, t0: float = 1000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


def _obj(label, x=500, distance="far", y=500):
    return {"label": label, "point": [y, x], "distance": distance}


@pytest.fixture
def session(mk_session):
    s, rec = mk_session(lang="en")
    sessions.register(s)
    token = current_session.set(s)
    yield s, rec
    current_session.reset(token)
    sessions.unregister(s)
    describe._ACTIVE.clear()
    from aira_live.tools import ambient
    ambient._ACTIVE.clear()
    ambient._SWITCH.clear()


@pytest.fixture
def er2(monkeypatch):
    """ER 2 replies in order (dict or exception); records every call."""
    seen: list[dict] = []
    replies: list = []

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        seen.append({"role": role, "parts": parts})
        item = replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return dict(item)

    monkeypatch.setattr(describe, "generate_json", fake)
    seen_replies = replies

    def feed(*items):
        seen_replies.extend(items)

    fake.seen, fake.feed = seen, feed
    return fake


def _loop(s, clock, **kw):
    kw.setdefault("busy", lambda: False)
    return describe.DescribeLoop(s, clock=clock, frame_timeout_s=0.05, **kw)


def _spoken(rec) -> list[str]:
    return [m.text for m in rec.msgs if isinstance(m, TranscriptMsg)]


def _frame_requests(rec):
    # describe frames only: describe_mode also switches ambient mode, whose hold-up stream has its own purpose
    return [(m.purpose, m.continuous, m.stop) for m in rec.msgs
            if isinstance(m, RequestFrameMsg) and m.purpose == "describe"]


# --- registry, prompt, voice trigger ------------------------------------------------------------------------


def test_registered_as_a_live_tool_with_a_prompt_rule():
    assert TOOLS_BY_NAME["describe_mode"] is describe.describe_mode
    text = prompt.render_prompt({"name": "Sunrise Dairy"}, [], [], "en", "short")
    assert "describe_mode" in text and "Stop describing" in text


@pytest.mark.parametrize("text, want", [
    ("describe as I walk", True), ("keep telling me what's around", True), ("Describe what is around me", True),
    ("stop describing", False), ("enough describing", False), ("please stop, don't describe", False),
    ("what is in front of me", None), ("", None), ("நடக்கும்போது விவரி", True), ("விவரிப்பதை நிறுத்து", False),
    ("चलते हुए बताते रहो", True),
])
def test_voice_trigger_parsing(text, want):
    assert describe.parse_describe_command(text) is want


def test_i18n_keys_exist_in_all_three_languages():
    for lang in ("en", "ta", "hi"):
        for key in ("describe.on", "describe.off", "describe.item", "describe.timeout", "describe.cap"):
            assert t(key, lang) != key, (key, lang)


# --- phrasing: direction and distance ------------------------------------------------------------------------


def test_phrase_side_and_distance_in_the_session_language():
    assert describe.phrase(_obj("milk crates", x=150, distance="far"), "en") == "Milk crates to your far left, a few steps away."
    assert describe.phrase(_obj("table", x=500, distance="arm"), "en") == "Table straight ahead, at arm's length."
    assert describe.phrase(_obj("fridge", x=850, distance="near"), "en") == "Fridge to your far right, within arm's reach."
    ta = describe.phrase(_obj("fridge", x=850, distance="near"), "ta")
    assert t("cam.side.far_right", "ta") in ta and t("cam.dist.near", "ta") in ta


def test_coarse_side_is_left_ahead_right():
    assert [describe.coarse_side(x) for x in (0, 399, 400, 600, 601, 1000)] == ["left", "left", "ahead", "ahead", "right", "right"]


# --- only new or changed objects are spoken ---------------------------------------------------------------------


async def test_speaks_only_new_objects_nearest_first_then_stays_quiet(session, er2):
    s, rec = session
    clock = Clock()
    loop = _loop(s, clock)
    er2.feed({"objects": [_obj("door", x=900, distance="far"), _obj("table", x=500, distance="arm")], "confidence": 0.9})
    s.frames.add(_frame(1))
    assert await loop.step() is True
    assert _spoken(rec) == ["Table straight ahead, at arm's length."]          # nearest first, one sentence
    assert loop.calls == 1

    clock.t += 5                                                              # past the 4 s gap
    er2.feed({"objects": [_obj("table", x=500, distance="arm"), _obj("door", x=900, distance="far")], "confidence": 0.9})
    s.frames.add(_frame(2))
    await loop.step()
    assert _spoken(rec) == ["Table straight ahead, at arm's length.", "Door to your far right, a few steps away."]

    clock.t += 5                                                              # same scene: nothing new
    er2.feed({"objects": [_obj("table", x=500, distance="arm"), _obj("door", x=900, distance="far")], "confidence": 0.9})
    s.frames.add(_frame(3))
    await loop.step()
    assert len(_spoken(rec)) == 2


async def test_a_changed_distance_or_an_expired_memory_is_spoken_again(session, er2):
    s, rec = session
    clock = Clock()
    loop = _loop(s, clock, ttl_s=20.0)
    er2.feed({"objects": [_obj("fridge", x=850, distance="far")], "confidence": 0.9})
    s.frames.add(_frame(1))
    await loop.step()
    clock.t += 5
    er2.feed({"objects": [_obj("fridge", x=850, distance="near")], "confidence": 0.9})   # he walked up to it
    s.frames.add(_frame(2))
    await loop.step()
    assert _spoken(rec) == ["Fridge to your far right, a few steps away.", "Fridge to your far right, within arm's reach."]
    clock.t += 5
    er2.feed({"objects": [_obj("fridge", x=850, distance="near")], "confidence": 0.9})
    s.frames.add(_frame(3))
    await loop.step()
    assert len(_spoken(rec)) == 2                                             # within the TTL: quiet
    clock.t += 21
    er2.feed({"objects": [_obj("fridge", x=850, distance="near")], "confidence": 0.9})
    s.frames.add(_frame(4))
    await loop.step()
    assert len(_spoken(rec)) == 3                                             # memory expired


# --- rate limit ---------------------------------------------------------------------------------------------------


async def test_at_most_one_sentence_per_four_seconds(session, er2):
    s, rec = session
    clock = Clock()
    loop = _loop(s, clock, min_gap_s=4.0)
    for i in range(1, 4):
        er2.feed({"objects": [_obj(f"thing{i}", x=500, distance="near")], "confidence": 0.9})
        s.frames.add(_frame(i))
        await loop.step()
        clock.t += 1.5
    assert _spoken(rec) == ["Thing1 straight ahead, within arm's reach."]
    clock.t += 1                                                              # 4.5 s after the first sentence
    er2.feed({"objects": [_obj("thing4", x=500, distance="near")], "confidence": 0.9})
    s.frames.add(_frame(9))
    await loop.step()
    assert len(_spoken(rec)) == 2


async def test_pauses_while_the_owner_talks_or_aira_speaks(session, er2):
    s, rec = session
    clock = Clock()
    busy = {"v": True}
    loop = _loop(s, clock, busy=lambda: busy["v"])
    s.frames.add(_frame(1))
    await loop.step()
    assert _spoken(rec) == [] and loop.calls == 0 and er2.seen == []         # no ER 2 call while busy
    busy["v"] = False
    er2.feed({"objects": [_obj("table")], "confidence": 0.9})
    s.frames.add(_frame(2))
    await loop.step()
    assert _spoken(rec) == ["Table straight ahead, a few steps away."]
    # the owner just finished a sentence: quiet for PAUSE_AFTER_TALK_S even though nothing else is busy
    clock.t += 10
    await loop.on_utterance("how much is curd?")
    s.frames.add(_frame(3))
    await loop.step()
    assert loop.calls == 1
    clock.t += describe.PAUSE_AFTER_TALK_S + 0.1
    er2.feed({"objects": [_obj("chair", x=200)], "confidence": 0.9})
    s.frames.add(_frame(4))
    await loop.step()
    assert len(_spoken(rec)) == 2


def test_bridge_busy_reads_the_live_bridge_state(session):
    s, _ = session

    class Bridge:
        _answering = False
        _in_partial = ""

    b = Bridge()
    sessions.attach_bridge(s.session_id, b)
    assert describe._bridge_busy(s) is False
    b._answering = True
    assert describe._bridge_busy(s) is True
    b._answering, b._in_partial = False, "how much"
    assert describe._bridge_busy(s) is True


# --- uncertainty, safety, observers --------------------------------------------------------------------------------


async def test_low_confidence_people_and_safe_words_are_never_spoken(session, er2):
    s, rec = session
    loop = _loop(s, Clock())
    er2.feed({"objects": [_obj("table")], "confidence": 0.3})
    s.frames.add(_frame(1))
    await loop.step()
    assert _spoken(rec) == []
    er2.feed({"objects": [_obj("man with a bag", x=500, distance="near"), _obj("safe path", x=500, distance="near"),
                          _obj("clear floor", x=500, distance="near")], "confidence": 0.9})
    s.frames.add(_frame(2))
    await loop.step()
    assert _spoken(rec) == []
    assert "safe" not in " ".join(t(k, "en") for k in ("describe.on", "describe.off", "describe.item")).lower()


async def test_vision_failure_is_logged_and_the_loop_goes_on(session, er2):
    s, rec = session
    loop = _loop(s, Clock())
    er2.feed(VisionError("down"))
    s.frames.add(_frame(1))
    assert await loop.step() is True
    assert _spoken(rec) == [] and loop.active


async def test_every_er2_result_reaches_the_vision_observers(session, er2, monkeypatch):
    s, _ = session
    got = []

    async def cb(session_, objects, context):
        got.append((session_, objects, context))

    monkeypatch.setattr(observers, "OBSERVERS", [cb])
    loop = _loop(s, Clock())
    er2.feed({"objects": [_obj("keys", x=100, distance="near")], "confidence": 0.4})   # low confidence still observed
    s.frames.add(_frame(1))
    await loop.step()
    assert got[0][0] is s and got[0][1][0]["label"] == "keys"
    assert got[0][2]["source"] == "describe" and got[0][2]["frame_id"] == "d1" and got[0][2]["confidence"] == 0.4


# --- start, stop, timeout, cap --------------------------------------------------------------------------------------


async def test_describe_mode_tool_starts_continuous_frames_and_stop_ends_them(session, fake_db):
    s, rec = session
    out = await describe.describe_mode(True)
    assert out == {"say": t("describe.on", "en"), "active": True}
    loop = describe.active(s)
    assert loop is not None and loop.active and loop.task is not None
    assert _frame_requests(rec) == [("describe", True, None)]
    out = await describe.describe_mode(False)
    assert out == {"say": t("describe.off", "en"), "active": False}
    assert describe.active(s) is None and not loop.active
    assert _frame_requests(rec)[-1] == ("describe", None, True)
    await asyncio.sleep(0)
    assert loop.task.cancelled() or loop.task.done()
    assert await describe.describe_mode(False) == {"say": t("describe.off", "en"), "active": False}  # idempotent


async def test_a_second_start_replaces_the_first(session):
    s, rec = session
    first = await describe.start(s, run=False)
    second = await describe.start(s, run=False)
    assert not first.active and second.active and describe.active(s) is second
    await describe.stop_describe(s)


async def test_stop_describing_by_voice_ends_the_loop(session):
    s, rec = session
    loop = await describe.start(s, run=False, busy=lambda: False)
    from aira_live.live_bridge import _listeners
    assert loop.on_utterance in _listeners[s.session_id]
    await loop.on_utterance("stop describing now")
    assert not loop.active and describe.active(s) is None
    assert _spoken(rec)[-1] == t("describe.off", "en")
    assert s.session_id not in _listeners                                # unsubscribed


async def test_auto_stop_after_the_time_limit(session):
    s, rec = session
    clock = Clock()
    loop = _loop(s, clock, max_s=600.0, interval_s=0.0)
    describe._ACTIVE[s.session_id] = loop
    clock.t += 600.1
    await loop.run()
    assert not loop.active and describe.active(s) is None
    assert _spoken(rec) == [t("describe.timeout", "en")]
    assert _frame_requests(rec)[-1] == ("describe", None, True)


async def test_cap_on_er2_calls_stops_the_mode(session, er2):
    s, rec = session
    clock = Clock()
    loop = _loop(s, clock, max_calls=2)
    describe._ACTIVE[s.session_id] = loop
    for i in (1, 2):
        er2.feed({"objects": [], "confidence": 0.9})
        s.frames.add(_frame(i))
        assert await loop.step() is True
    s.frames.add(_frame(3))
    assert await loop.step() is False
    assert loop.calls == 2 and not loop.active and describe.active(s) is None
    assert _spoken(rec) == [t("describe.cap", "en")]


async def test_loop_stops_itself_when_the_session_is_gone(session):
    s, rec = session
    loop = _loop(s, Clock(), interval_s=0.0)
    describe._ACTIVE[s.session_id] = loop
    sessions.unregister(s)
    await loop.run()
    assert not loop.active and describe.active(s) is None
    sessions.register(s)                                                  # the fixture unregisters it again


async def test_no_frame_means_no_call_and_no_speech(session, er2):
    s, rec = session
    loop = _loop(s, Clock())
    assert await loop.step() is True
    assert loop.calls == 0 and _spoken(rec) == []


async def test_language_follows_the_session(mk_session, er2):
    s, rec = mk_session(lang="ta")
    sessions.register(s)
    try:
        loop = _loop(s, Clock())
        er2.feed({"objects": [_obj("table", x=500, distance="arm")], "confidence": 0.9})
        s.frames.add(_frame(1))
        await loop.step()
        assert _spoken(rec) == [t("describe.item", "ta", label="Table", side=t("cam.side.centre", "ta"),
                                  dist=t("cam.dist.arm", "ta"))]
        assert rec.msgs[0].lang == "ta"
    finally:
        sessions.unregister(s)
