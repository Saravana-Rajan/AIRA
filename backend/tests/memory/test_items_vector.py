"""Last-seen memory over Firestore vector search: "keys" finds a sighting labelled "key bunch" through find_nearest
(fake embedder, fake collection.find_nearest); without a vector or an index, exactly the old behaviour."""
from datetime import datetime, timedelta, timezone

import pytest

from aira_live import embeddings
from aira_live.embeddings import FailingEmbedder, FakeEmbedder
from aira_live.memory import items
from aira_live.tools import memory as memtools
from tests.memory.conftest import FakeCollection

NOW = datetime(2026, 10, 15, 13, 40, tzinfo=timezone.utc)
SYNONYMS = {"bunch": "keys", "key": "keys", "keychain": "keys", "mobile": "phone"}
FOCUS = {"keys", "wallet", "phone", "umbrella", "glasses"}


@pytest.fixture
def embedder():
    e = FakeEmbedder(SYNONYMS, FOCUS)
    embeddings.set_embedder(e)
    yield e
    embeddings.set_embedder(None)
    FakeCollection.find_nearest_raises = None


async def test_sighting_stores_the_label_vector(fake_db, embedder):
    await items.record_sighting("demo", "key bunch", "on the fridge", 0.8, at=NOW)
    (doc,) = [d for d in fake_db.store.values() if "item" in d]
    vec = embeddings.values_of(doc["embedding"])
    assert vec is not None and len(vec) == embeddings.DIM
    assert doc["item"] == "key bunch" and doc["place"] == "on the fridge"


async def test_keys_matches_key_bunch_by_vector(fake_db, embedder):
    await items.record_sighting("demo", "key bunch", "on the fridge", 0.8, at=NOW - timedelta(minutes=5))
    await items.record_sighting("demo", "wallet", "in the drawer", 0.8, at=NOW)
    s = await items.last_seen("demo", "keys", now=NOW)
    assert s is not None and s.item == "key bunch" and s.place == "on the fridge"
    assert await items.last_seen("demo", "umbrella", now=NOW) is None


async def test_exact_label_still_wins(fake_db, embedder):
    await items.record_sighting("demo", "keys", "on the counter", 0.8, at=NOW - timedelta(hours=1))
    await items.record_sighting("demo", "key bunch", "on the fridge", 0.8, at=NOW)
    s = await items.last_seen("demo", "keys", now=NOW)
    assert s.place == "on the counter"


async def test_memory_where_speaks_the_synonym_match(fake_db, session, embedder, monkeypatch):
    monkeypatch.setattr(memtools, "get_session", lambda: session)
    monkeypatch.setattr(items, "_utcnow", lambda: NOW)
    await items.record_sighting("demo", "key bunch", "on the fridge", 0.8, at=NOW - timedelta(minutes=30))
    out = await memtools.memory_where("where are my keys")
    assert out["found"] and out["place"] == "on the fridge"
    assert out["item"] == "keys" and out["when"] == "today at 6:40 pm" and out["old"] is False
    assert out["say_en"] == "I last saw your keys on the fridge, today at 6:40 pm. Check there first."


async def test_expired_synonym_sightings_are_ignored(fake_db, embedder):
    await items.record_sighting("demo", "key bunch", "on the fridge", 0.8, at=NOW - timedelta(days=8))
    assert await items.last_seen("demo", "keys", now=NOW) is None


async def test_missing_index_behaves_as_before(fake_db, embedder):
    await items.record_sighting("demo", "key bunch", "on the fridge", 0.8, at=NOW)
    FakeCollection.find_nearest_raises = RuntimeError("400 Missing vector index configuration")
    assert await items.last_seen("demo", "keys", now=NOW) is None
    assert (await items.last_seen("demo", "key bunch", now=NOW)).place == "on the fridge"


async def test_embedding_failure_stores_text_only_and_matches_exactly(fake_db):
    embeddings.set_embedder(FailingEmbedder())
    try:
        await items.record_sighting("demo", "key bunch", "on the fridge", 0.8, at=NOW)
        (doc,) = [d for d in fake_db.store.values() if "item" in d]
        assert "embedding" not in doc
        assert await items.last_seen("demo", "keys", now=NOW) is None
        assert (await items.last_seen("demo", "key bunch", now=NOW)).item == "key bunch"
    finally:
        embeddings.set_embedder(None)
