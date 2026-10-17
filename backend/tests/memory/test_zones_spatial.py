from datetime import datetime, timedelta, timezone

import pytest

from aira_live.memory import spatial
from aira_live.memory.zones import normalize_zone, speak_zone

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


def test_normalize_zone_spoken_forms():
    assert normalize_zone("Fridge 2, middle shelf, left") == "fridge2/middle/left"
    assert normalize_zone("fridge two top") == "fridge2/top"
    assert normalize_zone("fridge2/middle/left") == "fridge2/middle/left"
    assert normalize_zone("counter") == "counter"


def test_normalize_zone_spoken_variants():
    assert normalize_zone("Fridge number 2, centre") == "fridge2/center"
    assert normalize_zone("rack 1, lower shelf, right side") == "rack1/bottom/right"
    assert normalize_zone("the freezer, upper shelf") == "freezer/top"


def test_normalize_zone_rejects_empty():
    with pytest.raises(ValueError):
        normalize_zone("  the shelf ")


def test_speak_zone_is_english_for_every_language():
    assert speak_zone("fridge2/middle/left") == "fridge 2, middle shelf, left"
    for lang in ("en", "ta", "hi", "fr"):
        assert speak_zone("fridge2/middle/left", lang) == "fridge 2, middle shelf, left"
    assert speak_zone("rack1/top/center", "ta") == "rack 1, top shelf, centre"
    assert speak_zone("counter", "hi") == "counter"


async def test_remember_then_where_roundtrip(fake_db, monkeypatch):
    monkeypatch.setattr(spatial, "_utcnow", lambda: NOW)
    loc = await spatial.remember("demo", "sakthi_curd_1l", "fridge2/middle/left", "user", 1.0)
    assert loc.confirmed_at == NOW
    stored = fake_db.store[("shops", "demo", "locations", "sakthi_curd_1l")]
    assert stored == {"sku": "sakthi_curd_1l", "zone": "fridge2/middle/left", "confirmedBy": "user",
                      "confirmedAt": NOW, "confidence": 1.0}
    again = await spatial.where("demo", "sakthi_curd_1l")
    assert again == loc


async def test_where_missing_returns_none(fake_db):
    assert await spatial.where("demo", "nothing") is None


async def test_where_malformed_doc_returns_none(fake_db):
    fake_db.store[("shops", "demo", "locations", "x")] = {"sku": "x", "confirmedBy": "user"}
    assert await spatial.where("demo", "x") is None


async def test_remember_rejects_bad_confirmed_by(fake_db):
    with pytest.raises(ValueError):
        await spatial.remember("demo", "x", "counter", "camera", 0.5)
    assert fake_db.store == {}


async def test_remember_rejects_bad_confidence_and_zone(fake_db):
    with pytest.raises(ValueError):
        await spatial.remember("demo", "x", "counter", "user", 1.5)
    with pytest.raises(ValueError):
        await spatial.remember("demo", "x", "", "user", 1.0)
    assert fake_db.store == {}


def _loc(by="user", age=timedelta(days=1)):
    return spatial.Location(sku="sakthi_curd_1l", zone="fridge2/middle/left",
                            confirmed_by=by, confirmed_at=NOW - age, confidence=0.9)


def test_describe_location_fresh():
    assert spatial.describe_location("Sakthi curd", _loc(), "en", NOW) == \
        "Sakthi curd: last recorded on fridge 2, middle shelf, left."


def test_describe_location_stale_when_model_confirmed():
    text = spatial.describe_location("Sakthi curd", _loc(by="model"), "en", NOW)
    assert "may have moved" in text


def test_describe_location_stale_after_three_days():
    assert not spatial.is_stale(_loc(age=timedelta(days=3)), NOW)
    assert spatial.is_stale(_loc(age=timedelta(days=3, minutes=1)), NOW)
    assert "may have moved" in spatial.describe_location("Sakthi curd", _loc(age=timedelta(days=4)), "en", NOW)


def test_naive_confirmed_at_is_treated_as_utc():
    loc = spatial.Location(sku="s", zone="counter", confirmed_by="user",
                           confirmed_at=datetime(2026, 10, 13, 9, 0), confidence=1.0)
    assert loc.confirmed_at.tzinfo is not None
    assert not spatial.is_stale(loc, NOW)


def test_describe_location_none():
    assert spatial.describe_location("Sakthi curd", None, "en", NOW) == \
        "I don't have a saved place for Sakthi curd. Let me look with the camera."


@pytest.mark.parametrize("lang", ["ta", "hi"])
def test_describe_location_ignores_the_language(lang):
    for loc in (_loc(), _loc(by="model"), None):
        assert spatial.describe_location("Sakthi curd", loc, lang, NOW) == \
            spatial.describe_location("Sakthi curd", loc, "en", NOW)


def test_location_facts():
    assert spatial.location_facts(_loc(), NOW) == {
        "zone": "fridge2/middle/left", "zone_text": "fridge 2, middle shelf, left", "stale": False,
        "confirmed_by": "user", "confirmed_at": (NOW - timedelta(days=1)).isoformat()}
    assert spatial.location_facts(_loc(by="model"), NOW)["stale"] is True
    assert spatial.location_facts(_loc(age=timedelta(days=4)), NOW)["stale"] is True
    assert spatial.location_facts(None, NOW) == {"zone": None, "zone_text": None, "stale": True,
                                                 "confirmed_by": None, "confirmed_at": None}
