"""Fridge / shelf listing (spec §4): camera_ask over a fridge or shelf lists what is visible, grouped by area."""
import json
from pathlib import Path

import pytest

from aira_live.frames import Frame
from aira_live.i18n import t
from aira_live.session import current_session
from aira_live.tools import camera
from aira_live.vision import listing
from aira_live.vision.gemini import VisionError

I18N_DIR = Path(__file__).resolve().parents[2] / "aira_live" / "i18n"
TINY = "/9j/4AAQSkZJRg=="

FRIDGE = {
    "areas": [
        {"area": "bottom", "items": [{"label": "water bottle", "point": [850, 700], "distance": "arm"}]},
        {"area": "top", "items": [
            {"label": "curd", "point": [150, 600], "distance": "arm"},
            {"label": "milk", "point": [150, 200], "distance": "arm"},
        ]},
        {"area": "door", "area_name": "fridge door", "items": [
            {"label": "red jam jar", "point": [400, 950], "distance": "near"}]},
    ],
    "confidence": 0.85,
}


# --- classification ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("q", [
    "what's in my fridge?", "What is in the fridge", "list the products on this shelf", "what products are here",
    "tell me each product", "what items are on the shelf", "ஃப்ரிட்ஜில் என்ன இருக்கு", "फ्रिज में क्या है",
])
def test_fridge_and_shelf_questions_are_listings(q):
    assert listing.is_listing(q) is True
    assert camera.classify(q) == "list"


@pytest.mark.parametrize("q", ["what's in front of me?", "is my path clear?", "read the label on the fridge",
                               "which shelf has curd?", "where is the fridge?"])
def test_other_questions_are_not_listings(q):
    assert camera.classify(q) != "list"


# --- parsing --------------------------------------------------------------------------------------------------------


def test_parse_areas_keeps_well_formed_items_top_to_bottom_and_left_to_right():
    areas = listing.parse_areas(FRIDGE["areas"])
    assert [a["area"] for a in areas] == ["top", "door", "bottom"]
    assert [i["label"] for i in areas[0]["items"]] == ["milk", "curd"]      # left to right by x
    assert areas[1]["area_name"] == "fridge door"


def test_parse_areas_drops_junk_and_unknown_areas_become_other():
    raw = [
        {"area": "shelf 9", "items": [{"label": "ghee", "point": [500, 500], "distance": "arm"}]},
        {"area": "top", "items": [{"label": "", "point": [1, 1]}, {"label": "x", "point": [5000, 1]}, "junk"]},
        "not a dict", {"area": "middle", "items": "none"},
    ]
    areas = listing.parse_areas(raw)
    assert areas == [{"area": "other", "area_name": "shelf 9",
                      "items": [{"label": "ghee", "point": [500, 500], "distance": "arm"}]}]


def test_parse_areas_handles_a_flat_object_list():
    """ER 2 sometimes answers with a flat items list: everything goes into one "other" area."""
    areas = listing.parse_areas({"items": [{"label": "milk", "point": [100, 100], "distance": "far"}]})
    assert len(areas) == 1 and areas[0]["area"] == "other" and areas[0]["items"][0]["label"] == "milk"


def test_flat_objects_of_a_listing():
    objs = listing.flat_objects(listing.parse_areas(FRIDGE["areas"]))
    assert [o["label"] for o in objs] == ["milk", "curd", "red jam jar", "water bottle"]
    assert objs[0]["area"] == "top"


# --- spoken line -------------------------------------------------------------------------------------------------


def test_say_listing_groups_by_area_with_sides_in_english():
    say = listing.say_listing(listing.parse_areas(FRIDGE["areas"]), "en")
    assert say == ("Top shelf: milk to your left, curd straight ahead. "
                   "Fridge door: red jam jar to your far right. "
                   "Bottom shelf: water bottle to your right.")


def test_say_listing_speaks_the_session_language():
    areas = listing.parse_areas(FRIDGE["areas"])
    ta, hi = listing.say_listing(areas, "ta"), listing.say_listing(areas, "hi")
    assert ta.startswith(t("cam.area.top", "ta")) and t("cam.side.left", "ta") in ta
    assert hi.startswith(t("cam.area.top", "hi")) and t("cam.side.left", "hi") in hi
    assert ta != hi != listing.say_listing(areas, "en")


def test_say_listing_caps_areas_and_items():
    areas = [{"area": "other", "area_name": f"shelf {n}", "items": [
        {"label": f"item {n}{k}", "point": [100, 100], "distance": "arm"} for k in range(6)]} for n in range(6)]
    say = listing.say_listing(areas, "en")
    assert say.count(":") == listing.MAX_AREAS
    assert say.count("item 0") == listing.MAX_ITEMS


def test_say_listing_empty_asks_for_the_shelf():
    assert listing.say_listing([], "en") == "I can't see any products. Show me the shelf."


def test_listing_keys_exist_in_every_language():
    keys = ["cam.list.none", "cam.list.area", "cam.list.item"] + [f"cam.area.{a}" for a in listing.AREAS]
    for lang in ("en", "ta", "hi"):
        cat = json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        assert [k for k in keys if not cat.get(k)] == [], lang


# --- camera_ask over a fridge ------------------------------------------------------------------------------------


@pytest.fixture
def session(mk_session, monkeypatch):
    s, rec = mk_session(lang="en")
    calls: list[dict] = []

    async def fake_frames(purpose, count=1, interval_ms=0, timeout_s=4.0):
        calls.append({"purpose": purpose, "count": count})
        return [Frame(id=f"f{i}", jpeg_b64=TINY, w=640, h=480, ts=float(i + 1), purpose=purpose)
                for i in range(count)]

    monkeypatch.setattr(s, "request_frames", fake_frames)
    s.frame_calls = calls
    token = current_session.set(s)
    yield s
    current_session.reset(token)


def _script(monkeypatch, *replies):
    seen: list[dict] = []
    items = list(replies)

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        seen.append({"role": role, "parts": parts, "schema": schema_hint})
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return dict(item)

    monkeypatch.setattr(listing, "generate_json", fake)
    return seen


async def test_camera_ask_lists_the_fridge(session, monkeypatch):
    seen = _script(monkeypatch, FRIDGE)
    out = await camera.camera_ask("what's in my fridge?")
    assert session.frame_calls == [{"purpose": "ask", "count": 1}]
    assert seen[0]["role"] == "point" and "shelf" in seen[0]["parts"][-1].lower()
    assert out["kind"] == "list" and out["confidence"] == 0.85
    assert out["say"].startswith("Top shelf: milk to your left, curd straight ahead.")
    assert [a["area"] for a in out["areas"]] == ["top", "door", "bottom"]
    assert [o["label"] for o in out["objects"]] == ["milk", "curd", "red jam jar", "water bottle"]


async def test_camera_ask_listing_in_tamil(session, monkeypatch):
    session.lang = "ta"
    _script(monkeypatch, FRIDGE)
    out = await camera.camera_ask("ஃப்ரிட்ஜில் என்ன இருக்கு")
    assert out["say"].startswith(t("cam.area.top", "ta"))


async def test_camera_ask_listing_low_confidence_is_not_sure(session, monkeypatch):
    _script(monkeypatch, {"areas": FRIDGE["areas"], "confidence": 0.3, "aim_hint": "vision.aim.move_back"})
    out = await camera.camera_ask("what's on this shelf?")
    assert out["say"] == f"{t('cam.not_sure', 'en')} {t('vision.aim.move_back', 'en')}"
    assert out["areas"] == [] and "milk" not in out["say"]


async def test_camera_ask_listing_with_nothing_visible_asks_for_the_shelf(session, monkeypatch):
    _script(monkeypatch, {"areas": [], "confidence": 0.9})
    out = await camera.camera_ask("what products are here?")
    assert out["say"] == t("cam.list.none", "en") and out["objects"] == []


async def test_camera_ask_listing_vision_down_is_honest(session, monkeypatch):
    _script(monkeypatch, VisionError("down"))
    out = await camera.camera_ask("what's in the fridge?")
    assert out["say"] == t("live.unavailable", "en")


async def test_listing_tells_the_observers_when_the_module_exists(session, monkeypatch):
    import sys
    import types

    seen = []
    mod = types.ModuleType("aira_live.vision.observers")

    async def on_vision_objects(sess, objects, context):
        seen.append((sess, [o["label"] for o in objects], context))

    mod.on_vision_objects = on_vision_objects
    monkeypatch.setitem(sys.modules, "aira_live.vision.observers", mod)
    _script(monkeypatch, FRIDGE)
    await camera.camera_ask("what's in my fridge?")
    assert seen == [(session, ["milk", "curd", "red jam jar", "water bottle"], "camera_list")]


async def test_listing_without_observers_module_still_works(session, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "aira_live.vision.observers", None)   # import raises ImportError
    _script(monkeypatch, FRIDGE)
    out = await camera.camera_ask("what's in my fridge?")
    assert out["kind"] == "list"


async def test_a_broken_observer_never_breaks_the_answer(session, monkeypatch):
    import sys
    import types

    mod = types.ModuleType("aira_live.vision.observers")

    async def on_vision_objects(sess, objects, context):
        raise RuntimeError("boom")

    mod.on_vision_objects = on_vision_objects
    monkeypatch.setitem(sys.modules, "aira_live.vision.observers", mod)
    _script(monkeypatch, FRIDGE)
    out = await camera.camera_ask("what's in my fridge?")
    assert out["kind"] == "list" and out["say"].startswith("Top shelf")
