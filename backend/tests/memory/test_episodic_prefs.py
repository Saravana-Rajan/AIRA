"""Episodic memory (shops/{shopId}/memoryEvents) and owner preferences (shops/{shopId}/prefs/main).

Uses the in-memory ``fake_db`` from ``tests/memory/conftest.py``.
"""
from datetime import datetime, timedelta, timezone

import pytest

from aira_live.memory import episodic, prefs

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


async def test_log_and_recent_newest_first(fake_db, monkeypatch):
    times = iter([T0, T0 + timedelta(minutes=1), T0 + timedelta(minutes=2)])
    monkeypatch.setattr(episodic, "_utcnow", lambda: next(times))
    await episodic.log("demo", "find", "Finding Sakthi curd 1 L", "product", "sakthi_curd_1l")
    await episodic.log("demo", "pick", "Picked 2 x Sakthi curd 1 L", "product", "sakthi_curd_1l")
    third = await episodic.log("demo", "location", "Aavin milk -> fridge1/top")
    events = await episodic.recent("demo", limit=2)
    assert [e["type"] for e in events] == ["location", "pick"]
    assert events[0]["id"] == third and events[0]["refType"] is None
    assert events[1]["refType"] == "product" and events[1]["refId"] == "sakthi_curd_1l"
    stored = fake_db.store[("shops", "demo", "memoryEvents", third)]
    assert stored == {"type": "location", "text": "Aavin milk -> fridge1/top",
                      "refType": None, "refId": None, "at": T0 + timedelta(minutes=2)}


async def test_recent_is_per_shop(fake_db):
    await episodic.log("demo", "find", "Finding Sakthi curd 1 L")
    await episodic.log("other", "find", "Finding Aavin milk")
    events = await episodic.recent("demo")
    assert [e["text"] for e in events] == ["Finding Sakthi curd 1 L"]


async def test_log_rejects_blank_text(fake_db):
    with pytest.raises(ValueError):
        await episodic.log("demo", "find", "   ")
    assert fake_db.store == {}


async def test_recent_limit_bounds(fake_db):
    with pytest.raises(ValueError):
        await episodic.recent("demo", limit=0)
    with pytest.raises(ValueError):
        await episodic.recent("demo", limit=101)


async def test_prefs_defaults_then_merge(fake_db):
    assert await prefs.get("demo") == {"speechRate": 1.0, "verbosity": "short",
                                       "productAliases": {}, "supplierAliases": {}}
    out = await prefs.set("demo", speech_rate=1.3, product_aliases={"thayir": "sakthi_curd_1l"})
    assert out["speechRate"] == 1.3 and out["verbosity"] == "short"
    assert out["productAliases"] == {"thayir": "sakthi_curd_1l"}
    assert fake_db.store[("shops", "demo", "prefs", "main")] == {
        "speechRate": 1.3, "productAliases": {"thayir": "sakthi_curd_1l"}}
    out2 = await prefs.set("demo", verbosity="detail")
    assert out2["speechRate"] == 1.3 and out2["verbosity"] == "detail"


async def test_prefs_set_replaces_each_given_field(fake_db, monkeypatch):
    """Real Firestore deep-merges maps under merge=True; a field-path merge list replaces each map whole,
    so an alias map can shrink and the fake and the real database agree."""
    doc_cls = type(fake_db.collection("shops").document("demo"))
    merges, real_set = [], doc_cls.set

    async def recording_set(self, data, merge=False):
        merges.append(merge)
        await real_set(self, data, merge=merge)

    monkeypatch.setattr(doc_cls, "set", recording_set)
    await prefs.set("demo", product_aliases={"paal": "aavin_milk_500ml"}, verbosity="detail")
    out = await prefs.set("demo", product_aliases={"thayir": "sakthi_curd_1l"})
    assert out["productAliases"] == {"thayir": "sakthi_curd_1l"} and out["verbosity"] == "detail"
    assert merges == [["productAliases", "verbosity"], ["productAliases"]]


async def test_prefs_defaults_are_not_shared(fake_db):
    first = await prefs.get("demo")
    first["productAliases"]["x"] = "y"
    assert (await prefs.get("demo"))["productAliases"] == {}


async def test_prefs_set_with_nothing_writes_nothing(fake_db):
    assert (await prefs.set("demo"))["verbosity"] == "short"
    assert fake_db.store == {}


async def test_prefs_validation(fake_db):
    with pytest.raises(ValueError):
        await prefs.set("demo", speech_rate=3.0)
    with pytest.raises(ValueError):
        await prefs.set("demo", speech_rate=True)
    with pytest.raises(ValueError):
        await prefs.set("demo", verbosity="loud")
    with pytest.raises(ValueError):
        await prefs.set("demo", favourite_colour="blue")
    with pytest.raises(ValueError):
        await prefs.set("demo", supplier_aliases=["sakthi"])
    assert fake_db.store == {}
