"""Bounding boxes: the ER 2 ask paths no longer request box_2d (dropped to cut reply length and latency; a
persistent box will come from on-device MediaPipe instead). The parsers still accept a box if one ever
arrives, and when present it still reaches the monitor (source "er2") and the phone's "Show boxes" overlay
as a ``boxes`` server message."""
import json

import pytest

from aira_live import monitor_hooks
from aira_live.frames import Frame
from aira_live.session import current_session
from aira_live.tools import camera, describe, guidance
from aira_live.vision import count, identify, listing, point
from aira_live.vision.identify import ProductRef

TINY = "/9j/4AAQSkZJRg=="
CANDS = [ProductRef(sku="aavin_milk_1l", name="Aavin milk", brand="Aavin", pack="1 L"),
         ProductRef(sku="arun_icecream_box", name="Arun ice cream", brand="Arun", pack="family box")]


@pytest.fixture(autouse=True)
def clean_hooks():
    saved = list(monitor_hooks.HOOKS)
    monitor_hooks.HOOKS.clear()
    yield
    monitor_hooks.HOOKS[:] = saved


@pytest.fixture
def published():
    got: list[dict] = []

    async def hook(session, msg):
        got.append(msg)

    monitor_hooks.register(hook)
    return got


def _frame(ts: float = 1.0, purpose: str = "ask") -> Frame:
    return Frame(id=f"f{ts}", jpeg_b64=TINY, w=640, h=480, ts=ts, purpose=purpose)


def _boxes_sent(rec) -> list[dict]:
    return [json.loads(m.model_dump_json(by_alias=True, exclude_none=True)) for m in rec.msgs
            if getattr(m, "t", None) == "boxes"]


# ---------- prompts and schemas ----------
@pytest.mark.parametrize("prompt, schema", [
    (point.POINT_PROMPT, point.POINT_SCHEMA),
    (count.COUNT_PROMPT, count.COUNT_SCHEMA),
    (identify.IDENTIFY_PROMPT, identify.IDENTIFY_SCHEMA),
    (identify.FREE_TEXT_PROMPT, identify.FREE_TEXT_SCHEMA),
    (listing.LIST_PROMPT, listing.LIST_SCHEMA),
    (camera.ASK_PROMPT, camera.ASK_SCHEMA),
    (describe.DESCRIBE_PROMPT, describe.DESCRIBE_SCHEMA),
], ids=["point", "count", "identify", "free_text", "listing", "camera_ask", "describe"])
def test_no_er2_prompt_requests_box_2d(prompt, schema):
    # box_2d was dropped from the ER 2 ask paths to cut reply length and latency; a persistent box will come
    # from on-device MediaPipe instead. The parsers below still accept a box if one ever arrives.
    assert "box_2d" not in prompt and "box_2d" not in schema
    assert "bounding box" not in prompt


# ---------- the shared box parser ----------
def test_parse_box_accepts_only_a_well_formed_box_inside_0_1000():
    assert point.parse_box([390, 505, 435, 555]) == (390, 505, 435, 555)
    assert point.parse_box((0.4, 0, 999.6, 1000)) == (0, 0, 1000, 1000)
    assert point.parse_box([500, 500, 100, 100]) is None       # inverted
    assert point.parse_box([10, 10, 10, 20]) is None           # empty height
    assert point.parse_box([0, 0, 1001, 10]) is None           # out of range
    assert point.parse_box([0, 0, 10]) is None                 # three numbers
    assert point.parse_box([0, 0, True, 10]) is None           # booleans are not numbers
    assert point.parse_box(None) is None and point.parse_box("box") is None


def test_point_prefers_box_2d_and_still_drops_a_box_that_misses_its_point():
    t = point.parse_target({"point": [412, 530], "box_2d": [390, 505, 435, 555], "box": [0, 0, 10, 10],
                            "confidence": 0.8}, "curd")
    assert t.box == (390, 505, 435, 555)
    t = point.parse_target({"point": [412, 530], "box_2d": [500, 500, 600, 600], "confidence": 0.8}, "curd")
    assert t is not None and t.box is None


def test_count_keeps_each_deduped_pack_with_its_box():
    r = count.assess([
        {"point": [100, 100], "box_2d": [80, 80, 120, 120], "label": "milk"},
        {"point": [105, 103], "box_2d": [80, 80, 120, 120], "label": "milk"},      # duplicate: dropped
        {"point": [400, 400], "box_2d": [500, 500, 100, 100], "label": "milk"},    # bad box: kept, box None
        {"point": [700, 700], "box": [650, 650, 750, 750]},                        # legacy "box", no label
    ])
    assert r.count == 3
    assert r.objects == [
        {"label": "milk", "point": [100, 100], "box": [80, 80, 120, 120]},
        {"label": "milk", "point": [400, 400], "box": None},
        {"label": "", "point": [700, 700], "box": [650, 650, 750, 750]},
    ]
    assert count.assess([]).objects == []


def test_identify_carries_the_box_of_the_held_product_whatever_the_verdict():
    r = identify.decide({"sku": "aavin_milk_1l", "confidence": 0.9, "label_visible": True,
                         "box_2d": [200, 300, 800, 700]}, CANDS)
    assert r.sku == "aavin_milk_1l" and r.box == (200, 300, 800, 700)
    r = identify.decide({"sku": "aavin_milk_1l", "confidence": 0.3, "box_2d": [200, 300, 800, 700]}, CANDS)
    assert r.sku is None and r.box == (200, 300, 800, 700)
    assert identify.decide({"sku": "aavin_milk_1l", "confidence": 0.9, "box_2d": [1, 1, 1, 1]}, CANDS).box is None
    assert identify.decide({"sku": None, "confidence": 0.9}, CANDS).box is None


def test_free_text_identify_carries_the_box():
    r = identify.decide_free_text({"match": True, "confidence": 0.9, "seen": "water bottle",
                                   "box_2d": [100, 400, 600, 600]}, "water bottle")
    assert r.match and r.box == (100, 400, 600, 600)
    assert identify.decide_free_text({"match": False, "confidence": 0.9, "seen": "x"}, "x").box is None


def test_listing_items_carry_their_boxes_and_survive_without_them():
    areas = listing.parse_areas([{"area": "top", "items": [
        {"label": "milk", "point": [200, 300], "box_2d": [150, 250, 250, 350], "distance": "arm"},
        {"label": "curd", "point": [200, 600], "distance": "near"},
        {"label": "ghee", "point": [200, 800], "box_2d": [0, 0, 2000, 1]},
    ]}])
    items = areas[0]["items"]
    assert items[0]["box"] == [150, 250, 250, 350]
    assert "box" not in items[1] and "box" not in items[2]
    assert [o["label"] for o in listing.flat_objects(areas)] == ["milk", "curd", "ghee"]


def test_camera_parse_objects_prefers_box_2d_over_a_stale_box():
    out = camera.parse_objects([{"label": "table", "point": [600, 500], "box_2d": [200, 100, 900, 900],
                                 "box": [0, 0, 10, 10]}])
    assert out[0]["box"] == [200, 100, 900, 900] and out[0]["distance"] == "near"


# ---------- results reach the monitor (source "er2") and the phone ----------
@pytest.fixture
def session(mk_session, monkeypatch):
    s, rec = mk_session(lang="en")

    async def fake_frames(purpose, count=1, interval_ms=0, timeout_s=4.0):
        return [_frame(float(i + 1)) for i in range(count)]

    monkeypatch.setattr(s, "request_frames", fake_frames)
    token = current_session.set(s)
    yield s, rec
    current_session.reset(token)


async def test_camera_ask_publishes_er2_boxes_to_the_monitor_and_the_phone(session, monkeypatch, published):
    s, rec = session

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        return {"answer": "A table ahead.", "guidance": [], "confidence": 0.9, "_model": "er2", "objects": [
            {"label": "table", "point": [600, 500], "box_2d": [200, 100, 900, 900], "distance": "near"},
            {"label": "door", "point": [400, 900], "distance": "far"}]}

    monkeypatch.setattr(camera, "generate_json", fake)
    out = await camera.camera_ask("what is in front of me?")
    assert out["objects"][0]["box"] == [200, 100, 900, 900]
    assert len(published) == 1
    msg = published[0]
    assert msg["kind"] == "vision" and msg["source"] == "er2" and msg["context"] == "camera_ask:look"
    assert msg["session"] == s.session_id and msg["frameId"] == "f1.0"
    assert msg["objects"] == [
        {"label": "table", "box": [200, 100, 900, 900], "point": [600, 500], "distance": "near"},
        {"label": "door", "box": None, "point": [400, 900], "distance": "far"},
    ]
    assert _boxes_sent(rec) == [{"t": "boxes", "source": "er2", "frameId": "f1.0",
                                 "objects": [{"label": "table", "box": [200, 100, 900, 900]}]}]


async def test_camera_ask_without_a_monitor_still_sends_the_phone_its_boxes(session, monkeypatch):
    s, rec = session

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        return {"answer": "ok", "confidence": 0.9, "objects": [
            {"label": "crate", "point": [600, 500], "box_2d": [300, 200, 900, 800], "distance": "near"}]}

    monkeypatch.setattr(camera, "generate_json", fake)
    await camera.camera_ask("what is ahead?")
    assert _boxes_sent(rec)[0]["objects"] == [{"label": "crate", "box": [300, 200, 900, 800]}]


async def test_listing_publishes_er2_boxes(session, monkeypatch, published):
    s, rec = session

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        return {"confidence": 0.9, "areas": [{"area": "top", "area_name": "top shelf", "items": [
            {"label": "milk", "point": [200, 300], "box_2d": [150, 250, 250, 350], "distance": "arm"}]}]}

    monkeypatch.setattr(listing, "generate_json", fake)
    out = await listing.list_shelf(s, TINY, "what's in my fridge?")
    assert out["objects"][0]["box"] == [150, 250, 250, 350]
    assert published[0]["source"] == "er2" and published[0]["context"] == "camera_list"
    assert published[0]["objects"][0]["box"] == [150, 250, 250, 350]
    assert _boxes_sent(rec)[0]["objects"] == [{"label": "milk", "box": [150, 250, 250, 350]}]


async def test_describe_step_publishes_er2_boxes(mk_session, monkeypatch, published):
    s, rec = mk_session(lang="en")
    s.frames.add(_frame(5.0, "describe"))

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        return {"confidence": 0.9, "objects": [
            {"label": "fridge", "point": [500, 200], "box_2d": [100, 50, 900, 350], "distance": "far"}]}

    monkeypatch.setattr(describe, "generate_json", fake)
    loop = describe.DescribeLoop(s, busy=lambda: False, frame_timeout_s=0.2)
    assert await loop.step() is True
    assert published[0]["source"] == "er2" and published[0]["context"] == "describe"
    assert published[0]["objects"] == [{"label": "fridge", "box": [100, 50, 900, 350], "point": [500, 200],
                                        "distance": "far"}]
    assert _boxes_sent(rec)[0]["objects"] == [{"label": "fridge", "box": [100, 50, 900, 350]}]


async def test_guidance_publishes_the_target_box_green_when_found_and_empty_when_lost(mk_session, monkeypatch,
                                                                                      published):
    s, rec = mk_session(lang="en")
    answers = iter([point.TargetResult(label="Aavin milk", point=(412, 530), box=(390, 505, 435, 555),
                                       confidence=0.8), None])

    async def fake_point(frame, label):
        return next(answers)

    monkeypatch.setattr(guidance, "point_to", fake_point)
    from aira_live.tools.products import free_ref

    loop = guidance.GuidanceLoop(s, free_ref("Aavin milk"), 1, [], frame_timeout_s=0.2)
    s.frames.add(_frame(1.0, "guide"))
    assert await loop.step() is True
    s.frames.add(_frame(2.0, "guide"))
    assert await loop.step() is True
    vision = [m for m in published if m["kind"] == "vision"]
    assert [m["found"] for m in vision] == [True, False]
    assert vision[0]["target"] == "Aavin milk" and vision[0]["objects"][0]["box"] == [390, 505, 435, 555]
    assert vision[1]["objects"] == []
    sent = _boxes_sent(rec)
    assert sent[0] == {"t": "boxes", "source": "er2", "frameId": "f1.0", "target": "Aavin milk", "found": True,
                       "objects": [{"label": "Aavin milk", "box": [390, 505, 435, 555], "score": 0.8}]}
    assert sent[1]["found"] is False and sent[1]["objects"] == []
