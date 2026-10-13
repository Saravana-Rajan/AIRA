"""Firestore vector search for products: catalog.resolve falls back to find_nearest only after the exact / alias
match fails; a close call returns a ``did_you_mean`` fact; every failure behaves exactly as before. Fake embedder
and a MemoryStore find_nearest: no model call, no Firestore."""
import pytest

from aira_live import embeddings
from aira_live.business import catalog, hints, seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.embeddings import FailingEmbedder, FakeEmbedder

from tests.business.test_catalog import CATALOG, SHOP  # noqa: F401  (same demo catalog path)

SYNONYMS = {"packet": "pouch"}     # the fake's "semantics": a packet is a pouch
# the fake's meaningful words: the `look` vocabulary of the catalog (everything else weighs 0.1)
FOCUS = {"small", "large", "big", "blue", "white", "green", "yellow", "plastic", "pouch", "soft", "round", "jar",
         "lid", "flat", "vacuum", "square", "block", "foil", "wrapped", "paper", "tub", "frozen", "family"}


@pytest.fixture
def embedder():
    e = FakeEmbedder(SYNONYMS, FOCUS)
    embeddings.set_embedder(e)
    yield e
    embeddings.set_embedder(None)


@pytest.fixture
async def store(embedder):
    s = MemoryStore()
    set_store(s)
    await seed.seed_into(s, seed.load_catalog(CATALOG), env={}, embed=True)
    yield s
    set_store(None)


async def test_seeder_writes_a_vector_per_product(store):
    for sku, doc in await store.list(f"shops/{SHOP}/products"):
        vec = embeddings.values_of(doc.get("embedding"))
        assert vec is not None and len(vec) == embeddings.DIM, sku
    assert "embedding" not in (await store.get(f"shops/{SHOP}/suppliers/aavin_vendor"))


async def test_product_text_has_brand_name_pack_look_and_aliases():
    text = embeddings.product_text(next(p for p in seed.load_catalog(CATALOG)["products"] if p["sku"] == "sakthi_curd_500ml"))
    for word in ("Sakthi", "Curd 500 ml", "500 ml pack", "blue packet", "சக்தி தயிர்", "dahi", "half litre curd"):
        assert word in text, text


async def test_exact_alias_match_wins_without_any_embedding(store, embedder):
    ref = await catalog.resolve(SHOP, "half litre sakthi curd", "en")
    assert ref.sku == "sakthi_curd_500ml"
    assert not any("| query: half litre sakthi curd" in c[0] for c in embedder.calls)


async def test_look_words_find_the_product_by_vector(store):
    # no alias word at all: only the product's `look` ("flat white vacuum pack, square block") can match
    ref = await catalog.resolve(SHOP, "the flat white vacuum block", "en")
    assert ref is not None and ref.sku == "aavin_paneer_200g"


async def test_blue_packet_curd_is_sakthi_curd(store):
    # "curd" is an alias, so the alias path answers (the default pack) and no vector is needed
    ref = await catalog.resolve(SHOP, "the blue packet curd", "en")
    assert ref is not None and ref.sku == "sakthi_curd_1l"


async def test_round_tub_is_the_ice_cream(store):
    ref = await catalog.resolve(SHOP, "big round plastic tub frozen family", "en")
    assert ref is not None and ref.sku == "arun_icecream_box"


async def test_close_call_asks_did_you_mean(store):
    # "blue packet soft pouch" sits between the two curd sizes: no sure match, a suggestion instead
    spoken = "blue packet soft pouch"
    assert await catalog.resolve(SHOP, spoken, "en") is None
    r = await catalog.did_you_mean(SHOP, spoken, "en")
    assert (r["ok"], r["error"], r["product"]) == (False, "PRODUCT_UNKNOWN", spoken)
    assert r["did_you_mean"].startswith("Sakthi Curd "), r
    assert r["say_en"] == f"Did you mean {r['did_you_mean']}?"
    assert await catalog.did_you_mean(SHOP, spoken, "ta") == r      # facts, not a translation


async def test_nothing_near_is_unknown(store):
    assert await catalog.resolve(SHOP, "wrench spanner hammer", "en") is None
    r = await catalog.did_you_mean(SHOP, "wrench spanner hammer", "en")
    assert r == hints.fact("PRODUCT_UNKNOWN", product="wrench spanner hammer") and "did_you_mean" not in r


async def test_ambiguous_alias_match_never_falls_back_to_vectors(store, embedder):
    # "milk curd" names two different products: the old answer (ask which one) stays
    assert await catalog.resolve(SHOP, "milk curd", "en") is None
    assert not any("| query: milk curd" in c[0] for c in embedder.calls)


async def test_disabled_products_are_not_suggested(store):
    doc = await store.get(f"shops/{SHOP}/products/arun_icecream_box")
    await store.set(f"shops/{SHOP}/products/arun_icecream_box", {**doc, "active": False})
    assert await catalog.resolve(SHOP, "big round plastic tub frozen family", "en") is None


async def test_embedding_failure_behaves_as_before(store):
    embeddings.set_embedder(FailingEmbedder())
    assert await catalog.resolve(SHOP, "the flat white vacuum block", "en") is None
    r = await catalog.did_you_mean(SHOP, "the flat white vacuum block", "en")
    assert r["error"] == "PRODUCT_UNKNOWN" and "did_you_mean" not in r
    assert (await catalog.resolve(SHOP, "curd", "en")).sku == "sakthi_curd_1l"


async def test_vector_query_failure_behaves_as_before(store, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("no index for this query")

    monkeypatch.setattr(store, "find_nearest", boom)
    assert await catalog.resolve(SHOP, "the flat white vacuum block", "en") is None
    assert (await catalog.resolve(SHOP, "milk", "en")).sku == "aavin_milk_500ml"


async def test_no_api_key_means_no_embedding_and_no_vector(monkeypatch):
    embeddings.set_embedder(None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert await embeddings.embed("curd") is None


async def test_seeder_without_embedder_writes_no_vector_field():
    s = MemoryStore()
    await seed.seed_into(s, seed.load_catalog(CATALOG), env={})
    assert "embedding" not in (await s.get(f"shops/{SHOP}/products/sakthi_curd_1l"))
