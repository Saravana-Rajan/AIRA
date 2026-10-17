"""price_query: the owner's price board as facts (read-only). Prices come from the products in the Store, never
invented; Live phrases them in the owner's language.

Runs on a MemoryStore seeded from the demo catalog with the real session, like the catalog tools' tests.
"""
from pathlib import Path

import pytest

from aira_live.business import seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.session import Session, current_session, current_tool_call_id
from aira_live.tools import TOOLS_BY_NAME
from aira_live.tools import prices
from aira_live.tools.prices import CATEGORIES, PAGE_LIMIT, category_of, price_query

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "shared" / "catalog" / "sunrise_dairy.json"
SHOP = "sunrise_dairy"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AIRA_DEMO_SHOP", SHOP)


@pytest.fixture
async def store():
    s = MemoryStore()
    set_store(s)
    await seed.seed_into(s, seed.load_catalog(CATALOG), env={})
    yield s
    set_store(None)


class _Phone:
    def __init__(self):
        self.msgs: list = []

    async def __call__(self, msg) -> None:
        self.msgs.append(msg)


def _session(lang: str = "en") -> Session:
    s = Session("owner1", SHOP, lang, "short", _Phone())
    current_session.set(s)
    current_tool_call_id.set("tc-price")
    return s


@pytest.fixture
def session():
    return _session()


# ---------------------------------------------------------------------------------------------------- registry

def test_price_query_is_registered_and_read_only():
    assert "price_query" in TOOLS_BY_NAME
    assert TOOLS_BY_NAME["price_query"] is price_query
    assert not hasattr(prices, "_commit_price_query")      # nothing to confirm: it never writes


def test_prompt_sends_price_questions_to_price_query():
    from aira_live.prompt import RULES
    assert any("price_query" in r for r in RULES)


# ---------------------------------------------------------------------------------------------------- categories

def test_category_of_puts_each_product_in_one_of_the_board_groups():
    assert category_of({"name": "Aavin Milk 500 ml"}) == "milk"
    assert category_of({"name": "Sakthi Curd 1 L"}) == "curd"
    assert category_of({"name": "Aavin Buttermilk 200 ml"}) == "buttermilk"     # not "butter", not "milk"
    assert category_of({"name": "Aavin Butter 100 g"}) == "butter"
    assert category_of({"name": "Aavin Ghee 200 ml"}) == "ghee"
    assert category_of({"name": "Aavin Paneer 200 g"}) == "paneer"
    assert category_of({"name": "Arun Ice Cream Family Box"}) == "ice cream"
    assert category_of({"name": "Hatsun Icecream Cup", "sku": "hatsun_icecream_cup"}) == "ice cream"
    assert category_of({"name": "Masala Lassi 200 ml"}) == "other"
    assert CATEGORIES == ("milk", "curd", "buttermilk", "ghee", "paneer", "butter", "ice cream")


# ---------------------------------------------------------------------------------------------------- one product

async def test_one_product_gives_its_price_from_the_store(store, session):
    r = await price_query("sakthi curd 500 ml")
    assert r["ok"] is True and r["sku"] == "sakthi_curd_500ml" and r["product"] == "Sakthi Curd 500 ml"
    doc = await store.get(f"shops/{SHOP}/products/sakthi_curd_500ml")
    assert r["price"] == doc["price"] and isinstance(r["price"], (int, float))
    assert r["unit"] == doc["unit"] and r["category"] == "curd"
    assert r["say_en"] == f"Sakthi Curd 500 ml is {doc['price']} rupees." and "say" not in r


async def test_one_product_follows_a_changed_price(store, session):
    doc = await store.get(f"shops/{SHOP}/products/sakthi_curd_500ml")
    await store.set(f"shops/{SHOP}/products/sakthi_curd_500ml", {**doc, "price": 30})
    r = await price_query("sakthi curd 500 ml")
    assert r["price"] == 30 and r["say_en"] == "Sakthi Curd 500 ml is 30 rupees."


async def test_unknown_product_is_an_engine_code(store, session):
    r = await price_query("chocolate")
    assert r["ok"] is False and r["error"] == "PRODUCT_UNKNOWN" and r["product"] == "chocolate"
    assert "price" not in r and "rupee" not in r["say_en"].lower()


async def test_facts_do_not_depend_on_the_session_language(store, session):
    en = await price_query("curd 500 ml")
    for lang in ("ta", "hi"):
        _session(lang)
        r = await price_query("curd 500 ml")
        assert r == en
        assert "say" not in r


async def test_product_without_a_price_is_never_invented(store, session):
    doc = await store.get(f"shops/{SHOP}/products/sakthi_curd_500ml")
    doc.pop("price")
    await store.set(f"shops/{SHOP}/products/sakthi_curd_500ml", doc)
    r = await price_query("sakthi curd 500 ml")
    assert r["ok"] is False and r["price"] is None and r["error"] == "price_unset"
    assert r["product"] == "Sakthi Curd 500 ml" and "rupee" not in r["say_en"]


# ---------------------------------------------------------------------------------------------------- the board

async def test_board_groups_by_category_in_board_order_and_pages(store, session):
    r = await price_query()
    assert r["ok"] is True
    assert len(r["prices"]) <= PAGE_LIMIT and r["page"] == 0
    cats = [i["category"] for i in r["items"]]
    assert cats == sorted(cats, key=lambda c: (*CATEGORIES, "other").index(c))
    assert r["groups"][0]["category"] == "milk"
    assert [(p["product"], p["price"]) for p in r["groups"][0]["prices"]] == [("Aavin Milk 500 ml", 44),
                                                                              ("Aavin Milk 1 L", 44)]
    assert r["groups"][1]["category"] == "curd"
    assert r["more"] is True and r["say_en"].startswith("Milk: Aavin Milk 500 ml 44, Aavin Milk 1 L 44. Curd: ")
    # Every listed number is a store price.
    docs = dict(await store.list(f"shops/{SHOP}/products"))
    for i in r["prices"]:
        assert i["price"] == docs[i["sku"]]["price"]
    assert [p for g in r["groups"] for p in g["prices"]] == r["prices"]


async def test_say_more_reads_the_rest_then_stops(store, session):
    first = await price_query()
    rest = await price_query(page=1)
    assert rest["ok"] is True and rest["page"] == 1
    assert {i["sku"] for i in first["prices"]} | {i["sku"] for i in rest["prices"]} == {i["sku"] for i in first["items"]}
    assert not ({i["sku"] for i in first["prices"]} & {i["sku"] for i in rest["prices"]})
    assert rest["more"] is False
    assert {"sku": "arun_icecream_box", "product": "Arun Ice Cream Family Box", "price": 180,
            "category": "ice cream"} in rest["prices"]
    end = await price_query(page=2)
    assert end["ok"] is True and end["prices"] == [] and end["groups"] == [] and end["more"] is False


async def test_board_skips_disabled_products_and_those_without_a_price(store, session):
    milk = await store.get(f"shops/{SHOP}/products/aavin_milk_1l")
    await store.set(f"shops/{SHOP}/products/aavin_milk_1l", {**milk, "active": False})
    butter = await store.get(f"shops/{SHOP}/products/aavin_butter_100g")
    butter.pop("price")
    await store.set(f"shops/{SHOP}/products/aavin_butter_100g", butter)
    r = await price_query()
    skus = {i["sku"] for i in r["items"]}
    assert "aavin_milk_1l" not in skus and "aavin_butter_100g" not in skus
    assert [p["sku"] for p in r["groups"][0]["prices"]] == ["aavin_milk_500ml"]


async def test_board_is_the_same_in_every_session_language(store, session):
    en = await price_query()
    _session("ta")
    assert await price_query() == en


async def test_empty_shop_has_no_prices(store, session):
    for sku, doc in await store.list(f"shops/{SHOP}/products"):
        await store.set(f"shops/{SHOP}/products/{sku}", {**doc, "active": False})
    r = await price_query()
    assert r["ok"] is True and r["items"] == [] and r["prices"] == [] and r["more"] is False
