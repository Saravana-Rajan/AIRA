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
    assert out == {"say": "I have not seen your keys yet. Show me around and I will remember.", "found": False,
                   "item": "keys"}


async def test_where_last_seen_wording_and_fields(wired):
    await items.record_sighting("demo", "keys", "on the fridge", 0.8, at=NOW - timedelta(minutes=30))
    out = await memtools.memory_where("where are my keys")
    assert out["say"] == "I last saw your keys on the fridge, today at 6:40 pm. Check there first."
    assert out["found"] and out["item"] == "keys" and out["place"] == "on the fridge" and out["old"] is False
    assert out["at"] == (NOW - timedelta(minutes=30)).isoformat()


async def test_where_old_sighting_says_a_while_ago(wired):
    await items.record_sighting("demo", "wallet", "in the drawer", 0.8, at=NOW - timedelta(days=2))
    out = await memtools.memory_where("wallet")
    assert out["old"] is True and "a while ago" in out["say"] and "2 days ago" in out["say"]


async def test_where_follows_the_session_language(wired):
    wired.lang = "ta"
    await items.record_sighting("demo", "phone", "on the counter", 0.8, at=NOW - timedelta(minutes=3))
    out = await memtools.memory_where("phone")
    assert items.speak_item("phone", "ta") in out["say"] and "last saw" not in out["say"]


async def test_where_an_owner_named_item(wired):
    await items.watch_item("demo", "walking stick")
    await items.record_sighting("demo", "walking stick", "by the door", 0.8, at=NOW - timedelta(minutes=3))
    out = await memtools.memory_where("my walking stick")
    assert out["found"] and "walking stick" in out["say"]


async def test_where_while_not_remembering_still_answers_from_memory(wired):
    await items.record_sighting("demo", "keys", "on the fridge", 0.8, at=NOW - timedelta(minutes=3))
    await items.set_remembering("demo", False)
    out = await memtools.memory_where("keys")
    assert out["found"] and "not remembering" in out["say"]


async def test_forget_everything_and_one_item(wired, fake_db):
    await items.record_sighting("demo", "keys", "on the fridge", 0.8, at=NOW)
    await items.record_sighting("demo", "bag", "on the chair", 0.8, at=NOW)
    out = await memtools.memory_forget("my bag")
    assert out == {"say": "Done. I forgot where your bag was.", "forgotten": 1, "item": "bag"}
    out = await memtools.memory_forget()
    assert out == {"say": "Done. I forgot everything I saw.", "forgotten": 1, "item": None}
    assert not [p for p in fake_db.store if p[:3] == ("shops", "demo", "memory") and p[-1] != "settings"]


async def test_stop_and_start_remembering(wired):
    out = await memtools.memory_remembering(False)
    assert out == {"say": "Okay. I stopped remembering. Nothing new will be saved.", "remembering": False, "item": None}
    assert (await items.settings("demo"))["remembering"] is False
    out = await memtools.memory_remembering(True)
    assert out["remembering"] is True and out["say"] == "Okay. I will remember where I see your things."


async def test_remember_my_item(wired):
    await items.set_remembering("demo", False)
    out = await memtools.memory_remembering(True, item="my walking stick")
    assert out == {"say": "Okay. I will remember where I see your walking stick.", "remembering": True,
                   "item": "walking stick"}
    cfg = await items.settings("demo")
    assert cfg == {"remembering": True, "items": ["walking stick"]}
