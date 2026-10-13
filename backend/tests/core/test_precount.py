"""Pre-count: a background count per settled scene, served at once for "how many", dropped when the view changes."""
import asyncio

import pytest

from aira_live.frames import Frame
from aira_live.vision import precount
from aira_live.vision.count import CountResult


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(precount, "_monotonic", c)
    return c


def _result(labels, conf=0.9):
    objs = [{"label": lb, "point": [100 * i, 100], "box": None} for i, lb in enumerate(labels)]
    return CountResult(count=len(labels), confidence=conf, needs_retake=False, aim_hint="", objects=objs)


class FakeCounter:
    def __init__(self, result, gate: asyncio.Event | None = None):
        self.result, self.gate, self.calls = result, gate, []

    async def __call__(self, frame, label):
        self.calls.append((frame.id, label))
        if self.gate is not None:
            await self.gate.wait()
        return self.result


def _session(mk_session):
    s, _ = mk_session()
    n = {"i": 0}

    async def request_frames(purpose, count=1, interval_ms=0, timeout_s=4.0):
        n["i"] += 1
        assert purpose == "count"
        return [Frame(id=f"c{n['i']}", jpeg_b64="x", w=1280, h=720, ts=float(n["i"]), purpose="count")]

    s.request_frames = request_frames
    return s


async def _settle():
    for _ in range(10):
        await asyncio.sleep(0)


async def test_a_settled_scene_is_counted_once_and_served_at_once(mk_session, clock):
    s = _session(mk_session)
    counter = FakeCounter(_result(["Hatsun curd", "Hatsun curd", "Arokya curd"]))
    precount.scene_changed(s, 10.0, counter=counter)
    await _settle()
    assert counter.calls == [("c1", "packet")]
    assert s.cached_count() == (3, 0.9, 0.0)
    assert s.cached_count("hatsun")[0] == 2 and s.cached_count("Arokya curd")[0] == 3  # "curd" matches both
    assert s.cached_count("aavin milk") is None  # labels of other products: not this one
    out = await precount.answer(s, "count", item="packet", product="Hatsun")
    assert out["count"] == 2 and out["kind"] == "count" and out["approx"] is False and out["product"] == "Hatsun"
    assert "2" in out["say_en"] and "say" not in out
    assert await precount.answer(s, "look") is None          # served only when the model asked for a count
    assert await precount.answer(s, "read", item="packet") is None
    precount.forget(s.session_id)


async def test_a_scene_change_invalidates_the_count(mk_session, clock):
    s = _session(mk_session)
    precount.scene_changed(s, 10.0, counter=FakeCounter(_result(["packet"] * 4)))
    await _settle()
    assert s.cached_count("hatsun") == (4, 0.9, 0.0)  # generic labels only: the total answers
    clock.t += 1.0  # inside the start gap: the new scene waits for the next live frame
    precount.scene_changed(s, 11.0, counter=FakeCounter(_result(["packet"] * 7)))
    assert s.cached_count() is None
    clock.t += precount.MIN_GAP_S
    precount.tick(s, counter=FakeCounter(_result(["packet"] * 7)))
    await _settle()
    assert s.cached_count()[0] == 7
    precount.forget(s.session_id)


async def test_one_count_in_flight_and_answer_waits_for_it(mk_session, clock):
    s = _session(mk_session)
    gate = asyncio.Event()
    first = FakeCounter(_result(["packet"] * 2), gate)
    precount.scene_changed(s, 10.0, counter=first)
    await _settle()
    waiting = asyncio.create_task(precount.answer(s, "count", item="packet", wait_s=5))
    await _settle()
    assert not waiting.done()
    gate.set()
    assert (await waiting)["count"] == 2
    precount.forget(s.session_id)


async def test_a_count_whose_scene_changed_is_dropped_and_the_new_scene_goes_next(mk_session, clock):
    s = _session(mk_session)
    gate = asyncio.Event()
    counter = FakeCounter(_result(["packet"] * 2), gate)
    precount.scene_changed(s, 10.0, counter=counter)
    await _settle()
    precount.scene_changed(s, 12.0, counter=counter)  # while counting: queued, not a second call
    await _settle()
    assert len(counter.calls) == 1
    gate.set()
    await _settle()
    assert len(counter.calls) == 2  # the wanted scene was counted right after
    assert s.cached_count()[0] == 2 and precount._STATE[s.session_id].counted_scene == 12.0
    precount.forget(s.session_id)


async def test_unusable_or_old_counts_are_never_served(mk_session, clock):
    s = _session(mk_session)
    bad = CountResult(count=None, confidence=0.0, needs_retake=True, aim_hint="vision.aim.move_back")
    precount.scene_changed(s, 10.0, counter=FakeCounter(bad))
    await _settle()
    assert s.cached_count() is None and await precount.answer(s, "count") is None
    clock.t += precount.MIN_GAP_S
    precount.scene_changed(s, 11.0, counter=FakeCounter(_result(["packet"])))
    await _settle()
    clock.t += precount.MAX_AGE_S + 1
    assert s.cached_count() is None
    precount.forget(s.session_id)


async def test_camera_ask_says_the_precount_without_asking_the_phone(mk_session, clock):
    from aira_live.session import current_session
    from aira_live.tools import camera

    s = _session(mk_session)
    precount.scene_changed(s, 10.0, counter=FakeCounter(_result(["packet"] * 5)))
    await _settle()

    async def no_frames(*a, **k):
        raise AssertionError("the phone must not be asked")

    s.request_frames = no_frames
    token = current_session.set(s)
    try:
        out = await camera.camera_ask("how many packets on this shelf?", kind="count", item="packet")
    finally:
        current_session.reset(token)
    assert out["count"] == 5 and out["precounted"] is True
    precount.forget(s.session_id)


def test_no_count_word_list_is_left():
    assert not hasattr(precount, "is_count_question") and not hasattr(precount, "COUNT_WORDS")


async def test_camera_ask_serves_the_precount_only_for_kind_count(mk_session, clock, monkeypatch):
    """A "how many" question the model sent as a look is a look: the pre-count is never served by words."""
    from aira_live.session import current_session
    from aira_live.tools import camera

    s = _session(mk_session)
    precount.scene_changed(s, 10.0, counter=FakeCounter(_result(["packet"] * 5)))
    await _settle()

    async def look(role, parts, schema_hint, thinking="low", timeout_s=6.0, **_kw):
        return {"answer": "A shelf.", "guidance": [], "objects": [], "confidence": 0.9}

    async def frames(*a, **k):
        from aira_live.frames import Frame
        return [Frame(id="f1", jpeg_b64="/9j/4AAQSkZJRg==", w=640, h=480, ts=1.0, purpose="ask")]

    monkeypatch.setattr(camera, "generate_json", look)
    s.request_frames = frames
    token = current_session.set(s)
    try:
        out = await camera.camera_ask("எத்தனை பாக்கெட்?")
        assert out["kind"] == "look" and not out.get("precounted")
        out = await camera.camera_ask("எத்தனை பாக்கெட்?", kind="count", item="packet")
        assert out["precounted"] is True and out["count"] == 5
    finally:
        current_session.reset(token)
    precount.forget(s.session_id)
