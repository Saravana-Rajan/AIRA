"""product_find free-text fallback (spec §4): an item that is not a catalog SKU is pointed at by its words
("water bottle", "red jam jar") with ER 2, guided with the same tones and confirmed on touch by a free-text
identify. Never invented: no confident point means "I can't find it, show me the shelf"."""
import sys
import types

import pytest

from aira_live.frames import Frame
from aira_live.tools import guidance, products
from aira_live.vision.gemini import VisionError
from aira_live.vision.identify import FreeTextResult, ProductRef
from aira_live.vision.point import TargetResult

CURD = ProductRef(sku="sakthi_curd_1l", name="Sakthi curd", brand="Sakthi", pack="1 L")


@pytest.fixture
def spoken(monkeypatch):
    said = []

    async def notify(shop_id, text, cue="ack", urgent=False):
        said.append((text, cue))
        return 1

    monkeypatch.setattr(guidance, "notify", notify)
    return said


@pytest.fixture(autouse=True)
def clean_registry():
    guidance._ACTIVE.clear()
    yield
    guidance._ACTIVE.clear()


@pytest.fixture
def find(fake_db, session, spoken, monkeypatch):
    """product_find with a resolver that only knows "curd"; the guidance loop never runs in the background."""
    fake_db.store[("shops", "demo", "products", "sakthi_curd_1l")] = {
        "name": "Sakthi curd", "brand": "Sakthi", "unit": "pack", "packSizeL": 1.0}
    monkeypatch.setattr(products, "get_session", lambda: session)

    async def run(self):
        return None

    monkeypatch.setattr(guidance.GuidanceLoop, "run", run)

    async def resolve(shop_id, spoken_name, lang):
        return {"curd": CURD}.get(spoken_name.lower())

    monkeypatch.setattr(products, "catalog_resolve", resolve)
    return session


def _frame(fid="a1", ts=5.0, purpose="ask"):
    return Frame(id=fid, jpeg_b64="x", w=640, h=480, ts=ts, purpose=purpose)


def _point(monkeypatch, result):
    seen = []

    async def point_to(frame, label):
        seen.append((frame.id, label))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(products, "point_to", point_to)
    return seen


BOTTLE = TargetResult(label="water bottle", point=(600, 250), box=(500, 200, 700, 300), confidence=0.8)


# --- product_find: free-text start --------------------------------------------------------------------------


async def test_unknown_item_is_pointed_by_free_text_and_guided(find, fake_db, monkeypatch):
    find.frames.add(_frame())
    seen = _point(monkeypatch, BOTTLE)
    out = await products.product_find("water bottle")
    assert seen == [("a1", "water bottle")]
    assert find.requested == ["ask"]
    assert out["found"] is True and out["sku"] is None and out["free_text"] is True
    assert out["label"] == "water bottle" and out["point"] == [600, 250] and out["confidence"] == 0.8
    loop = guidance.active(find)
    assert loop is not None and loop.free_text is True and loop.ref.name == "water bottle"
    assert loop.ref.sku == products.FREE_PREFIX + "water bottle" and loop.needed == 1
    assert find.sent[-1].t == "request_frame" and find.sent[-1].purpose == "guide" and find.sent[-1].continuous
    events = [v for k, v in fake_db.store.items() if k[2] == "memoryEvents"]
    assert [(e["type"], e["refType"], e["refId"]) for e in events] == [("find", "free_text", "water bottle")]


async def test_catalog_products_never_use_the_free_text_path(find, monkeypatch):
    find.frames.add(_frame())
    seen = _point(monkeypatch, BOTTLE)
    out = await products.product_find("curd")
    assert out["sku"] == "sakthi_curd_1l" and "free_text" not in out
    assert seen == [] and guidance.active(find).free_text is False


async def test_no_confident_point_says_show_me_the_shelf_and_starts_nothing(find, fake_db, monkeypatch):
    find.frames.add(_frame())
    _point(monkeypatch, None)
    out = await products.product_find("red jam jar")
    assert out["found"] is False and out["sku"] is None and out["free_text"] is True
    assert out["label"] == "red jam jar" and out["confidence"] == 0.0
    assert guidance.active(find) is None
    assert not any(k[2] == "memoryEvents" for k in fake_db.store)


async def test_low_confidence_point_is_not_trusted(find, monkeypatch):
    find.frames.add(_frame())
    _point(monkeypatch, TargetResult(label="jar", point=(1, 1), box=None, confidence=products.FREE_MIN_CONF - 0.01))
    out = await products.product_find("red jam jar")
    assert out["found"] is False
    assert out["confidence"] == pytest.approx(products.FREE_MIN_CONF - 0.01)


async def test_free_text_without_a_frame_says_camera_none(find, monkeypatch):
    seen = _point(monkeypatch, BOTTLE)
    out = await products.product_find("water bottle")
    assert out["found"] is False and seen == []
    assert guidance.active(find) is None


async def test_free_text_vision_down_is_honest(find, monkeypatch):
    find.frames.add(_frame())
    _point(monkeypatch, VisionError("all models failed"))
    out = await products.product_find("water bottle")
    assert out["found"] is False
    assert guidance.active(find) is None


@pytest.mark.parametrize("spoken_item", ["", "   ", None])
async def test_blank_item_is_unknown_not_a_camera_call(find, monkeypatch, spoken_item):
    find.frames.add(_frame())
    seen = _point(monkeypatch, BOTTLE)
    out = await products.product_find(spoken_item)
    assert out["found"] is False and seen == [] and find.requested == []


async def test_free_text_count_is_kept(find, monkeypatch):
    find.frames.add(_frame())
    _point(monkeypatch, BOTTLE)
    await products.product_find("water bottle", count_needed="2")
    assert guidance.active(find).needed == 2


async def test_free_text_find_tells_the_observers(find, monkeypatch):
    seen = []
    mod = types.ModuleType("aira_live.vision.observers")

    async def on_vision_objects(sess, objects, context):
        seen.append((sess, objects, context))

    mod.on_vision_objects = on_vision_objects
    monkeypatch.setitem(sys.modules, "aira_live.vision.observers", mod)
    find.frames.add(_frame())
    _point(monkeypatch, BOTTLE)
    await products.product_find("water bottle")
    assert seen == [(find, [{"label": "water bottle", "point": [600, 250], "box": [500, 200, 700, 300],
                             "confidence": 0.8}], "product_find")]


# --- guidance: touch verified by free-text identify ----------------------------------------------------------


@pytest.fixture
def free_loop(fake_db, session, spoken, monkeypatch):
    async def run(self):
        return None

    monkeypatch.setattr(guidance.GuidanceLoop, "run", run)
    return session


def _ident(monkeypatch, result):
    seen = []

    async def identify_free_text(frame, label):
        seen.append((frame.id, label))
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(guidance, "identify_free_text", identify_free_text)

    async def never(frame, candidates):
        raise AssertionError("catalog identify must not run for a free-text item")

    monkeypatch.setattr(guidance, "identify_product", never)
    return seen


async def test_touch_on_the_free_text_item_confirms_it(free_loop, fake_db, spoken, monkeypatch):
    ref = products.free_ref("water bottle")
    seen = _ident(monkeypatch, FreeTextResult(match=True, confidence=0.9, seen="water bottle"))
    loop = await guidance.start(free_loop, ref, 1, [], run=False)
    free_loop.frames.add(_frame("g1", 10.0, "guide"))
    out = await loop.on_touch()
    assert seen == [("g1", "water bottle")]
    assert spoken[-1] == ("That's the water bottle.", "match")
    assert out == {"verdict": "match", "item": "water bottle", "sku": "free:water bottle", "picked": 1, "needed": 1,
                   "remaining": 0, "done": True, "say_en": "That's the water bottle."}
    assert guidance.active(free_loop) is None
    stop = free_loop.sent[-1]
    assert stop.t == "request_frame" and stop.purpose == "guide" and stop.stop is True
    events = [v for k, v in fake_db.store.items() if k[2] == "memoryEvents"]
    assert [(e["type"], e["refType"], e["refId"]) for e in events] == [("pick", "free_text", "water bottle")]


async def test_touch_on_a_different_thing_says_what_it_looks_like(free_loop, spoken, monkeypatch):
    _ident(monkeypatch, FreeTextResult(match=False, confidence=0.9, seen="jam jar"))
    loop = await guidance.start(free_loop, products.free_ref("water bottle"), 1, [], run=False)
    free_loop.frames.add(_frame("g1", 10.0, "guide"))
    out = await loop.on_touch()
    assert spoken[-1] == ("That looks like jam jar, not the water bottle.", "mismatch")
    assert out["verdict"] == "mismatch" and out["got"] == "jam jar" and out["item"] == "water bottle"
    assert loop.picked == 0 and guidance.active(free_loop) is loop


async def test_touch_unsure_never_confirms(free_loop, spoken, monkeypatch):
    _ident(monkeypatch, FreeTextResult(match=False, confidence=0.4, seen=""))
    loop = await guidance.start(free_loop, products.free_ref("water bottle"), 1, [], run=False)
    free_loop.frames.add(_frame("g1", 10.0, "guide"))
    out = await loop.on_touch()
    assert spoken[-1] == ("Not sure. Hold it closer to the camera.", "unsure")
    assert out["verdict"] == "unsure" and out["confidence"] == 0.4 and "jam" not in out["say_en"]
    assert loop.picked == 0 and guidance.active(free_loop) is loop


async def test_touch_two_items_needed(free_loop, spoken, monkeypatch):
    _ident(monkeypatch, FreeTextResult(match=True, confidence=0.9, seen="water bottle"))
    await guidance.start(free_loop, products.free_ref("water bottle"), 2, [], run=False)
    free_loop.frames.add(_frame("g1", 10.0, "guide"))
    await guidance.handle_client_event(free_loop, "fingertip_in_target", {})
    assert spoken[-1] == ("water bottle ✓. Take 1 more.", "match")
    await guidance.handle_client_event(free_loop, "fingertip_in_target", {})
    assert spoken[-1] == ("That's the water bottle.", "match")


async def test_touch_vision_down_is_honest(free_loop, spoken, monkeypatch):
    _ident(monkeypatch, VisionError("down"))
    loop = await guidance.start(free_loop, products.free_ref("water bottle"), 1, [], run=False)
    free_loop.frames.add(_frame("g1", 10.0, "guide"))
    await guidance.handle_client_event(free_loop, "fingertip_in_target", {})
    assert spoken[-1] == ("I can't reach the AI right now. Please try again in a minute.", "warn")
    assert loop.picked == 0


async def test_free_text_touch_is_english_whatever_the_session_language(free_loop, spoken, monkeypatch):
    free_loop.lang = "ta"
    _ident(monkeypatch, FreeTextResult(match=False, confidence=0.9, seen="jam"))
    await guidance.start(free_loop, products.free_ref("water bottle"), 1, [], run=False)
    free_loop.frames.add(_frame("g1", 10.0, "guide"))
    await guidance.handle_client_event(free_loop, "fingertip_in_target", {})
    assert spoken[-1] == ("That looks like jam, not the water bottle.", "mismatch")


def test_free_ref_and_label_round_trip():
    ref = products.free_ref("  Red Jam Jar ")
    assert ref == ProductRef(sku="free:Red Jam Jar", name="Red Jam Jar", brand="", pack="")
    assert guidance.is_free_text(ref) is True and guidance.is_free_text(CURD) is False
    assert guidance._item(ref) == "Red Jam Jar"
