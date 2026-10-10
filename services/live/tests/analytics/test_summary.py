"""analytics.summary: pure day aggregation plus the Firestore reads for one IST business day."""
import operator
from datetime import datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from aira_live.analytics import summary as sm

IST = ZoneInfo("Asia/Kolkata")

PRODUCTS = {"sakthi_curd_1l": {"name": "Sakthi curd", "reorderThreshold": 5},
            "aavin_milk_500ml": {"name": "Aavin milk", "reorderThreshold": 10}}


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
    assert out["suggested_orders"] == [{"sku": "sakthi_curd_1l", "qty": 28}]
    assert out["open_discrepancies"] == 1


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
    assert out["suggested_orders"] == [{"sku": "sakthi_curd_1l", "qty": 28}]
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


async def test_today_without_forecast_suggests_nothing(monkeypatch, forecasts):
    shop = {"products": PRODUCTS, "inventory": {}, "sales": {}, "orders": {}}
    monkeypatch.setattr(sm, "db", lambda: _FakeDb({"murugan_dairy": shop}))
    out = await sm.today("murugan_dairy")
    assert out["sales_count"] == 0 and out["suggested_orders"] == [] and out["top_sku"] is None
