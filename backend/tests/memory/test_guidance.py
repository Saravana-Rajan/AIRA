"""Task 4 (sarmitha/03): hand-guidance loop, product_find, and the client-event hooks main.py calls."""
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aira_live.business import seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.frames import Frame
from aira_live.tools import guidance, products
from aira_live.vision.gemini import VisionError
from aira_live.vision.identify import IdentifyResult, ProductRef
from aira_live.vision.point import TargetResult

SUNRISE_CATALOG = Path(__file__).resolve().parents[3] / "shared" / "catalog" / "sunrise_dairy.json"

CURD = ProductRef(sku="sakthi_curd_1l", name="Sakthi curd", brand="Sakthi", pack="1 L")
MILK = ProductRef(sku="aavin_milk_500ml", name="Aavin milk", brand="Aavin", pack="500 ml")
CANDS = [CURD, MILK]
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


@pytest.fixture
def spoken(monkeypatch):
    """Records ``(text, cue)`` of every notify; ``spoken.urgent`` the texts that were urgent."""
    class Said(list):
        urgent: list = []

    said = Said()
    said.urgent = []

    async def notify(shop_id, text, cue="ack", urgent=False):
        said.append((text, cue))
        if urgent:
            said.urgent.append(text)
        return 1

    monkeypatch.setattr(guidance, "notify", notify)
    return said


@pytest.fixture(autouse=True)
def clean_registry():
    guidance._ACTIVE.clear()
    yield
    guidance._ACTIVE.clear()


@pytest.fixture
def no_run(monkeypatch):
    """product_find tests check what starts, not the background loop."""
    async def run(self):
        return None

    monkeypatch.setattr(guidance.GuidanceLoop, "run", run)


@pytest.fixture
def find(fake_db, session, spoken, no_run, monkeypatch):
    """product_find wired to a fake resolver over the CURD/MILK products of shop "demo"."""
    fake_db.store[("shops", "demo", "products", "sakthi_curd_1l")] = {
        "name": "Sakthi curd", "brand": "Sakthi", "unit": "pack", "packSizeL": 1.0}
    fake_db.store[("shops", "demo", "products", "aavin_milk_500ml")] = {
        "name": "Aavin milk", "brand": "Aavin", "unit": "pack", "pack": "500 ml"}
    monkeypatch.setattr(products.spatial, "_utcnow", lambda: NOW)
    monkeypatch.setattr(products, "get_session", lambda: session)

    async def resolve(shop_id, spoken_name, lang):
        return {"curd": CURD, "milk": MILK}.get(spoken_name.lower())

    monkeypatch.setattr(products, "catalog_resolve", resolve)
    return session


def _frame(fid, ts, purpose="guide"):
    return Frame(id=fid, jpeg_b64="x", w=640, h=480, ts=ts, purpose=purpose)


def _location(fake_db, sku, confirmed_by="user", at=NOW, zone="fridge2/middle/left", shop="demo"):
    fake_db.store[("shops", shop, "locations", sku)] = {
        "sku": sku, "zone": zone, "confirmedBy": confirmed_by, "confirmedAt": at, "confidence": 1.0}


# --- start / step ------------------------------------------------------------------------------------------------


async def test_start_requests_continuous_guide_frames(session, fake_db, spoken):
    loop = await guidance.start(session, CURD, 2, CANDS, run=False)
    msg = session.sent[-1]
    assert msg.t == "request_frame" and msg.purpose == "guide" and msg.continuous is True
    assert guidance.active(session) is loop and loop.needed == 2 and loop.picked == 0


async def test_step_sends_target_for_confident_point(session, fake_db, spoken, monkeypatch):
    async def point(frame, label):
        assert label == "Sakthi curd 1 L"
        return TargetResult(label=label, point=(412, 530), box=(390, 505, 435, 555), confidence=0.8)

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    assert await loop.step() is True
    tgt = session.sent[-1]
    assert tgt.t == "target" and tuple(tgt.point) == (412, 530) and tgt.frame_id == "f1"
    assert tuple(tgt.box) == (390, 505, 435, 555) and tgt.confidence == 0.8 and tgt.label == "Sakthi curd"


async def test_step_uses_newest_frame_and_skips_low_confidence(session, fake_db, spoken, monkeypatch):
    seen = []

    async def point(frame, label):
        seen.append(frame.id)
        return TargetResult(label=label, point=(10, 10), box=None, confidence=0.1)

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    for i in range(3):
        session.frames.add(_frame(f"f{i}", float(i + 1)))
    await loop.step()
    assert seen == ["f2"]
    assert all(m.t != "target" for m in session.sent)


async def test_step_ignores_frames_of_other_purposes(session, fake_db, spoken, monkeypatch):
    seen = []

    async def point(frame, label):
        seen.append(frame.id)

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, frame_timeout_s=0.01, run=False)
    session.frames.add(_frame("c1", 5.0, purpose="count"))
    await loop.step()
    assert seen == []


async def test_step_does_not_reuse_an_old_frame(session, fake_db, spoken, monkeypatch):
    seen = []

    async def point(frame, label):
        seen.append(frame.id)

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, frame_timeout_s=0.01, run=False)
    session.frames.add(_frame("f1", 10.0))
    await loop.step()
    await loop.step()
    assert seen == ["f1"]


async def test_step_announces_lost_once(session, fake_db, spoken, monkeypatch):
    async def point(frame, label):
        return None

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, frame_timeout_s=0.01, run=False)
    for i in range(5):
        session.frames.add(_frame(f"f{i}", float(i + 1)))
        await loop.step()
    lost = [s for s in spoken if s[0] == "I can't see the Sakthi curd. Turn slowly to the left."]
    assert len(lost) == 1 and lost[0][1] == "unsure"
    assert len(spoken) == 1 and spoken.urgent == [lost[0][0]]   # a can't-see line cuts in


async def test_lost_is_announced_again_after_a_hit(session, fake_db, spoken, monkeypatch):
    replies = iter([None, None, None, 0.9, None, None, None])

    async def point(frame, label):
        conf = next(replies)
        return None if conf is None else TargetResult(label=label, point=(1, 1), box=None, confidence=conf)

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, frame_timeout_s=0.01, run=False)
    for i in range(7):
        session.frames.add(_frame(f"f{i}", float(i + 1)))
        await loop.step()
    assert [c for _, c in spoken] == ["unsure", "unsure"]


async def test_no_frames_counts_as_lost(session, fake_db, spoken):
    loop = await guidance.start(session, CURD, 1, CANDS, frame_timeout_s=0.01, run=False)
    for _ in range(4):
        await loop.step()
    assert spoken == [("I can't see the Sakthi curd. Turn slowly to the left.", "unsure")]


async def test_step_vision_error_counts_as_a_miss(session, fake_db, spoken, monkeypatch):
    async def point(frame, label):
        raise VisionError("all models failed")

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, frame_timeout_s=0.01, run=False)
    for i in range(3):
        session.frames.add(_frame(f"f{i}", float(i + 1)))
        assert await loop.step() is True
    assert all(m.t != "target" for m in session.sent)
    assert [c for _, c in spoken] == ["unsure"]


async def test_step_after_stop_does_nothing(session, fake_db, spoken):
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    await guidance.stop_guidance(session)
    sent = len(session.sent)
    assert await loop.step() is False
    assert len(session.sent) == sent and spoken == []


async def test_late_point_result_after_stop_sends_no_target(session, fake_db, spoken, monkeypatch):
    gate = asyncio.Event()

    async def point(frame, label):
        await gate.wait()
        return TargetResult(label=label, point=(5, 5), box=None, confidence=0.9)

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    stepping = asyncio.create_task(loop.step())
    await asyncio.sleep(0.01)
    await guidance.stop_guidance(session)
    gate.set()
    assert await stepping is False
    assert all(m.t != "target" for m in session.sent)


# --- the background run loop ---------------------------------------------------------------------------------------


async def test_run_loop_sends_targets_for_new_frames(session, fake_db, spoken, monkeypatch):
    async def point(frame, label):
        return TargetResult(label=label, point=(100, 200), box=None, confidence=0.7)

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, interval_s=0.0, frame_timeout_s=0.5)
    session.frames.add(_frame("f1", 1.0))
    for _ in range(100):
        if any(m.t == "target" for m in session.sent):
            break
        await asyncio.sleep(0.005)
    await guidance.stop_guidance(session)
    targets = [m for m in session.sent if m.t == "target"]
    assert [m.frame_id for m in targets] == ["f1"]
    await asyncio.sleep(0)
    assert loop.task.done()


async def test_run_survives_a_broken_step(session, fake_db, spoken, monkeypatch):
    calls = []

    async def point(frame, label):
        calls.append(frame.id)
        if len(calls) == 1:
            raise RuntimeError("unexpected")
        return TargetResult(label=label, point=(1, 2), box=None, confidence=0.9)

    monkeypatch.setattr(guidance, "point_to", point)
    await guidance.start(session, CURD, 1, CANDS, interval_s=0.0, frame_timeout_s=0.5)
    session.frames.add(_frame("f1", 1.0))
    for _ in range(50):
        if calls:
            break
        await asyncio.sleep(0.005)
    session.frames.add(_frame("f2", 2.0))
    for _ in range(100):
        if any(m.t == "target" for m in session.sent):
            break
        await asyncio.sleep(0.005)
    await guidance.stop_guidance(session)
    assert [m.frame_id for m in session.sent if m.t == "target"] == ["f2"]


async def test_guidance_stops_itself_after_max_time(session, fake_db, spoken, monkeypatch):
    async def point(frame, label):
        return None

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, interval_s=0.0, frame_timeout_s=0.01, max_s=0.05)
    await asyncio.wait_for(loop.task, 1.0)
    assert loop.active is False and guidance.active(session) is None
    assert spoken[-1] == ("I stopped guiding. Ask me again when you are ready.", "warn")
    assert spoken[-1][0] not in spoken.urgent   # stopping is not a safety line
    assert session.sent[-1].t == "request_frame" and session.sent[-1].stop is True


async def test_timeout_does_not_stop_a_newer_guidance(session, fake_db, spoken):
    old = await guidance.start(session, CURD, 1, CANDS, run=False)
    new = await guidance.start(session, MILK, 1, CANDS, run=False)
    await old._finish()
    assert guidance.active(session) is new and new.active


async def test_run_task_is_cancelled_on_stop(session, fake_db, spoken, monkeypatch):
    async def point(frame, label):
        return None

    monkeypatch.setattr(guidance, "point_to", point)
    loop = await guidance.start(session, CURD, 1, CANDS, interval_s=0.01, frame_timeout_s=0.01)
    await asyncio.sleep(0.05)
    await guidance.stop_guidance(session)
    await asyncio.sleep(0)
    assert loop.task.done()


# --- touch verification ------------------------------------------------------------------------------------------


async def test_touch_correct_then_done(session, fake_db, spoken, monkeypatch):
    async def ident(frame, candidates):
        assert candidates == CANDS
        return IdentifyResult(sku="sakthi_curd_1l", confidence=0.9, alternatives=[])

    monkeypatch.setattr(guidance, "identify_product", ident)
    loop = await guidance.start(session, CURD, 2, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    out = await loop.on_touch()
    assert spoken[-1] == ("Sakthi curd 1 L ✓. Take 1 more.", "match")
    assert out == {"verdict": "match", "item": "Sakthi curd 1 L", "sku": "sakthi_curd_1l", "picked": 1, "needed": 2,
                   "remaining": 1, "say_en": "Sakthi curd 1 L ✓. Take 1 more."}
    out = await loop.on_touch()
    assert spoken[-1] == ("Sakthi curd 1 L ✓. That's all 2.", "match")
    assert out["verdict"] == "match" and out["picked"] == 2 and out["remaining"] == 0 and out["done"] is True
    assert "say" not in out and spoken.urgent == []
    assert guidance.active(session) is None
    stop = session.sent[-1]
    assert stop.t == "request_frame" and stop.purpose == "guide" and stop.stop is True
    events = [v for k, v in fake_db.store.items() if k[2] == "memoryEvents"]
    assert [(e["type"], e["refId"]) for e in events] == [("pick", "sakthi_curd_1l")]


async def test_touch_wrong_product_is_not_counted(session, fake_db, spoken, monkeypatch):
    async def ident(frame, candidates):
        return IdentifyResult(sku="aavin_milk_500ml", confidence=0.95, alternatives=[])

    monkeypatch.setattr(guidance, "identify_product", ident)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    out = await loop.on_touch()
    assert spoken[-1] == ("This is Aavin milk 500 ml, not Sakthi curd 1 L.", "mismatch")
    assert out["verdict"] == "mismatch" and out["got"] == "Aavin milk 500 ml" and out["got_sku"] == "aavin_milk_500ml"
    assert out["item"] == "Sakthi curd 1 L" and out["picked"] == 0 and out["confidence"] == 0.95
    assert loop.picked == 0 and guidance.active(session) is loop


async def test_touch_low_confidence_says_not_sure(session, fake_db, spoken, monkeypatch):
    async def ident(frame, candidates):
        return IdentifyResult(sku="sakthi_curd_1l", confidence=0.4, alternatives=["aavin_milk_500ml"])

    monkeypatch.setattr(guidance, "identify_product", ident)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    out = await loop.on_touch()
    assert spoken[-1] == ("Not sure. Hold it closer to the camera.", "unsure")
    assert out["verdict"] == "unsure" and out["aim_hint"] == "hold_closer" and out["confidence"] == 0.4
    assert loop.picked == 0 and out["picked"] == 0


async def test_touch_look_alike_unsure_asks_for_the_label(session, fake_db, spoken, monkeypatch):
    curd_small = ProductRef(sku="sakthi_curd_500ml", name="Sakthi curd", brand="Sakthi", pack="500 ml")

    async def ident(frame, candidates):
        return IdentifyResult(sku=None, confidence=0.3, alternatives=["sakthi_curd_1l", "sakthi_curd_500ml"])

    monkeypatch.setattr(guidance, "identify_product", ident)
    loop = await guidance.start(session, CURD, 1, [CURD, curd_small, MILK], run=False)
    session.frames.add(_frame("f1", 10.0))
    out = await loop.on_touch()
    assert spoken[-1] == ("Not sure. Turn the pack so the label faces the camera.", "unsure")
    assert out["verdict"] == "unsure" and out["aim_hint"] == "show_label"
    assert loop.picked == 0


async def test_touch_unknown_sku_is_not_sure_never_a_raw_id(session, fake_db, spoken, monkeypatch):
    async def ident(frame, candidates):
        return IdentifyResult(sku="someone_elses_sku", confidence=0.95, alternatives=[])

    monkeypatch.setattr(guidance, "identify_product", ident)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    await guidance.handle_client_event(session, "fingertip_in_target", {})
    assert spoken[-1] == ("Not sure. Hold it closer to the camera.", "unsure")
    assert loop.picked == 0 and "someone_elses_sku" not in spoken[-1][0]


async def test_touch_vision_down_is_honest_and_not_counted(session, fake_db, spoken, monkeypatch):
    async def ident(frame, candidates):
        raise VisionError("all models failed")

    monkeypatch.setattr(guidance, "identify_product", ident)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    out = await loop.on_touch()
    assert spoken[-1] == ("I can't reach the AI right now. Please try again in a minute.", "warn")
    assert out["verdict"] == "warn" and out["error"] == "live.unavailable"
    assert loop.picked == 0 and guidance.active(session) is loop


async def test_touch_without_frame_warns(session, fake_db, spoken):
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    out = await loop.on_touch()
    assert spoken[-1] == ("I can't see a camera picture. Please check the camera.", "warn")
    assert out["verdict"] == "warn" and out["error"] == "camera.none" and loop.picked == 0


async def test_touch_uses_newest_guide_frame(session, fake_db, spoken, monkeypatch):
    seen = []

    async def ident(frame, candidates):
        seen.append(frame.id)
        return IdentifyResult(sku="sakthi_curd_1l", confidence=0.9, alternatives=[])

    monkeypatch.setattr(guidance, "identify_product", ident)
    await guidance.start(session, CURD, 3, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    session.frames.add(_frame("f2", 11.0))
    session.frames.add(_frame("i1", 12.0, purpose="identify"))
    await guidance.handle_client_event(session, "fingertip_in_target", {})
    assert seen == ["f2"]


async def test_duplicate_touch_after_done_is_ignored(session, fake_db, spoken, monkeypatch):
    async def ident(frame, candidates):
        return IdentifyResult(sku="sakthi_curd_1l", confidence=0.9, alternatives=[])

    monkeypatch.setattr(guidance, "identify_product", ident)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    await guidance.handle_client_event(session, "fingertip_in_target", {})
    count, sent = len(spoken), len(session.sent)
    await guidance.handle_client_event(session, "fingertip_in_target", {})
    assert len(spoken) == count and len(session.sent) == sent and loop.picked == 1
    assert await loop.on_touch() is None and loop.picked == 1


async def test_concurrent_touches_count_once(session, fake_db, spoken, monkeypatch):
    gate = asyncio.Event()

    async def ident(frame, candidates):
        await gate.wait()
        return IdentifyResult(sku="sakthi_curd_1l", confidence=0.9, alternatives=[])

    monkeypatch.setattr(guidance, "identify_product", ident)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    first = asyncio.create_task(guidance.handle_client_event(session, "fingertip_in_target", {}))
    second = asyncio.create_task(guidance.handle_client_event(session, "fingertip_in_target", {}))
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, second)
    assert loop.picked == 1 and len(spoken) == 1


async def test_late_identify_after_stop_says_nothing(session, fake_db, spoken, monkeypatch):
    gate = asyncio.Event()

    async def ident(frame, candidates):
        await gate.wait()
        return IdentifyResult(sku="sakthi_curd_1l", confidence=0.9, alternatives=[])

    monkeypatch.setattr(guidance, "identify_product", ident)
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    session.frames.add(_frame("f1", 10.0))
    touch = asyncio.create_task(guidance.handle_client_event(session, "fingertip_in_target", {}))
    await asyncio.sleep(0)
    await guidance.stop_guidance(session)
    gate.set()
    await touch
    assert spoken == [] and loop.picked == 0
    assert not any(k[2] == "memoryEvents" for k in fake_db.store)


# --- client events / stop ------------------------------------------------------------------------------------------


async def test_other_client_events_are_ignored(session, fake_db, spoken):
    loop = await guidance.start(session, CURD, 1, CANDS, run=False)
    await guidance.handle_client_event(session, "visibility", {"hidden": True})
    await guidance.handle_client_event(session, "network", {"online": False})
    assert spoken == [] and guidance.active(session) is loop


async def test_event_without_guidance_is_ignored(session, fake_db, spoken):
    await guidance.handle_client_event(session, "fingertip_in_target", {})
    assert spoken == [] and session.sent == []


async def test_event_with_null_data_is_fine(session, fake_db, spoken):
    await guidance.start(session, CURD, 1, CANDS, run=False)
    await guidance.handle_client_event(session, "fingertip_in_target", None)
    assert spoken[-1][1] == "warn"


async def test_stop_guidance_idempotent(session, fake_db, spoken):
    await guidance.start(session, CURD, 1, CANDS, run=False)
    await guidance.stop_guidance(session)
    await guidance.stop_guidance(session)
    stops = [m for m in session.sent if m.t == "request_frame" and m.stop]
    assert len(stops) == 1 and guidance.active(session) is None


async def test_stop_guidance_without_guidance_sends_nothing(session, fake_db, spoken):
    await guidance.stop_guidance(session)
    assert session.sent == []


async def test_stop_guidance_survives_a_closed_socket(session, fake_db, spoken):
    await guidance.start(session, CURD, 1, CANDS, run=False)

    async def closed(msg):
        raise RuntimeError("socket closed")

    session.send = closed
    await guidance.stop_guidance(session)
    assert guidance.active(session) is None


async def test_start_replaces_previous_loop(session, fake_db, spoken):
    first = await guidance.start(session, CURD, 1, CANDS, run=False)
    second = await guidance.start(session, MILK, 1, CANDS, run=False)
    assert first.active is False and guidance.active(session) is second
    assert [m.stop for m in session.sent if m.t == "request_frame"] == [None, True, None]


async def test_sessions_are_independent(session, fake_db, spoken):
    other = type(session)()
    other.session_id = "s2"
    a = await guidance.start(session, CURD, 1, CANDS, run=False)
    b = await guidance.start(other, MILK, 1, CANDS, run=False)
    await guidance.stop_guidance(other)
    assert guidance.active(session) is a and a.active and not b.active


# --- the spoken item label ---------------------------------------------------------------------------------------


def test_item_label_does_not_repeat_the_pack_size():
    assert guidance._item(CURD) == "Sakthi curd 1 L"
    assert guidance._item(ProductRef(sku="x", name="Sakthi Curd 1 L", brand="Sakthi", pack="1 L pack")) == \
        "Sakthi Curd 1 L"
    assert guidance._item(ProductRef(sku="x", name="Aavin Milk 500 ml", brand="Aavin", pack="500 ml pouch")) == \
        "Aavin Milk 500 ml"
    assert guidance._item(ProductRef(sku="x", name="Arun Ice Cream Family Box", brand="Arun", pack="family box")) == \
        "Arun Ice Cream Family Box"
    assert guidance._item(ProductRef(sku="x", name="Aavin Milk 500 ml", brand="Aavin", pack="1 L pouch")) == \
        "Aavin Milk 500 ml 1 L pouch"
    assert guidance._item(ProductRef(sku="x", name="Aavin ghee", brand="Aavin", pack="")) == "Aavin ghee"
    assert guidance._item(None) == ""


# --- product_find --------------------------------------------------------------------------------------------------


async def test_product_find_fresh_location_starts_guidance(find, fake_db):
    _location(fake_db, "sakthi_curd_1l")
    out = await products.product_find("curd", count_needed=2)
    assert out["found"] is True and out["sku"] == "sakthi_curd_1l" and out["stale"] is False
    loop = guidance.active(find)
    assert loop.needed == 2 and loop.ref == CURD and {c.sku for c in loop.candidates} == {c.sku for c in CANDS}
    assert find.sent[-1].t == "request_frame" and find.sent[-1].continuous is True
    events = [v for k, v in fake_db.store.items() if k[2] == "memoryEvents"]
    assert [(e["type"], e["refId"]) for e in events] == [("find", "sakthi_curd_1l")]
    await guidance.stop_guidance(find)


async def test_product_find_model_location_is_never_fact(find, fake_db):
    _location(fake_db, "sakthi_curd_1l", confirmed_by="model")
    out = await products.product_find("curd")
    assert out["found"] is True and out["stale"] is True and guidance.active(find).ref == CURD


async def test_product_find_old_location_is_stale(find, fake_db):
    _location(fake_db, "sakthi_curd_1l", at=NOW - timedelta(days=4))
    out = await products.product_find("curd")
    assert out["found"] is True and out["stale"] is True


async def test_product_find_without_location_is_stale(find):
    out = await products.product_find("curd")
    assert out["found"] is True and out["stale"] is True
    assert guidance.active(find).needed == 1


async def test_product_find_unknown_product_without_a_frame_starts_nothing(find, fake_db):
    """Not a catalog product: the camera looks for it by its words (tests/memory/test_find_free_text.py);
    with no frame nothing starts and nothing is logged."""
    out = await products.product_find("paneer")
    assert out["found"] is False and out["free_text"] is True
    assert find.requested == ["ask"] and guidance.active(find) is None
    assert not any(k[2] == "memoryEvents" for k in fake_db.store)


@pytest.mark.parametrize("raw, needed", [(0, 1), (-3, 1), ("2", 2), (2.0, 2), ("lots", 1), (None, 1)])
async def test_product_find_count_is_a_positive_int(find, raw, needed):
    await products.product_find("curd", count_needed=raw)
    assert guidance.active(find).needed == needed


async def test_product_find_adds_ref_missing_from_shop_products(find, fake_db):
    del fake_db.store[("shops", "demo", "products", "sakthi_curd_1l")]
    await products.product_find("curd")
    assert "sakthi_curd_1l" in {c.sku for c in guidance.active(find).candidates}


async def test_product_find_twice_replaces_the_first_guidance(find):
    await products.product_find("curd")
    first = guidance.active(find)
    await products.product_find("milk")
    assert first.active is False and guidance.active(find).ref == MILK


# --- end to end over the real Sunrise Dairy catalog --------------------------------------------------------------


@pytest.fixture
async def sunrise(fake_db, session, spoken, no_run, monkeypatch):
    data = seed.load_catalog(SUNRISE_CATALOG)
    store = MemoryStore()
    await seed.seed_into(store, data, env={})
    for p in data["products"]:
        fake_db.store[("shops", "sunrise_dairy", "products", p["sku"])] = dict(p)
    set_store(store)
    session.shop_id = "sunrise_dairy"
    monkeypatch.setattr(products, "get_session", lambda: session)
    monkeypatch.setattr(products.spatial, "_utcnow", lambda: NOW)
    yield session
    set_store(None)


async def test_find_and_pick_two_curd_real_catalog(sunrise, fake_db, spoken, monkeypatch):
    _location(fake_db, "sakthi_curd_1l", shop="sunrise_dairy")
    out = await products.product_find("Sakthi curd 1 litre", count_needed=2)
    assert out["found"] is True and out["sku"] == "sakthi_curd_1l" and out["stale"] is False
    loop = guidance.active(sunrise)
    assert len(loop.candidates) == 10

    labels = []

    async def point(frame, label):
        labels.append(label)
        return TargetResult(label=label, point=(400, 500), box=None, confidence=0.9)

    picks = iter(["aavin_milk_500ml", "sakthi_curd_1l", "sakthi_curd_1l"])

    async def ident(frame, candidates):
        return IdentifyResult(sku=next(picks), confidence=0.9, alternatives=[])

    monkeypatch.setattr(guidance, "point_to", point)
    monkeypatch.setattr(guidance, "identify_product", ident)
    sunrise.frames.add(_frame("g1", 100.0))
    assert await loop.step() is True
    assert labels == ["Sakthi Curd 1 L"] and sunrise.sent[-1].t == "target"
    for _ in range(3):
        await guidance.handle_client_event(sunrise, "fingertip_in_target", {})
    assert spoken == [
        ("This is Aavin Milk 500 ml, not Sakthi Curd 1 L.", "mismatch"),
        ("Sakthi Curd 1 L ✓. Take 1 more.", "match"),
        ("Sakthi Curd 1 L ✓. That's all 2.", "match"),
    ]
    assert spoken.urgent == [] and guidance.active(sunrise) is None


# --- registry -------------------------------------------------------------------------------------------------------


def test_tools_registered():
    from aira_live.tools import TOOL_FUNCS, TOOLS_BY_NAME

    names = {f.__name__ for f in TOOL_FUNCS}
    assert {"product_find", "product_identify", "location_remember"} <= names
    assert TOOLS_BY_NAME["product_find"] is products.product_find
