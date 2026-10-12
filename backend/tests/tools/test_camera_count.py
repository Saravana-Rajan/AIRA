"""camera_ask counting: "how many X are visible?" counts with ER 2 points on two frames (in parallel) and speaks a
number only when they agree (a one-off difference gets a third read). Ambient and describe loops stay quiet while
the owner's question is answered."""
import asyncio
import json

import pytest

from aira_live import sessions
from aira_live.contracts import RequestFrameMsg
from aira_live.frames import Frame
from aira_live.i18n import t
from aira_live.session import current_session
from aira_live.tools import camera, describe
from aira_live.vision.count import CountResult
from aira_live.vision.gemini import VisionError

TINY = "/9j/4AAQSkZJRg=="


def _frame(ts: float) -> Frame:
    return Frame(id=f"c{ts}", jpeg_b64=TINY, w=1280, h=960, ts=ts, purpose="count")


@pytest.fixture
def session(mk_session, monkeypatch):
    s, rec = mk_session(lang="en")
    calls: list[dict] = []
    s.n_frames = 2

    async def fake_frames(purpose, count=1, interval_ms=0, timeout_s=4.0):
        calls.append({"purpose": purpose, "count": count, "interval_ms": interval_ms, "timeout_s": timeout_s})
        return [_frame(float(i + 1)) for i in range(min(count, s.n_frames))]

    monkeypatch.setattr(s, "request_frames", fake_frames)
    token = current_session.set(s)
    s.frame_calls = calls
    yield s, rec
    current_session.reset(token)


def _res(n, conf=1.0, hint=""):
    return CountResult(count=n, confidence=conf, needs_retake=bool(hint) or n is None, aim_hint=hint,
                       objects=[], model="gemini-robotics-er-2")


def _counter(monkeypatch, *results):
    seen: list[dict] = []
    items = list(results)

    async def fake(frame, label, product=None):
        seen.append({"frame": frame.id, "label": label, "product": product})
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(camera, "count_in_frame", fake)
    return seen


# --- classification and target --------------------------------------------------------------------------------


@pytest.mark.parametrize("q", [
    "how many Hatsun Curd packets are visible?", "How many milk packets do you see", "count the curd packets",
    "can you count these", "எத்தனை தயிர் பாக்கெட் இருக்கு?", "பாக்கெட்டுகளை எண்ணு", "कितने दूध के पैकेट हैं?",
    "कितनी थैलियाँ हैं", "पैकेट गिनो",
])
def test_counting_questions_are_kind_count(q):
    assert camera.classify(q) == "count"


@pytest.mark.parametrize("q", ["what's on the counter?", "what's in front of me?", "read this label",
                               "is my path clear?"])
def test_other_questions_are_not_count(q):
    assert camera.classify(q) != "count"


def test_count_target_from_an_english_question():
    tgt = camera.count_target("how many Hatsun Curd packets are visible?")
    assert (tgt.generic, tgt.product, tgt.phrase) == ("curd packet", "Hatsun Curd", "Hatsun Curd packets")
    tgt = camera.count_target("How many milk packets do you see")
    assert (tgt.generic, tgt.product, tgt.phrase) == ("milk packet", None, "milk packets")
    tgt = camera.count_target("count the curd pouches in front of me")
    assert (tgt.generic, tgt.product) == ("curd pouch", None)


def test_count_target_without_a_name_or_in_another_language_hints_with_the_question():
    q = "எத்தனை தயிர் பாக்கெட் இருக்கு?"
    tgt = camera.count_target(q)
    assert q in tgt.generic and tgt.product is None and tgt.phrase == ""
    tgt = camera.count_target("count them")
    assert "item" in tgt.generic and tgt.product is None and tgt.phrase == ""


# --- counting -------------------------------------------------------------------------------------------------


async def test_two_frames_that_agree_speak_the_number(session, monkeypatch):
    s, _ = session
    seen = _counter(monkeypatch, _res(4), _res(4))
    out = await camera.camera_ask("how many Hatsun Curd packets are visible?")
    assert s.frame_calls == [{"purpose": "count", "count": 2, "interval_ms": 400, "timeout_s": camera.FRAME_TIMEOUT_S}]
    assert [c["frame"] for c in seen] == ["c1.0", "c2.0"]
    assert all(c["label"] == "curd packet" and c["product"] == "Hatsun Curd" for c in seen)
    assert out["kind"] == "count" and out["count"] == 4 and out["agreed"] is True
    assert out["say"] == t("cam.count_label", "en", n=4, label="Hatsun Curd packets")


async def test_frames_are_counted_in_parallel(session, monkeypatch):
    running = {"now": 0, "max": 0}

    async def slow(frame, label, product=None):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return _res(3)

    monkeypatch.setattr(camera, "count_in_frame", slow)
    out = await camera.camera_ask("how many milk packets?")
    assert out["count"] == 3 and running["max"] == 2


async def test_off_by_one_takes_a_third_read_that_breaks_the_tie(session, monkeypatch):
    seen = _counter(monkeypatch, _res(4), _res(5), _res(5))
    out = await camera.camera_ask("how many curd packets?")
    assert len(seen) == 3 and seen[2]["frame"] == "c2.0"
    assert out["count"] == 5 and out["agreed"] is True


async def test_off_by_one_with_a_third_read_that_matches_neither_is_not_sure(session, monkeypatch):
    _counter(monkeypatch, _res(4), _res(5), _res(7))
    out = await camera.camera_ask("how many curd packets?")
    assert out["count"] is None and out["say"] == t("cam.count_unsure", "en")


async def test_far_apart_counts_never_give_a_number(session, monkeypatch):
    seen = _counter(monkeypatch, _res(4), _res(7))
    out = await camera.camera_ask("how many curd packets?")
    assert len(seen) == 2
    assert out["count"] is None and out["agreed"] is False
    assert out["say"] == t("cam.count_unsure", "en")
    assert not any(ch.isdigit() for ch in out["say"])


async def test_low_confidence_never_gives_a_number(session, monkeypatch):
    _counter(monkeypatch, _res(6, conf=0.3, hint="vision.aim.spread_out"), _res(6, conf=0.3, hint="vision.aim.spread_out"))
    out = await camera.camera_ask("how many curd packets?")
    assert out["count"] is None
    assert t("cam.count_unsure", "en") in out["say"]


async def test_nothing_seen_is_not_a_count(session, monkeypatch):
    _counter(monkeypatch, _res(None, conf=0.0, hint="vision.aim.show_items"),
             _res(None, conf=0.0, hint="vision.aim.show_items"))
    out = await camera.camera_ask("how many curd packets?")
    assert out["count"] is None and t("cam.count_unsure", "en") in out["say"]


async def test_a_failed_count_is_not_sure(session, monkeypatch):
    _counter(monkeypatch, _res(4), VisionError("timeout"))
    out = await camera.camera_ask("how many curd packets?")
    assert out["count"] is None and out["say"] == t("cam.count_unsure", "en")


async def test_one_frame_is_counted_twice(session, monkeypatch):
    s, _ = session
    s.n_frames = 1
    seen = _counter(monkeypatch, _res(2), _res(2))
    out = await camera.camera_ask("how many curd packets?")
    assert [c["frame"] for c in seen] == ["c1.0", "c1.0"] and out["count"] == 2


async def test_no_frames_says_no_camera(session, monkeypatch):
    s, _ = session
    s.n_frames = 0
    seen = _counter(monkeypatch)
    out = await camera.camera_ask("how many curd packets?")
    assert seen == [] and out["say"] == t("camera.none", "en")


async def test_tamil_count_answers_in_tamil(session, monkeypatch):
    s, _ = session
    s.lang = "ta"
    _counter(monkeypatch, _res(3), _res(3))
    out = await camera.camera_ask("எத்தனை தயிர் பாக்கெட் இருக்கு?")
    assert out["say"] == t("cam.count", "ta", n=3)


def test_count_keys_exist_in_every_language():
    for lang in ("en", "ta", "hi"):
        for key in ("cam.count", "cam.count_label", "cam.count_unsure"):
            assert t(key, lang) != key


def test_ask_frame_wait_is_six_seconds():
    assert camera.FRAME_TIMEOUT_S == 6.0


async def test_count_diag_has_counts_and_no_user_words(session, monkeypatch):
    logged: list[dict] = []
    monkeypatch.setattr(camera, "diag", lambda event, **kw: logged.append({"event": event, **kw}))
    _counter(monkeypatch, _res(4), _res(4))
    await camera.camera_ask("how many Hatsun Curd packets are visible?")
    done = [d for d in logged if d.get("stage") == "count"]
    assert done and done[0]["kind"] == "count" and done[0]["counts"] == [4, 4] and done[0]["agreed"] is True
    assert done[0]["models"] == ["gemini-robotics-er-2", "gemini-robotics-er-2"] and "ms" in done[0]
    assert "Hatsun" not in json.dumps(logged, default=str)


# --- owner questions come first --------------------------------------------------------------------------------


async def test_session_is_owner_busy_while_a_question_runs(session, monkeypatch):
    s, _ = session
    during: list[bool] = []

    async def fake(frame, label, product=None):
        during.append(s.owner_busy)
        return _res(2)

    monkeypatch.setattr(camera, "count_in_frame", fake)
    assert s.owner_busy is False
    await camera.camera_ask("how many curd packets?")
    assert during == [True, True] and s.owner_busy is False


async def test_owner_busy_is_released_when_vision_fails(session, monkeypatch):
    s, _ = session

    async def boom(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        assert s.owner_busy
        raise VisionError("x")

    monkeypatch.setattr(camera, "generate_json", boom)
    await camera.camera_ask("what's in front of me?")
    assert s.owner_busy is False


async def test_describe_loop_skips_frames_while_the_owner_asks(mk_session, monkeypatch):
    s, _ = mk_session(lang="en")
    sessions.register(s)
    try:
        calls: list = []

        async def fake(*a, **kw):
            calls.append(1)
            return {"objects": [], "confidence": 0.9}

        monkeypatch.setattr(describe, "generate_json", fake)
        loop = describe.DescribeLoop(s, busy=lambda: False)
        s.frames.add(Frame(id="d1", jpeg_b64=TINY, w=640, h=480, ts=1.0, purpose="describe"))
        with s.owner_asking():
            assert loop.busy() is True
            await loop.step()
        assert calls == []
        assert loop.busy() is False
    finally:
        sessions.unregister(s)
        describe._ACTIVE.clear()


async def test_holdup_loop_waits_while_the_owner_asks(mk_session):
    from aira_live.tools import ambient

    s, _ = mk_session(lang="en")
    loop = ambient.HoldUpLoop(s, busy=lambda: False)
    with s.owner_asking():
        assert loop.busy() is True
    assert loop.busy() is False


async def test_describe_frames_pause_on_the_phone_during_a_question(session, monkeypatch):
    s, rec = session
    sessions.register(s)
    try:
        await describe.start(s, run=False)
        _counter(monkeypatch, _res(2), _res(2))
        await camera.camera_ask("how many curd packets?")
        msgs = [m for m in rec.msgs if isinstance(m, RequestFrameMsg) and m.purpose == "describe"]
        assert msgs[-2].stop is True and msgs[-1].continuous is True
    finally:
        await describe.stop_describe(s)
        sessions.unregister(s)
        describe._ACTIVE.clear()
