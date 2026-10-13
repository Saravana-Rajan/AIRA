"""Live tools memory_where / memory_forget / memory_remembering over the last-seen memory."""
from datetime import datetime, timedelta, timezone

import pytest

from aira_live import prompt
from aira_live.memory import items
from aira_live.tools import TOOLS_BY_NAME
from aira_live.tools import memory as memtools

NOW = datetime(2026, 10, 15, 13, 40, tzinfo=timezone.utc)   # 19:10 IST


@pytest.fixture
def wired(fake_db, session, monkeypatch):
    monkeypatch.setattr(memtools, "get_session", lambda: session)
    monkeypatch.setattr(items, "_utcnow", lambda: NOW)
    return session


def test_registered_as_live_tools_with_a_prompt_rule():
    assert TOOLS_BY_NAME["memory_where"] is memtools.memory_where
    assert TOOLS_BY_NAME["memory_forget"] is memtools.memory_forget
    assert TOOLS_BY_NAME["memory_remembering"] is memtools.memory_remembering
    text = prompt.render_prompt({"name": "Sunrise Dairy"}, [], [], "en", "short")
    assert "memory_where" in text and "memory_forget" in text and "memory_remembering" in text
    assert "last saw" in text


async def test_where_nothing_seen(wired):
    out = await memtools.memory_where("my keys")
    assert out["found"] is False and out["item"] == "keys" and out["remembering"] is True
    assert "place" not in out and "say" not in out
    assert out["say_en"] == "I have not seen your keys yet. Show me around and I will remember."


async def test_where_last_seen_facts(wired):
    await items.record_sighting("demo", "keys", "on the fridge", 0.8, at=NOW - timedelta(minutes=30))
    out = await memtools.memory_where("where are my keys")
    assert out["found"] is True and out["item"] == "keys" and out["place"] == "on the fridge"
    assert out["old"] is False and out["kind"] == "seen" and out["remembering"] is True
    assert out["at"] == (NOW - timedelta(minutes=30)).isoformat() and out["last_seen_ago_s"] == 1800
    assert out["when"] == "today at 6:40 pm"
    assert out["side"] is None and out["dist"] is None and out["landmark"] is None   # a text-only place
    assert out["say_en"] == "I last saw your keys on the fridge, today at 6:40 pm. Check there first."
    assert "say" not in out


async def test_where_place_parts_become_side_and_distance_facts(wired):
    parts = {"side": "cam.side.left", "dist": "cam.dist.arm", "landmark": "chair", "on": True}
    await items.record_sighting("demo", "bag", "on the chair, to your left, at arm's length", 0.9,
                                kind="placed", at=NOW - timedelta(minutes=1), place_parts=parts)
    out = await memtools.memory_where("bag")
    assert out["side"] == "left" and out["dist"] == "arm" and out["landmark"] == "chair" and out["on"] is True
    assert out["place"] == "on the chair, to your left, at arm's length" and out["kind"] == "placed"
    assert out["when"] == "just now" and out["last_seen_ago_s"] == 60


async def test_where_old_sighting_is_flagged(wired):
    await items.record_sighting("demo", "wallet", "in the drawer", 0.8, at=NOW - timedelta(days=2))
    out = await memtools.memory_where("wallet")
    assert out["old"] is True and out["when"] == "2 days ago" and out["last_seen_ago_s"] == 2 * 86400
    assert "a while ago" in out["say_en"] and "2 days ago" in out["say_en"]


async def test_where_never_depends_on_the_session_language(wired):
    wired.lang = "ta"
    await items.record_sighting("demo", "phone", "on the counter", 0.8, at=NOW - timedelta(minutes=3))
    out = await memtools.memory_where("phone")
    assert out["item"] == "phone" and out["place"] == "on the counter" and out["say_en"].isascii()
    wired.lang = "en"
    assert (await memtools.memory_where("phone"))["say_en"] == out["say_en"]


async def test_where_an_owner_named_item(wired):
    await items.watch_item("demo", "walking stick")
    await items.record_sighting("demo", "walking stick", "by the door", 0.8, at=NOW - timedelta(minutes=3))
    out = await memtools.memory_where("my walking stick")
    assert out["found"] and out["item"] == "walking stick" and out["place"] == "by the door"


async def test_where_while_not_remembering_still_answers_from_memory(wired):
    await items.record_sighting("demo", "keys", "on the fridge", 0.8, at=NOW - timedelta(minutes=3))
    await items.set_remembering("demo", False)
    out = await memtools.memory_where("keys")
    assert out["found"] and out["remembering"] is False and "not remembering" in out["say_en"]


async def test_forget_everything_and_one_item(wired, fake_db):
    await items.record_sighting("demo", "keys", "on the fridge", 0.8, at=NOW)
    await items.record_sighting("demo", "bag", "on the chair", 0.8, at=NOW)
    out = await memtools.memory_forget("my bag")
    assert out == {"ok": True, "forgotten": 1, "item": "bag", "say_en": "Done. I forgot where your bag was."}
    out = await memtools.memory_forget()
    assert out == {"ok": True, "forgotten": 1, "item": None, "say_en": "Done. I forgot everything I saw."}
    assert not [p for p in fake_db.store if p[:3] == ("shops", "demo", "memory") and p[-1] != "settings"]


async def test_stop_and_start_remembering(wired):
    out = await memtools.memory_remembering(False)
    assert out == {"ok": True, "remembering": False, "item": None,
                   "say_en": "Okay. I stopped remembering. Nothing new will be saved."}
    assert (await items.settings("demo"))["remembering"] is False
    out = await memtools.memory_remembering(True)
    assert out["remembering"] is True and out["item"] is None and "say" not in out


async def test_remember_my_item(wired):
    await items.set_remembering("demo", False)
    out = await memtools.memory_remembering(True, item="my walking stick")
    assert out == {"ok": True, "remembering": True, "item": "walking stick",
                   "say_en": "Okay. I will remember where I see your walking stick."}
    cfg = await items.settings("demo")
    assert cfg == {"remembering": True, "items": ["walking stick"]}
