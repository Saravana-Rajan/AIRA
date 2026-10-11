"""Last-seen visual memory of personal items (spec §5): sightings, placing, wording, staleness, expiry, forget."""
from datetime import datetime, timedelta, timezone

import pytest

from aira_live.memory import items
from aira_live.vision import observers

NOW = datetime(2026, 10, 15, 13, 40, tzinfo=timezone.utc)   # 19:10 IST


@pytest.fixture(autouse=True)
def _fresh_dedupe():
    items._recent.clear()
    yield
    items._recent.clear()


def _docs(fake_db, shop="demo"):
    return {p[-1]: v for p, v in fake_db.store.items() if p[:3] == ("shops", shop, "memory") and p[-1] != "settings"}


# --- what counts as a personal item ----------------------------------------------------------------------------


def test_canonical_item_matches_labels_and_speech():
    assert items.canonical_item("car keys") == "keys"
    assert items.canonical_item("my wallet") == "wallet"
    assert items.canonical_item("a pair of spectacles") == "glasses"
    assert items.canonical_item("mobile phone") == "phone"
    assert items.canonical_item("TV remote control") == "remote"
    assert items.canonical_item("சாவி") == "keys"
    assert items.canonical_item("चश्मा") == "glasses"


def test_canonical_item_ignores_products_and_partial_words():
    assert items.canonical_item("milk crate") is None
    assert items.canonical_item("keyboard") is None
    assert items.canonical_item("") is None


def test_canonical_item_knows_owner_named_items():
    assert items.canonical_item("white walking stick", custom=["walking stick"]) == "walking stick"
    assert items.canonical_item("walking stick") is None


# --- recording -------------------------------------------------------------------------------------------------


async def test_record_sighting_stores_text_only_with_expiry(fake_db):
    s = await items.record_sighting("demo", "keys", "on the fridge", 0.7, at=NOW)
    assert s is not None and s.kind == "seen"
    (doc,) = _docs(fake_db).values()
    assert doc == {"item": "keys", "place": "on the fridge", "placeParts": None, "at": NOW, "confidence": 0.7,
                   "kind": "seen", "expiresAt": NOW + items.EXPIRY}
    assert not any(k in doc for k in ("jpeg", "image", "frame", "frameId"))


async def test_record_sighting_validates(fake_db):
    with pytest.raises(ValueError):
        await items.record_sighting("demo", "keys", "   ", 0.7, at=NOW)
    with pytest.raises(ValueError):
        await items.record_sighting("demo", "keys", "fridge", 1.7, at=NOW)
    with pytest.raises(ValueError):
        await items.record_sighting("demo", "", "fridge", 0.7, at=NOW)


async def test_record_sighting_is_skipped_while_not_remembering(fake_db):
    await items.set_remembering("demo", False)
    assert await items.record_sighting("demo", "keys", "fridge", 0.7, at=NOW) is None
    assert _docs(fake_db) == {}
    await items.set_remembering("demo", True)
    assert await items.record_sighting("demo", "keys", "fridge", 0.7, at=NOW) is not None


# --- recall ----------------------------------------------------------------------------------------------------


async def test_last_seen_is_the_newest(fake_db):
    await items.record_sighting("demo", "keys", "on the counter", 0.6, at=NOW - timedelta(hours=2))
    await items.record_sighting("demo", "keys", "on the fridge", 0.6, at=NOW - timedelta(minutes=5))
    await items.record_sighting("demo", "wallet", "in the drawer", 0.6, at=NOW)
    s = await items.last_seen("demo", "keys", now=NOW)
    assert s is not None and s.place == "on the fridge"
    assert await items.last_seen("demo", "umbrella", now=NOW) is None


async def test_placing_beats_a_later_passive_sighting(fake_db):
    await items.record_sighting("demo", "keys", "on the fridge", 0.9, kind="placed", at=NOW - timedelta(hours=1))
    await items.record_sighting("demo", "keys", "on the counter", 0.55, at=NOW - timedelta(minutes=10))
    s = await items.last_seen("demo", "keys", now=NOW)
    assert s is not None and s.place == "on the fridge" and s.kind == "placed"


async def test_a_confident_later_sighting_beats_an_older_placing(fake_db):
    await items.record_sighting("demo", "keys", "on the fridge", 0.9, kind="placed", at=NOW - timedelta(days=2))
    await items.record_sighting("demo", "keys", "on the counter", 0.9, at=NOW - timedelta(minutes=10))
    s = await items.last_seen("demo", "keys", now=NOW)
    assert s is not None and s.place == "on the counter"


async def test_expired_sightings_are_ignored_and_purged(fake_db):
    await items.record_sighting("demo", "keys", "on the fridge", 0.9, at=NOW - timedelta(days=8))
    await items.record_sighting("demo", "bag", "on the chair", 0.9, at=NOW - timedelta(days=1))
    assert await items.last_seen("demo", "keys", now=NOW) is None
    assert await items.purge_expired("demo", now=NOW) == 1
    assert [d["item"] for d in _docs(fake_db).values()] == ["bag"]


async def test_forget_everything_and_forget_one_item(fake_db):
    await items.record_sighting("demo", "keys", "on the fridge", 0.9, at=NOW)
    await items.record_sighting("demo", "bag", "on the chair", 0.9, at=NOW)
    await items.record_sighting("other", "keys", "on the desk", 0.9, at=NOW)
    assert await items.forget("demo", "keys") == 1
    assert [d["item"] for d in _docs(fake_db).values()] == ["bag"]
    assert await items.forget("demo") == 1
    assert _docs(fake_db) == {}
    assert len(_docs(fake_db, "other")) == 1


async def test_settings_default_and_custom_items(fake_db):
    cfg = await items.settings("demo")
    assert cfg == {"remembering": True, "items": []}
    assert await items.watch_item("demo", "My Walking Stick") == "walking stick"
    assert (await items.settings("demo"))["items"] == ["walking stick"]
    await items.watch_item("demo", "walking stick")
    assert (await items.settings("demo"))["items"] == ["walking stick"]


# --- wording ---------------------------------------------------------------------------------------------------


def _sighting(at, kind="seen", place="on top of the fridge"):
    return items.Sighting(item="keys", place=place, at=at, confidence=0.8, kind=kind)


def test_describe_says_last_saw_never_is():
    text = items.describe("keys", _sighting(NOW - timedelta(minutes=30)), "en", NOW)
    assert text == "I last saw your keys on top of the fridge, today at 6:40 pm. Check there first."
    assert " is " not in text and " are " not in text


def test_describe_yesterday_and_a_while_ago():
    text = items.describe("keys", _sighting(NOW - timedelta(days=1, hours=1)), "en", NOW)
    assert text.startswith("I last saw your keys on top of the fridge, yesterday at 6:10 pm.")
    assert "a while ago" in text
    text = items.describe("keys", _sighting(NOW - timedelta(days=3)), "en", NOW)
    assert "3 days ago" in text and "a while ago" in text


def test_describe_within_24h_is_not_old():
    assert "a while ago" not in items.describe("keys", _sighting(NOW - timedelta(hours=23)), "en", NOW)
    assert items.is_old(_sighting(NOW - timedelta(hours=25)), NOW)


def test_describe_in_tamil_and_hindi_use_the_catalog():
    for lang in ("ta", "hi"):
        text = items.describe(items.speak_item("keys", lang), _sighting(NOW - timedelta(minutes=5)), lang, NOW)
        assert items.speak_item("keys", lang) in text and "last saw" not in text


def test_speak_place_renders_parts_in_the_session_language():
    s = items.Sighting(item="bag", place="near the chair, to your left, at arm's length", at=NOW, confidence=0.8,
                       kind="seen", place_parts={"side": "cam.side.left", "dist": "cam.dist.arm", "landmark": "chair"})
    assert items.speak_place(s, "en") == "near the chair, to your left, at arm's length"
    assert items.speak_place(s, "ta").startswith("உங்கள் இடது பக்கம்")


# --- from an ER result -----------------------------------------------------------------------------------------


def test_place_from_objects_uses_side_distance_and_the_nearest_landmark():
    keys = {"label": "keys", "point": [300, 150], "distance": "arm"}
    fridge = {"label": "fridge", "point": [500, 200], "distance": "arm"}
    door = {"label": "door", "point": [500, 900], "distance": "far"}
    place, parts = items.place_from_objects(keys, [keys, fridge, door])
    assert parts == {"side": "cam.side.far_left", "dist": "cam.dist.arm", "landmark": "fridge", "on": True}
    assert place == "on the fridge, to your far left, at arm's length"


def test_place_from_objects_without_landmark():
    keys = {"label": "keys", "point": [500, 500], "distance": "near"}
    place, parts = items.place_from_objects(keys, [keys])
    assert parts == {"side": "cam.side.centre", "dist": "cam.dist.near", "landmark": None, "on": False}
    assert place == "straight ahead, within arm's reach"


class _S:
    shop_id, lang, session_id = "demo", "en", "s1"


async def test_observer_records_a_sighting_from_an_er_result(fake_db, monkeypatch):
    monkeypatch.setattr(items, "_utcnow", lambda: NOW)
    objs = [{"label": "milk crate", "point": [700, 150], "distance": "near"},
            {"label": "a bunch of keys", "point": [300, 150], "distance": "arm"},
            {"label": "fridge", "point": [500, 200], "distance": "arm"}]
    await items.observe(_S(), objs, {"purpose": "ask", "confidence": 0.85})
    (doc,) = _docs(fake_db).values()
    assert doc["item"] == "keys" and doc["kind"] == "seen" and doc["confidence"] == 0.85
    assert doc["place"].startswith("on the fridge")
    assert doc["placeParts"]["landmark"] == "fridge"


async def test_observer_marks_a_placing_event_strong(fake_db, monkeypatch):
    monkeypatch.setattr(items, "_utcnow", lambda: NOW)
    objs = [{"label": "keys", "point": [300, 150], "distance": "arm"}, {"label": "fridge", "point": [500, 200], "distance": "arm"}]
    await items.observe(_S(), objs, {"purpose": "describe", "answer": "A hand put the keys on the fridge top."})
    (doc,) = _docs(fake_db).values()
    assert doc["kind"] == "placed"


async def test_observer_skips_products_and_low_confidence(fake_db, monkeypatch):
    monkeypatch.setattr(items, "_utcnow", lambda: NOW)
    await items.observe(_S(), [{"label": "curd", "point": [1, 1], "distance": "near"}], {"purpose": "ask"})
    await items.observe(_S(), [{"label": "keys", "point": [1, 1], "distance": "near", "confidence": 0.2}], {"purpose": "ask"})
    assert _docs(fake_db) == {}


async def test_observer_dedupes_the_same_sighting_within_a_minute(fake_db, monkeypatch):
    monkeypatch.setattr(items, "_utcnow", lambda: NOW)
    objs = [{"label": "bag", "point": [500, 800], "distance": "far"}]
    await items.observe(_S(), objs, {"purpose": "describe"})
    await items.observe(_S(), objs, {"purpose": "describe"})
    assert len(_docs(fake_db)) == 1
    monkeypatch.setattr(items, "_utcnow", lambda: NOW + timedelta(minutes=2))
    await items.observe(_S(), objs, {"purpose": "describe"})
    assert len(_docs(fake_db)) == 2


async def test_observer_is_registered_with_vision_observers(fake_db, monkeypatch):
    monkeypatch.setattr(items, "_utcnow", lambda: NOW)
    assert items.observe in observers.observers()
    await observers.on_vision_objects(_S(), [{"label": "umbrella", "point": [500, 500], "distance": "near"}], {"purpose": "ask"})
    assert [d["item"] for d in _docs(fake_db).values()] == ["umbrella"]


def test_personal_in_view_lines_for_describe_mode():
    objs = [{"label": "bag", "point": [500, 300], "distance": "arm"}, {"label": "chair", "point": [700, 300], "distance": "arm"},
            {"label": "milk", "point": [500, 600], "distance": "near"}]
    lines = items.personal_in_view(objs, "en")
    assert lines == {"bag": "Your bag: on the chair, to your left, at arm's length."}
