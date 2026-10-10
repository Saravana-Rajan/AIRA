"""Live tools day_summary and reorder_suggest: they read and suggest, never order or commit."""
import importlib
import json
import string
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from aira_live.analytics import forecast
from aira_live.session import current_session
from aira_live.tools import TOOLS_BY_NAME, insights

I18N = Path(insights.__file__).resolve().parents[1] / "i18n"


def _business(monkeypatch, name: str):
    """aira_live.business.<name> (Kanish). A scoped empty stand-in only while that module is not merged yet."""
    full = f"aira_live.business.{name}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as e:
        if e.name != full:
            raise
        mod = types.ModuleType(full)
        monkeypatch.setitem(sys.modules, full, mod)
        return mod


@pytest.fixture
def session(mk_session):
    s, _ = mk_session(shop="murugan_dairy", lang="en")
    tok = current_session.set(s)
    yield s
    current_session.reset(tok)


@pytest.fixture
def shop(monkeypatch, fake_db):
    """The demo shop in a FakeFirestore; tests fill in forecasts and stock per SKU."""
    fake_db.store.update({
        # Sold, stocked and forecast in litres, as in content/catalog/murugan_dairy.json.
        "shops/murugan_dairy/products/sakthi_curd_1l": {"name": "Sakthi curd", "unit": "L", "reorderThreshold": 5},
        "shops/murugan_dairy/suppliers/sakthi_vendor": {"name": "Sakthi vendor", "products": ["sakthi_curd_1l"]},
    })
    state = {"db": fake_db, "forecast": {}, "on_hand": {}, "on_hand_calls": []}

    async def daily_demand(shop_id, sku):
        return state["forecast"].get(sku)

    async def on_hand(shop_id, sku):
        state["on_hand_calls"].append((shop_id, sku))
        return state["on_hand"].get(sku, 0.0)

    monkeypatch.setattr(forecast, "daily_demand", daily_demand)
    monkeypatch.setattr(_business(monkeypatch, "ledger"), "on_hand", on_hand, raising=False)
    return state


def test_registered_under_the_interface_names():
    assert TOOLS_BY_NAME["day_summary"] is insights.day_summary
    assert TOOLS_BY_NAME["reorder_suggest"] is insights.reorder_suggest


# ---------- day_summary ----------

def _day(**over) -> dict:
    base = {"sales_count": 0, "revenue": 0, "top_sku": None, "qty_by_sku": {}, "low_stock": [],
            "open_discrepancies": 0, "suggested_orders": [], "names": {}}
    return {**base, **over}


async def test_day_summary_speaks_counts(monkeypatch, session):
    seen = {}

    async def fake_today(shop_id):
        seen["shop"] = shop_id
        return _day(sales_count=48, revenue=2860, top_sku="aavin_milk_500ml",
                    suggested_orders=[{"sku": "sakthi_curd_1l", "qty": 30, "unit": "L"}],
                    names={"aavin_milk_500ml": "Aavin milk", "sakthi_curd_1l": "Sakthi curd"})
    monkeypatch.setattr(insights.summary, "today", fake_today)
    out = await insights.day_summary()
    assert seen["shop"] == "murugan_dairy"
    assert out["say"] == ("Today: 48 sales, ₹2860. Top seller: Aavin milk. "
                          "Suggested order for tomorrow: 30 litres of Sakthi curd.")
    assert out["sales_count"] == 48 and out["suggested_orders"][0]["qty"] == 30


async def test_day_summary_no_sales_yet(monkeypatch, session):
    async def fake_today(shop_id):
        return _day()
    monkeypatch.setattr(insights.summary, "today", fake_today)
    out = await insights.day_summary()
    assert out["say"] == "No sales recorded today yet."


async def test_day_summary_speaks_tamil(monkeypatch, session):
    session.lang = "ta"

    async def fake_today(shop_id):
        return _day(sales_count=3, revenue=180, top_sku="sakthi_curd_1l", names={"sakthi_curd_1l": "Sakthi curd"})
    monkeypatch.setattr(insights.summary, "today", fake_today)
    out = await insights.day_summary()
    assert "180" in out["say"] and "Sakthi curd" in out["say"] and "விற்பனை" in out["say"]


async def test_day_summary_when_records_cannot_be_read_says_so(monkeypatch, session):
    async def broken(shop_id):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(insights.summary, "today", broken)
    out = await insights.day_summary()
    assert out == {"say": "I can't read the shop records right now. Please try again.", "error": "unavailable"}


# ---------- reorder_suggest ----------

async def test_reorder_suggest_no_history_says_not_enough_data(shop, session):
    shop["on_hand"]["sakthi_curd_1l"] = 20.0
    out = await insights.reorder_suggest()
    assert out == {"say": "I don't have enough sales history yet to suggest an order.",
                   "suggestions": [], "next_tool": None}


async def test_reorder_suggest_low_stock_suggests_order(shop, session):
    shop["forecast"]["sakthi_curd_1l"] = 26.0
    shop["on_hand"]["sakthi_curd_1l"] = 2.0
    out = await insights.reorder_suggest()
    assert out["next_tool"] == "order_create"
    assert out["suggestions"] == [{"sku": "sakthi_curd_1l", "name": "Sakthi curd", "qty": 28, "unit": "L",
                                   "supplier": "Sakthi vendor", "forecast": 26.0, "on_hand": 2.0}]
    assert out["say"] == ("Sakthi curd: 2 litres left, you sell about 26 litres a day. "
                          "Order 28 litres from Sakthi vendor?")
    assert shop["on_hand_calls"] == [("murugan_dairy", "sakthi_curd_1l")]


async def test_reorder_suggest_half_litre_pouches_are_spoken_in_litres(shop, session):
    """Milk 500 ml is stocked and forecast in litres: 'Order 20' would be heard as 20 pouches, not 40."""
    shop["db"].store["shops/murugan_dairy/products/aavin_milk_500ml"] = {
        "name": "Aavin Milk 500 ml", "unit": "L", "packSizeL": 0.5, "reorderThreshold": 5}
    shop["forecast"]["aavin_milk_500ml"] = 20.0
    shop["on_hand"]["aavin_milk_500ml"] = 3.0
    out = await insights.reorder_suggest()
    milk = next(x for x in out["suggestions"] if x["sku"] == "aavin_milk_500ml")
    assert milk["qty"] == 20 and milk["unit"] == "L"                    # ceil(20 * 1.15 - 3) litres = 40 pouches
    assert "Aavin Milk 500 ml: 3 litres left, you sell about 20 litres a day. Order 20 litres?" in out["say"]


async def test_reorder_suggest_product_without_a_unit_is_spoken_in_packs(shop, session):
    shop["db"].store["shops/murugan_dairy/products/aavin_paneer_200g"] = {"name": "Aavin paneer"}
    shop["forecast"]["aavin_paneer_200g"] = 1.0
    out = await insights.reorder_suggest()
    assert "Aavin paneer: 0 packs left, you sell about 1 pack a day. Order 2 packs?" in out["say"]


async def test_reorder_suggest_only_suggests_never_writes(shop, session):
    shop["forecast"]["sakthi_curd_1l"] = 26.0
    before = dict(shop["db"].store)
    out = await insights.reorder_suggest()
    assert out["suggestions"] and shop["db"].store == before
    assert session._send.msgs == []          # no confirm prompt: order_create runs its own read-back


async def test_reorder_suggest_stock_fine(shop, session):
    shop["forecast"]["sakthi_curd_1l"] = 5.0
    shop["on_hand"]["sakthi_curd_1l"] = 40.0
    out = await insights.reorder_suggest()
    assert out == {"say": "Stock looks fine. Nothing to order now.", "suggestions": [], "next_tool": None}


async def test_reorder_suggest_without_supplier_does_not_say_from(shop, session):
    del shop["db"].store["shops/murugan_dairy/suppliers/sakthi_vendor"]
    shop["forecast"]["sakthi_curd_1l"] = 25.8
    out = await insights.reorder_suggest()
    assert out["suggestions"][0]["supplier"] is None
    # ceil(25.8 * 1.15) = 30
    assert out["say"] == "Sakthi curd: 0 litres left, you sell about 25.8 litres a day. Order 30 litres?"


async def test_reorder_suggest_skips_products_without_history(shop, session):
    shop["db"].store["shops/murugan_dairy/products/aavin_ghee_200ml"] = {"name": "Aavin ghee"}
    shop["forecast"]["sakthi_curd_1l"] = 26.0
    out = await insights.reorder_suggest()
    assert [x["sku"] for x in out["suggestions"]] == ["sakthi_curd_1l"]
    assert "ghee" not in out["say"].lower()


async def test_reorder_suggest_one_product(monkeypatch, shop, session):
    shop["db"].store["shops/murugan_dairy/products/aavin_milk_500ml"] = {"name": "Aavin milk", "reorderThreshold": 10}
    shop["forecast"].update({"sakthi_curd_1l": 26.0, "aavin_milk_500ml": 40.0})
    spoken = []

    async def resolve(shop_id, text, lang):
        spoken.append((shop_id, text, lang))
        return SimpleNamespace(sku="aavin_milk_500ml", name="Aavin milk") if "milk" in text else None
    monkeypatch.setattr(_business(monkeypatch, "catalog"), "resolve", resolve, raising=False)

    out = await insights.reorder_suggest("aavin milk")
    assert [x["sku"] for x in out["suggestions"]] == ["aavin_milk_500ml"]
    assert spoken == [("murugan_dairy", "aavin milk", "en")]

    out = await insights.reorder_suggest("rasmalai")
    assert out == {"say": "I couldn't find rasmalai in your shop.", "suggestions": [], "next_tool": None}


async def test_reorder_suggest_speaks_tamil(shop, session):
    session.lang = "ta"
    shop["forecast"]["sakthi_curd_1l"] = 26.0
    shop["on_hand"]["sakthi_curd_1l"] = 2.0
    out = await insights.reorder_suggest()
    assert "Sakthi vendor" in out["say"] and "insights." not in out["say"] and "units." not in out["say"]
    assert "ஆர்டர்" in out["say"] and "28 லிட்டர்" in out["say"] and "2 லிட்டர்" in out["say"]


async def test_reorder_suggest_when_records_cannot_be_read_says_so(monkeypatch, shop, session):
    async def broken(shop_id, sku):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(_business(monkeypatch, "ledger"), "on_hand", broken)
    shop["forecast"]["sakthi_curd_1l"] = 26.0
    out = await insights.reorder_suggest()
    assert out == {"say": "I can't read the shop records right now. Please try again.",
                   "suggestions": [], "next_tool": None, "error": "unavailable"}


# ---------- strings ----------

KEYS = ["insights.day_summary", "insights.no_sales", "insights.tomorrow_order", "insights.reorder_item",
        "insights.reorder_item_no_supplier", "insights.not_enough_data", "insights.nothing_to_order",
        "insights.unknown_product", "insights.unavailable",
        "units.item", "units.L", "units.L.one", "units.pack", "units.pack.one", "units.jar", "units.jar.one",
        "units.box", "units.box.one"]


def test_insights_keys_exist_in_every_language_with_the_same_placeholders():
    def fields(text: str) -> set[str]:
        return {f for _, f, _, _ in string.Formatter().parse(text) if f}

    catalogs = {lang: json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8")) for lang in ("en", "ta", "hi")}
    for key in KEYS:
        want = fields(catalogs["en"][key])
        for lang in ("ta", "hi"):
            assert fields(catalogs[lang][key]) == want, (lang, key)
