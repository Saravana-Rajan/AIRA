"""price_query: the owner's spoken price list (read-only). Prices come from the products in the Store, never invented.

Runs on a MemoryStore seeded from the demo catalog with the real session, like the catalog tools' tests.
"""
from pathlib import Path

import pytest

from aira_live.business import seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.i18n import LANGS, t
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


def test_category_labels_exist_in_every_language():
    for cat in (*CATEGORIES, "other"):
        key = "k3.cat." + cat.replace(" ", "_")
        for lang in LANGS:
            assert t(key, lang) != key, (key, lang)


# ---------------------------------------------------------------------------------------------------- one product

async def test_one_product_speaks_its_price_from_the_store(store, session):
    r = await price_query("sakthi curd 500 ml")
    assert r["ok"] is True and r["sku"] == "sakthi_curd_500ml"
    doc = await store.get(f"shops/{SHOP}/products/sakthi_curd_500ml")
    assert r["price"] == doc["price"]
    assert r["say"] == f"Sakthi Curd 500 ml is {doc['price']} rupees."


async def test_one_product_follows_a_changed_price(store, session):
    doc = await store.get(f"shops/{SHOP}/products/sakthi_curd_500ml")
    await store.set(f"shops/{SHOP}/products/sakthi_curd_500ml", {**doc, "price": 30})
    r = await price_query("sakthi curd 500 ml")
    assert r["price"] == 30 and r["say"] == "Sakthi Curd 500 ml is 30 rupees."


async def test_unknown_product_asks_which(store, session):
    r = await price_query("chocolate")
    assert r["ok"] is False and "price" not in r and "rupee" not in r["say"].lower()


async def test_one_product_in_tamil_and_hindi(store, session):
    for lang in ("ta", "hi"):
        _session(lang)
        r = await price_query("curd 500 ml")
        assert r["ok"] is True
        assert r["say"] == t("k3.price.one", lang, product="Sakthi Curd 500 ml", price=r["price"])
        assert r["say"] != t("k3.price.one", "en", product="Sakthi Curd 500 ml", price=r["price"])


async def test_product_without_a_price_is_never_invented(store, session):
    doc = await store.get(f"shops/{SHOP}/products/sakthi_curd_500ml")
    doc.pop("price")
    await store.set(f"shops/{SHOP}/products/sakthi_curd_500ml", doc)
    r = await price_query("sakthi curd 500 ml")
    assert r["ok"] is False and r["price"] is None
    assert r["say"] == t("k3.price.unset", "en", product="Sakthi Curd 500 ml") and "rupee" not in r["say"]


# ---------------------------------------------------------------------------------------------------- the board

async def test_board_groups_by_category_in_board_order_and_says_at_most_a_page(store, session):
    r = await price_query()
    assert r["ok"] is True
    assert len(r["spoken"]) <= PAGE_LIMIT
    cats = [i["category"] for i in r["items"]]
    assert cats == sorted(cats, key=lambda c: (*CATEGORIES, "other").index(c))
    assert r["say"].startswith("Milk: Aavin Milk 500 ml 44, Aavin Milk 1 L 44. Curd: ")
    assert r["has_more"] is True and r["say"].endswith(t("k3.price.more", "en"))
    # Every spoken number is a store price.
    docs = dict(await store.list(f"shops/{SHOP}/products"))
    for i in r["spoken"]:
        assert i["price"] == docs[i["sku"]]["price"]


async def test_say_more_reads_the_rest_then_stops(store, session):
    first = await price_query()
    rest = await price_query(page=1)
    assert rest["ok"] is True
    assert {i["sku"] for i in first["spoken"]} | {i["sku"] for i in rest["spoken"]} == {i["sku"] for i in first["items"]}
    assert not ({i["sku"] for i in first["spoken"]} & {i["sku"] for i in rest["spoken"]})
    assert rest["has_more"] is False and t("k3.price.more", "en") not in rest["say"]
    assert "Ice cream: Arun Ice Cream Family Box 180." in rest["say"]
    end = await price_query(page=2)
    assert end["ok"] is True and end["spoken"] == [] and end["say"] == t("k3.price.end", "en")


async def test_board_skips_disabled_products_and_those_without_a_price(store, session):
    milk = await store.get(f"shops/{SHOP}/products/aavin_milk_1l")
    await store.set(f"shops/{SHOP}/products/aavin_milk_1l", {**milk, "active": False})
    butter = await store.get(f"shops/{SHOP}/products/aavin_butter_100g")
    butter.pop("price")
    await store.set(f"shops/{SHOP}/products/aavin_butter_100g", butter)
    r = await price_query()
    skus = {i["sku"] for i in r["items"]}
    assert "aavin_milk_1l" not in skus and "aavin_butter_100g" not in skus
    assert r["say"].startswith("Milk: Aavin Milk 500 ml 44. Curd: ")


async def test_board_in_tamil_uses_tamil_category_names(store, session):
    _session("ta")
    r = await price_query()
    assert r["say"].startswith(t("k3.cat.milk", "ta") + ": ")
    assert r["say"].endswith(t("k3.price.more", "ta"))


async def test_empty_shop_says_no_prices(store, session):
    for sku, doc in await store.list(f"shops/{SHOP}/products"):
        await store.set(f"shops/{SHOP}/products/{sku}", {**doc, "active": False})
    r = await price_query()
    assert r["ok"] is True and r["items"] == [] and r["say"] == t("k3.price.none", "en")
