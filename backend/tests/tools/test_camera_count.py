"""camera_ask counting: "how many X are visible?" asks the phone for TWO fresh count frames ~400 ms apart, reads
each once with ER 2 points (in parallel) and speaks a verified number only when the two independent reads agree (a
one-off difference gets a third read). One frame is one read, never verified. Ambient and describe loops stay quiet
while the owner's question is answered."""
import asyncio
import json

import pytest

from aira_live import sessions
from aira_live.contracts import RequestFrameMsg
from aira_live.frames import Frame
from aira_live.session import current_session
from aira_live.tools import camera, describe
from aira_live.vision.count import CountResult
from aira_live.vision.gemini import VisionError

TINY = "/9j/4AAQSkZJRg=="


def _frame(ts: float) -> Frame:
    return Frame(id=f"c{ts}", jpeg_b64=f"{TINY}{ts}", w=1024, h=768, ts=ts, purpose="count")


@pytest.fixture
def session(mk_session, monkeypatch):
    s, rec = mk_session(lang="en")
    calls: list[dict] = []
    s.n_frames = 2
    s.partial_asks = []

    async def fake_frames(purpose, count=1, interval_ms=0, timeout_s=4.0, partial=False):
        calls.append({"purpose": purpose, "count": count, "interval_ms": interval_ms, "timeout_s": timeout_s})
        s.partial_asks.append(partial)
        return [_frame(float(i + 1)) for i in range(min(count, s.n_frames))]

    monkeypatch.setattr(s, "request_frames", fake_frames)
    token = current_session.set(s)
    s.frame_calls = calls
    yield s, rec
    current_session.reset(token)


def _res(n, conf=1.0, hint="", **kw):
    return CountResult(count=n, confidence=conf, needs_retake=bool(hint) or n is None, aim_hint=hint,
                       objects=[], model="gemini-robotics-er-2", **kw)


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


# --- target from the model's arguments ------------------------------------------------------------------------


def test_count_target_comes_from_the_model_arguments():
    tgt = camera.count_target("curd packet", "Hatsun Curd")
    assert (tgt.generic, tgt.product, tgt.phrase) == ("curd packet", "Hatsun Curd", "Hatsun Curd packets")
    tgt = camera.count_target("milk packet")
    assert (tgt.generic, tgt.product, tgt.phrase) == ("milk packet", None, "milk packets")
    tgt = camera.count_target("curd pouch")
    assert (tgt.generic, tgt.product, tgt.phrase) == ("curd pouch", None, "curd pouches")


def test_count_target_without_arguments_counts_what_is_shown():
    tgt = camera.count_target()
    assert "item" in tgt.generic and tgt.product is None and tgt.phrase == ""
    tgt = camera.count_target("", "Hatsun Curd")
    assert tgt.generic == "Hatsun Curd packet" and tgt.product == "Hatsun Curd"


def test_the_question_words_are_never_parsed():
    assert not hasattr(camera, "classify") and not hasattr(camera, "COUNT_WORDS")
    import inspect
    assert list(inspect.signature(camera.count_target).parameters) == ["item", "product"]


async def test_a_how_many_question_without_kind_count_is_not_counted(session, monkeypatch):
    """The model decides: "how many" words alone never start a count (the code does not read them)."""
    seen = _counter(monkeypatch)

    async def look(role, parts, schema_hint, thinking="low", timeout_s=6.0, **_kw):
        return {"answer": "Curd packets on a shelf.", "guidance": [], "objects": [], "confidence": 0.9}

    monkeypatch.setattr(camera, "generate_json", look)
    out = await camera.camera_ask("how many curd packets?")
    assert seen == [] and out["kind"] == "look"


async def test_kind_count_counts_in_any_language_with_the_model_item(session, monkeypatch):
    seen = _counter(monkeypatch, _res(3), _res(3))
    out = await camera.camera_ask("தயிர் எவ்வளவு?", kind="count", item="curd packet")
    assert all(c["label"] == "curd packet" and c["product"] is None for c in seen)
    assert out["count"] == 3 and out["label"] == "curd packet"


# --- counting -------------------------------------------------------------------------------------------------


async def test_two_frames_read_once_each_that_agree_give_a_verified_number(session, monkeypatch):
    s, _ = session
    seen = _counter(monkeypatch, _res(4), _res(4))
    out = await camera.camera_ask("how many Hatsun Curd packets are visible?", kind="count", item="curd packet", product="Hatsun Curd")
    assert s.frame_calls == [{"purpose": "count", "count": 2, "interval_ms": camera.COUNT_INTERVAL_MS,
                              "timeout_s": camera.COUNT_FRAME_TIMEOUT_S}]
    assert camera.COUNT_INTERVAL_MS == 400
    assert [c["frame"] for c in seen] == ["c1.0", "c2.0"]  # two distinct frames, one read each
    assert all(c["label"] == "curd packet" and c["product"] == "Hatsun Curd" for c in seen)
    assert out["kind"] == "count" and out["count"] == 4 and out["agreed"] is True and out["verified"] is True
    assert out["approx"] is False and out["exact"] is True and out["sure"] is True
    assert out["label"] == "curd packet" and out["product"] == "Hatsun Curd"
    assert out["say_en"] == "I can see 4 Hatsun Curd packets in the current view." and "say" not in out


async def test_the_two_reads_of_the_one_frame_run_in_parallel(session, monkeypatch):
    running = {"now": 0, "max": 0}
    frames: list[str] = []

    async def slow(frame, label, product=None):
        frames.append(frame.id)
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return _res(3)

    monkeypatch.setattr(camera, "count_in_frame", slow)
    out = await camera.camera_ask("how many milk packets?", kind="count", item="milk packet")
    assert out["count"] == 3 and running["max"] == 2 and frames == ["c1.0", "c2.0"]


async def test_off_by_one_third_read_agreeing_with_the_other_frame_is_verified(session, monkeypatch):
    seen = _counter(monkeypatch, _res(4), _res(5), _res(4))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert len(seen) == 3 and seen[2]["frame"] == "c2.0"  # the newest frame, read again
    assert out["count"] == 4 and out["agreed"] is True and out["verified"] is True and out["exact"] is True


async def test_off_by_one_third_read_agreeing_only_with_its_own_frame_is_not_verified(session, monkeypatch):
    """Two reads of the same picture are not independent: their agreement is never passed off as verification."""
    seen = _counter(monkeypatch, _res(4), _res(5), _res(5))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert len(seen) == 3 and seen[2]["frame"] == "c2.0"
    assert out["count"] == 5 and out["verified"] is False and out["exact"] is False and out["approx"] is True
    assert out["agreed"] is False and out["say_en"].startswith("About 5")


async def test_off_by_one_with_a_third_read_that_matches_neither_is_not_sure(session, monkeypatch):
    _counter(monkeypatch, _res(4), _res(5), _res(7))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["count"] is None and out["sure"] is False and out["reason"] == "reads_disagree"


async def test_far_apart_counts_never_give_a_number(session, monkeypatch):
    seen = _counter(monkeypatch, _res(4), _res(7))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert len(seen) == 2
    assert out["count"] is None and out["agreed"] is False and out["reason"] == "reads_disagree"
    assert out["hint"] == "hold_still" and not any(ch.isdigit() for ch in out["say_en"])


async def test_low_confidence_never_gives_a_number(session, monkeypatch):
    _counter(monkeypatch, _res(6, conf=0.3, hint="vision.aim.spread_out"), _res(6, conf=0.3, hint="vision.aim.spread_out"))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["count"] is None and out["reason"] == "low_confidence" and out["hint"] == "spread_out"


async def test_nothing_seen_is_not_a_count(session, monkeypatch):
    _counter(monkeypatch, _res(None, conf=0.0, hint="vision.aim.show_items"),
             _res(None, conf=0.0, hint="vision.aim.show_items"))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["count"] is None and out["reason"] == "nothing_counted" and out["hint"] == "show_items"


async def test_a_failed_count_is_not_sure(session, monkeypatch):
    _counter(monkeypatch, _res(4), VisionError("timeout"))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["count"] is None and out["sure"] is False and out["reason"] == "vision_failed"
    assert out.get("approx") is False


async def test_one_frame_is_read_once_and_never_verified(session, monkeypatch):
    s, _ = session
    s.n_frames = 1
    seen = _counter(monkeypatch, _res(2), _res(2))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert [c["frame"] for c in seen] == ["c1.0"]  # one picture: one read, never the same image twice
    assert out["count"] == 2 and out["verified"] is False and out["exact"] is False and out["approx"] is True
    assert out["evidence"]["frames"] == 1 and out["evidence"]["reads"] == 1
    assert "one look" in out["say_en"]
    # The short burst is taken from THIS request's own frames (partial), never from the shared buffer.
    assert s.partial_asks == [True]


async def test_two_copies_of_the_same_picture_are_one_frame(session, monkeypatch):
    """The phone sending the same picture twice (same bytes) is not two frames: one read, unverified."""
    s, _ = session

    async def same_twice(purpose, count=1, interval_ms=0, timeout_s=4.0, partial=False):
        f = _frame(1.0)
        return [f, Frame(id="c-dup", jpeg_b64=f.jpeg_b64, w=f.w, h=f.h, ts=2.0, purpose="count")]

    monkeypatch.setattr(s, "request_frames", same_twice)
    seen = _counter(monkeypatch, _res(3), _res(3))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert len(seen) == 1 and out["verified"] is False and out["evidence"]["frames"] == 1


async def test_no_frames_says_no_camera(session, monkeypatch):
    s, _ = session
    s.n_frames = 0
    seen = _counter(monkeypatch)
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert seen == [] and out["reason"] == "no_camera_frame" and out["count"] is None


async def test_a_tamil_session_gets_the_same_facts(session, monkeypatch):
    """No fixed Tamil sentence: the number and the item go to Live, which says them in Tamil."""
    s, _ = session
    s.lang = "ta"
    _counter(monkeypatch, _res(3), _res(3))
    out = await camera.camera_ask("எத்தனை தயிர் பாக்கெட் இருக்கு?", kind="count", item="curd packet")
    assert out["count"] == 3 and out["approx"] is False and out["label"] == "curd packet"



def test_ask_frame_wait_is_six_seconds():
    assert camera.FRAME_TIMEOUT_S == 6.0


def test_count_waits_fifteen_seconds_for_two_frames():
    # a busy phone (Galaxy A22) took 4-13 s to encode one count photo: a longer wait for the two frames
    assert camera.COUNT_FRAME_TIMEOUT_S == 15.0 and camera.COUNT_FRAMES == 2


async def test_count_diag_has_counts_and_no_user_words(session, monkeypatch):
    logged: list[dict] = []
    monkeypatch.setattr(camera, "diag", lambda event, **kw: logged.append({"event": event, **kw}))
    _counter(monkeypatch, _res(4), _res(4))
    await camera.camera_ask("how many Hatsun Curd packets are visible?", kind="count", item="curd packet", product="Hatsun Curd")
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
    await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert during == [True, True] and s.owner_busy is False


async def test_owner_busy_is_released_when_vision_fails(session, monkeypatch):
    s, _ = session

    async def boom(role, parts, schema_hint, thinking="low", timeout_s=6.0, **_kw):
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
        await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
        msgs = [m for m in rec.msgs if isinstance(m, RequestFrameMsg) and m.purpose == "describe"]
        assert msgs[-2].stop is True and msgs[-1].continuous is True
    finally:
        await describe.stop_describe(s)
        sessions.unregister(s)
        describe._ACTIVE.clear()


# --- a full shelf: close reads give an "about" count -----------------------------------------------------------


async def test_a_full_shelf_with_close_reads_says_about_the_rounded_mean(session, monkeypatch):
    _counter(monkeypatch, _res(57, conf=0.45, hint="vision.aim.spread_out"), _res(60, conf=0.4, hint="vision.aim.spread_out"))
    out = await camera.camera_ask("how many curd packets are there?", kind="count", item="curd packet")
    assert out["count"] == 58 and out["approx"] is True and out["exact"] is False and out["agreed"] is False
    assert out["verified"] is False
    assert out["say_en"].startswith("About 58 curd packets in the current view.")


async def test_a_full_shelf_cut_at_the_edge_says_about_and_asks_to_step_back(session, monkeypatch):
    _counter(monkeypatch, _res(40, conf=0.6, hint="vision.aim.move_back"), _res(41, conf=0.6, hint="vision.aim.move_back"))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["count"] in (40, 41) and out["approx"] is True
    assert out["hint"] == "move_back"


async def test_a_full_shelf_with_far_apart_reads_is_still_not_sure(session, monkeypatch):
    _counter(monkeypatch, _res(40, conf=0.6), _res(60, conf=0.6))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["count"] is None and not any(ch.isdigit() for ch in out["say_en"])


async def test_a_full_shelf_with_very_low_confidence_is_not_sure(session, monkeypatch):
    _counter(monkeypatch, _res(50, conf=0.1), _res(50, conf=0.1))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["count"] is None


async def test_an_unreadable_label_or_a_capped_reply_never_gives_about_n(session, monkeypatch):
    """count.assess caps unreadable-label and item-cap reads at 0.4 with "hold closer" to block a number."""
    _counter(monkeypatch, _res(50, conf=0.4, hint="vision.aim.hold_closer"),
             _res(50, conf=0.4, hint="vision.aim.hold_closer"))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["count"] is None and out["approx"] is False and out["hint"] == "hold_closer"
    assert not any(ch.isdigit() for ch in out["say_en"] + out["facts"])


async def test_one_close_read_with_hold_closer_blocks_about_n(session, monkeypatch):
    _counter(monkeypatch, _res(50, conf=0.6, hint="vision.aim.spread_out"),
             _res(51, conf=0.4, hint="vision.aim.hold_closer"))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["count"] is None and out["approx"] is False


@pytest.mark.parametrize("second", [VisionError("timeout"), _res(None, conf=0.0, hint="vision.aim.hold_closer")])
async def test_a_failed_or_empty_read_never_gives_about_n(session, monkeypatch, second):
    _counter(monkeypatch, _res(50, conf=0.6, hint="vision.aim.spread_out"), second)
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["count"] is None and out["approx"] is False and out["sure"] is False


def test_about_guard_directly():
    assert camera._about(_res(50, 0.5, "vision.aim.spread_out"), _res(52, 0.5, "vision.aim.move_back")) == 51
    assert camera._about(_res(50, 0.4, "vision.aim.hold_closer"), _res(50, 0.4, "vision.aim.hold_closer")) is None
    assert camera._about(_res(50, 0.5, "vision.aim.show_items"), _res(50, 0.5)) is None
    assert camera._about(None, _res(50, 0.5)) is None
    assert camera._about(_res(None, 0.0), _res(50, 0.5)) is None


async def test_an_approx_count_is_marked_and_never_exact(session, monkeypatch):
    """Provenance: an "about N" result says approx, never exact, so nothing can treat it as a stock number."""
    _counter(monkeypatch, _res(57, conf=0.45, hint="vision.aim.spread_out"), _res(60, conf=0.4, hint="vision.aim.spread_out"))
    out = await camera.camera_ask("how many curd packets?", kind="count", item="curd packet")
    assert out["approx"] is True and out["exact"] is False
    assert "about" in out["say_en"].lower() and "about" in out["answer"].lower()


# --- result shape: what was seen now, with its evidence (never stock) -----------------------------------------


async def test_a_count_result_carries_scope_evidence_and_provenance(session, monkeypatch):
    _counter(monkeypatch, _res(3), _res(3))
    out = await camera.camera_ask("how many milk cartons?", kind="count", item="milk carton")
    assert out["visible_count"] == 3 == out["count"] and out["scope"] == "visible_now"
    assert out["source"] == "fresh_count" and out["observed_at"] == 2.0 and out["frame_ids"] == ["c1.0", "c2.0"]
    ev = out["evidence"]
    assert ev == {"frames": 2, "reads": 2, "agreed": True, "unknown": 0, "hidden": 0, "edge": 0}
    assert out["say_en"] == "I can see 3 milk cartons in the current view."
    assert "stock" not in out["say_en"].lower()


async def test_a_not_sure_count_carries_the_same_fields(session, monkeypatch):
    _counter(monkeypatch, _res(4), _res(7))
    out = await camera.camera_ask("how many milk cartons?", kind="count", item="milk carton")
    assert out["visible_count"] is None and out["scope"] == "visible_now" and out["verified"] is False
    assert out["evidence"]["frames"] == 2 and out["evidence"]["agreed"] is False and out["frame_ids"]


async def test_no_frame_count_carries_the_fields(session, monkeypatch):
    s, _ = session
    s.n_frames = 0
    _counter(monkeypatch)
    out = await camera.camera_ask("how many milk cartons?", kind="count", item="milk carton")
    assert out["verified"] is False and out["scope"] == "visible_now" and out["evidence"]["frames"] == 0
    assert out["frame_ids"] == [] and out["observed_at"] is None


async def test_a_partly_hidden_item_is_said_and_asks_for_a_closer_look(session, monkeypatch):
    _counter(monkeypatch, _res(2, conf=0.7, hint="vision.aim.spread_out", hidden=1),
             _res(2, conf=0.7, hint="vision.aim.spread_out", hidden=1))
    out = await camera.camera_ask("how many milk cartons?", kind="count", item="milk carton")
    assert out["count"] == 2 and out["evidence"]["hidden"] == 1
    assert out["say_en"].startswith("I can clearly identify 2 milk cartons, but")
    assert "partly hidden" in out["say_en"] and "show me that part of the shelf" in out["say_en"]


async def test_unreadable_labels_make_a_product_count_uncertain_and_are_reported(session, monkeypatch):
    _counter(monkeypatch, _res(2, conf=0.4, hint="vision.aim.hold_closer", unknown=1),
             _res(2, conf=0.4, hint="vision.aim.hold_closer", unknown=1))
    out = await camera.camera_ask("how many Hatsun curd?", kind="count", item="curd packet", product="Hatsun Curd")
    assert out["count"] is None and out["sure"] is False and out["evidence"]["unknown"] == 1
    assert "label" in out["say_en"] and not any(ch.isdigit() for ch in out["say_en"])


async def test_a_count_is_remembered_as_a_scene_fact_and_a_recount_shows_the_previous(session, monkeypatch):
    s, _ = session
    _counter(monkeypatch, _res(3), _res(3), _res(4), _res(4))
    first = await camera.camera_ask("how many milk cartons?", kind="count", item="milk carton")
    assert first.get("previous") is None
    fact = s.scene_facts.previous("milk cartons")
    assert fact["count"] == 3 and fact["verified"] is True and fact["source"] == "fresh_count"
    again = await camera.camera_ask("are you sure?", kind="count", item="milk carton")
    assert again["count"] == 4 and again["source"] == "fresh_count"   # a recount is fresh, never the cache
    assert again["previous"]["count"] == 3 and "age_s" in again["previous"]
