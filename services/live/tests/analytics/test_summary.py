"""analytics.summary: pure day aggregation plus the Firestore reads for one IST business day."""
import operator
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from aira_live.analytics import summary as sm

IST = ZoneInfo("Asia/Kolkata")

# Units as in content/catalog/murugan_dairy.json: milk and curd are stocked, sold and forecast in litres.
PRODUCTS = {"sakthi_curd_1l": {"name": "Sakthi curd", "unit": "L", "reorderThreshold": 5},
            "aavin_milk_500ml": {"name": "Aavin milk", "unit": "L", "packSizeL": 0.5, "reorderThreshold": 10}}


# ---------- spoken quantities always carry their selling unit ----------

def test_amount_speaks_the_selling_unit():
    assert sm.amount(23, "L", "en") == "23 litres"
    assert sm.amount(1, "L", "en") == "1 litre"
    assert sm.amount(3.5, "L", "en") == "3.5 litres"
    assert sm.amount(6.0, "pack", "en") == "6 packs"
    assert sm.amount(1, "box", "en") == "1 box" and sm.amount(2, "box", "en") == "2 boxes"
    assert sm.amount(2, "jar", "en") == "2 jars"


def test_amount_without_a_unit_uses_the_ledger_default_pack_and_never_drops_an_unknown_unit():
    assert sm.amount(2, None, "en") == "2 packs"            # business.ledger stores products with no unit as packs
    assert sm.amount(4, "crate", "en") == "4 crate"         # spoken as stored, not guessed


def test_amount_in_tamil_and_hindi():
    assert sm.amount(23, "L", "ta") == "23 லிட்டர்" and sm.amount(6, "pack", "ta") == "6 பாக்கெட்"
    assert sm.amount(23, "L", "hi") == "23 लीटर" and sm.amount(6, "pack", "hi") == "6 पैकेट"


def test_say_speaks_suggested_orders_in_litres_not_bare_numbers():
    """23 is litres of 500 ml pouches (46 pouches): '23 Aavin Milk 500 ml' would be heard as 23 pouches."""
    d = {"sales_count": 0, "names": {"aavin_milk_500ml": "Aavin Milk 500 ml", "aavin_ghee_200ml": "Aavin Ghee"},
         "suggested_orders": [{"sku": "aavin_milk_500ml", "qty": 23, "unit": "L"},
                              {"sku": "aavin_ghee_200ml", "qty": 2, "unit": "jar"}]}
    assert sm.say(d, "en") == ("No sales recorded today yet. "
                               "Suggested order for tomorrow: 23 litres of Aavin Milk 500 ml, 2 jars of Aavin Ghee.")
    assert "Aavin Milk 500 ml 23 லிட்டர்" in sm.say(d, "ta")
    assert "23 लीटर Aavin Milk 500 ml" in sm.say(d, "hi")


def test_say_marks_a_suggestion_built_on_demo_history():
    d = {"sales_count": 0, "names": {"aavin_ghee_200ml": "Aavin Ghee"}, "demo_history": True,
         "suggested_orders": [{"sku": "aavin_ghee_200ml", "qty": 2, "unit": "jar"}]}
    assert sm.say(d, "en") == ("No sales recorded today yet. Based on demo sales history. "
                               "Suggested order for tomorrow: 2 jars of Aavin Ghee.")
    assert sm.say({**d, "suggested_orders": []}, "en") == "No sales recorded today yet."  # nothing to mark
    assert sm.say({**d, "demo_history": False}, "en") == ("No sales recorded today yet. "
                                                          "Suggested order for tomorrow: 2 jars of Aavin Ghee.")


def test_suggest_qty_rounds_up_with_safety_and_never_negative():
    assert sm.suggest_qty(26.0, 2) == 28        # ceil(26 * 1.15 - 2) = ceil(27.9)
    assert sm.suggest_qty(5.0, 40) == 0
    assert sm.suggest_qty(None, 0) == 0


def test_suggest_qty_ignores_float_noise():
    assert sm.suggest_qty(20.0, 0) == 23        # 20 * 1.15 is not exactly 23.0 in binary floats
    assert sm.suggest_qty(10.0, 1.5) == 10      # ceil(11.5 - 1.5) = 10, not 11


def test_summarize_counts_only_completed_sales():
    sales = [
        {"state": "SALE_COMPLETED", "total": 120, "items": [{"sku": "sakthi_curd_1l", "qty": 2}]},
        {"state": "SALE_COMPLETED", "total": 54,
         "items": [{"sku": "aavin_milk_500ml", "qty": 2}, {"sku": "sakthi_curd_1l", "qty": 1}]},
        {"state": "SALE_VOID", "total": 999, "items": [{"sku": "aavin_milk_500ml", "qty": 50}]},
    ]
    inventory = {"sakthi_curd_1l": {"onHand": 2}, "aavin_milk_500ml": {"onHand": 30}}
    out = sm.summarize(sales, PRODUCTS, inventory, {"sakthi_curd_1l": 26.0, "aavin_milk_500ml": 20.0}, 1)
    assert out["sales_count"] == 2 and out["revenue"] == 174
    assert out["top_sku"] == "sakthi_curd_1l" and out["qty_by_sku"]["sakthi_curd_1l"] == 3
    assert out["low_stock"] == ["sakthi_curd_1l"]
    assert out["suggested_orders"] == [{"sku": "sakthi_curd_1l", "qty": 28, "unit": "L"}]
    assert out["open_discrepancies"] == 1


def test_summarize_suggestion_without_a_product_unit_is_in_packs():
    products = {"aavin_paneer_200g": {"name": "Aavin paneer", "reorderThreshold": 3}}
    out = sm.summarize([], products, {}, {"aavin_paneer_200g": 3.0}, 0)
    assert out["suggested_orders"] == [{"sku": "aavin_paneer_200g", "qty": 4, "unit": "pack"}]


def test_summarize_empty_day():
    out = sm.summarize([], PRODUCTS, {}, {}, 0)
    assert out["sales_count"] == 0 and out["top_sku"] is None and out["suggested_orders"] == []
    assert out["revenue"] == 0 and out["qty_by_sku"] == {}


def test_summarize_never_suggests_without_a_forecast():
    out = sm.summarize([], PRODUCTS, {}, {"sakthi_curd_1l": None}, 0)
    assert out["suggested_orders"] == []
    assert out["low_stock"] == ["aavin_milk_500ml", "sakthi_curd_1l"]   # no inventory doc = nothing on hand


# ---------- today(): Firestore reads for the IST business day (in-memory fake with where()) ----------

_OPS = {"==": operator.eq, ">=": operator.ge, "<": operator.lt}


class _Doc:
    def __init__(self, doc_id: str, data: dict):
        self.id, self._data = doc_id, data

    def to_dict(self) -> dict:
        return dict(self._data)


class _Query:
    def __init__(self, docs: list[_Doc], log: list):
        self._docs, self._log = docs, log

    def where(self, *args, filter=None):
        if filter is not None:  # google-cloud-firestore FieldFilter
            field, op, value = filter.field_path, filter.op_string, filter.value
        else:
            field, op, value = args
        self._log.append((field, op))
        return _Query([d for d in self._docs
                       if d._data.get(field) is not None and _OPS[op](d._data[field], value)], self._log)

    async def stream(self):
        for d in self._docs:
            yield d


class _FakeDb:
    def __init__(self, shops: dict[str, dict]):
        self._shops = shops
        self.queries: list[tuple[str, str]] = []

    def collection(self, name: str):
        assert name == "shops"

        def document(shop_id):
            data = self._shops[shop_id]
            return SimpleNamespace(
                collection=lambda col: _Query([_Doc(k, v) for k, v in data.get(col, {}).items()], self.queries))
        return SimpleNamespace(document=document)


@pytest.fixture
def forecasts(monkeypatch):
    values: dict[str, float | None] = {}

    async def daily_demand(shop_id, sku):
        assert shop_id == "murugan_dairy"
        return values.get(sku)
    monkeypatch.setattr(sm.forecast, "daily_demand", daily_demand)
    return values


async def test_today_reads_only_this_ist_day(monkeypatch, forecasts):
    now = datetime(2026, 10, 13, 20, 0, tzinfo=IST)
    start = datetime.combine(now.date(), time.min, tzinfo=IST)
    shop = {
        "products": PRODUCTS,
        "inventory": {"sakthi_curd_1l": {"onHand": 2}, "aavin_milk_500ml": {"onHand": 30}},
        "sales": {
            "s1": {"state": "SALE_COMPLETED", "total": 120, "completedAt": start + timedelta(hours=9),
                   "items": [{"sku": "sakthi_curd_1l", "qty": 2}]},
            "s2": {"state": "SALE_COMPLETED", "total": 999, "completedAt": start - timedelta(minutes=1),
                   "items": [{"sku": "aavin_milk_500ml", "qty": 40}]},          # yesterday 23:59 IST
            "s3": {"state": "SALE_PENDING", "total": 60, "items": [{"sku": "sakthi_curd_1l", "qty": 1}]},
        },
        "orders": {"o1": {"state": "DISCREPANCY"}, "o2": {"state": "CLOSED"}},
    }
    fake = _FakeDb({"murugan_dairy": shop})
    monkeypatch.setattr(sm, "db", lambda: fake)
    forecasts.update({"sakthi_curd_1l": 26.0, "aavin_milk_500ml": 20.0})

    out = await sm.today("murugan_dairy", now=now)
    assert out["sales_count"] == 1 and out["revenue"] == 120 and out["top_sku"] == "sakthi_curd_1l"
    assert out["open_discrepancies"] == 1 and out["low_stock"] == ["sakthi_curd_1l"]
    assert out["suggested_orders"] == [{"sku": "sakthi_curd_1l", "qty": 28, "unit": "L"}]
    assert out["names"] == {"sakthi_curd_1l": "Sakthi curd", "aavin_milk_500ml": "Aavin milk"}
    # Single-field filters only: Firestore indexes them automatically, so no composite index is needed.
    assert sorted(fake.queries) == [("completedAt", ">="), ("state", "==")]


async def test_today_utc_clock_still_uses_the_ist_day(monkeypatch, forecasts):
    # 20:00 UTC on 12 Oct is 01:30 IST on 13 Oct: a sale at 00:30 IST on the 13th is "today".
    now_utc = datetime(2026, 10, 12, 20, 0, tzinfo=ZoneInfo("UTC"))
    shop = {"products": PRODUCTS, "inventory": {}, "orders": {},
            "sales": {"s1": {"state": "SALE_COMPLETED", "total": 44,
                             "completedAt": datetime(2026, 10, 13, 0, 30, tzinfo=IST),
                             "items": [{"sku": "aavin_milk_500ml", "qty": 1}]}}}
    monkeypatch.setattr(sm, "db", lambda: _FakeDb({"murugan_dairy": shop}))
    out = await sm.today("murugan_dairy", now=now_utc)
    assert out["sales_count"] == 1 and out["revenue"] == 44


async def test_today_carries_the_demo_history_flag(monkeypatch, forecasts):
    """Backfilled demo rows feeding the forecast make the day summary say "demo history" (summary.say)."""
    shop = {"products": PRODUCTS, "inventory": {}, "sales": {}, "orders": {}}
    monkeypatch.setattr(sm, "db", lambda: _FakeDb({"murugan_dairy": shop}))

    async def demo(shop_id):
        assert shop_id == "murugan_dairy"
        return True

    monkeypatch.setattr(sm.forecast, "demo_history", demo)
    assert (await sm.today("murugan_dairy"))["demo_history"] is True


async def test_today_without_forecast_suggests_nothing(monkeypatch, forecasts):
    shop = {"products": PRODUCTS, "inventory": {}, "sales": {}, "orders": {}}
    monkeypatch.setattr(sm, "db", lambda: _FakeDb({"murugan_dairy": shop}))
    out = await sm.today("murugan_dairy")
    assert out["sales_count"] == 0 and out["suggested_orders"] == [] and out["top_sku"] is None
