"""Demand forecast: 7-day moving average of SALE ledger rows in Firestore (build overrides §1)."""
import asyncio
import importlib.util
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from aira_live.analytics import forecast as fc

IST = ZoneInfo("Asia/Kolkata")
SHOP = "sunrise_dairy"
TODAY = date(2026, 10, 13)
BACKFILL = Path(__file__).resolve().parents[3] / "shared" / "scripts" / "backfill_demo_sales.py"


def days_back(n, qty=10.0):
    return {TODAY - timedelta(days=i): qty for i in range(1, n + 1)}


def at_ist(day, hour=12, minute=0):
    """ISO-8601 UTC string, as business.ledger writes `at`, for a local IST wall-clock time."""
    return datetime.combine(day, time(hour, minute), IST).astimezone(timezone.utc).isoformat()


def sale(sku, qty, day, hour=12):
    return {"sku": sku, "delta": -float(qty), "reason": "SALE", "refType": "sale", "refId": "s",
            "idemKey": "k", "at": at_ist(day, hour), "actor": "aira"}


# ---------- a tiny in-memory stand-in for the Firestore AsyncClient ----------

class _Snap:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)


def _kind(v):  # Firestore compares values only within one type
    return "timestamp" if isinstance(v, datetime) else type(v).__name__


class _Query:
    def __init__(self, db, path, filters=()):
        self.db, self.path, self.filters = db, path, list(filters)

    def where(self, *args, filter=None):
        f = (filter.field_path, filter.op_string, filter.value) if filter is not None else tuple(args)
        assert f[1] == ">=", f
        return _Query(self.db, self.path, [*self.filters, f])

    def _match(self, data):
        for field, _, bound in self.filters:
            have = data.get(field)
            if have is None or _kind(have) != _kind(bound) or not have >= bound:
                return False
        return True

    async def stream(self):
        self.db.streams.append(self.path)
        if self.db.fail:
            raise RuntimeError("firestore unavailable")
        for doc_id, data in list(self.db.docs.get(self.path, {}).items()):
            if self._match(data):
                yield _Snap(doc_id, data)


class _Doc:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def collection(self, name):
        return _Query(self.db, f"{self.path}/{name}")


class _Root(_Query):
    def document(self, doc_id):
        return _Doc(self.db, f"{self.path}/{doc_id}")


class FakeDb:
    def __init__(self):
        self.docs: dict[str, dict[str, dict]] = {}
        self.streams: list[str] = []
        self.fail = False

    def collection(self, name):
        return _Root(self, name)

    def add(self, path, data):
        col, _, doc_id = path.rpartition("/")
        self.docs.setdefault(col, {})[doc_id] = data

    def add_sales(self, rows):
        col = f"shops/{SHOP}/ledger"
        for row in rows:
            self.add(f"{col}/t{len(self.docs.get(col, {}))}", row)


@pytest.fixture
def ledger(monkeypatch):
    fake = FakeDb()
    fc.clear_cache()
    monkeypatch.delenv("AIRA_DEMO_SHOP", raising=False)
    monkeypatch.setattr(fc.config, "load",
                        lambda: SimpleNamespace(mock=False, gcp_project="demo-aira", demo_shop_id=SHOP))
    monkeypatch.setattr(fc, "_today", lambda: TODAY)
    monkeypatch.setattr(fc, "_db", lambda: fake)
    yield fake
    fc.clear_cache()


# ---------- pure moving average ----------

def test_moving_average_full_week_counts_missing_days_as_zero():
    daily = {TODAY - timedelta(days=1): 14.0, TODAY - timedelta(days=7): 7.0}
    assert fc.moving_average_7(daily, TODAY, active_days=days_back(7)) == 3.0      # (14 + 7) / 7


def test_moving_average_none_with_fewer_than_three_days_of_data():
    assert fc.MIN_DAYS == 3
    assert fc.moving_average_7(days_back(2), TODAY) is None


def test_moving_average_none_without_recent_history():
    assert fc.moving_average_7({TODAY - timedelta(days=30): 9.0}, TODAY) is None
    assert fc.moving_average_7({}, TODAY) is None


def test_moving_average_short_history_averages_over_its_own_days():
    assert fc.moving_average_7(days_back(3, 6.0), TODAY) == 6.0                    # 18 / 3, not 18 / 7


def test_moving_average_slow_mover_counts_shop_days_as_zero():
    daily = {TODAY - timedelta(days=2): 2.0}
    assert fc.moving_average_7(daily, TODAY, active_days=days_back(7)) == 0.29     # 2 / 7


def test_moving_average_ignores_today_and_days_before_the_window():
    daily = {**days_back(7, 4.0), TODAY: 100.0, TODAY - timedelta(days=8): 100.0}
    assert fc.moving_average_7(daily, TODAY) == 4.0


def test_ist_day_parses_iso_strings_and_datetimes():
    assert fc.ist_day("2026-10-12T20:00:00+00:00") == date(2026, 10, 13)          # 01:30 IST
    assert fc.ist_day("2026-10-12T12:30:00Z") == date(2026, 10, 12)
    assert fc.ist_day(datetime(2026, 10, 12, 19, 0, tzinfo=timezone.utc)) == date(2026, 10, 13)
    assert fc.ist_day(datetime(2026, 10, 12, 10, 0)) == date(2026, 10, 12)         # naive → UTC
    assert fc.ist_day("not a time") is None and fc.ist_day(None) is None


def test_fold_sales_counts_only_sale_rows():
    d1, d2 = TODAY - timedelta(days=1), TODAY - timedelta(days=2)
    rows = [sale("a", 2, d1), sale("a", 3, d1), sale("b", 1.5, d2),
            {**sale("a", 30, d1), "reason": "DELIVERY", "delta": 30.0},
            {**sale("b", 1, d1), "reason": "ADJUST"},
            {"reason": "SALE", "delta": -1.0, "at": at_ist(d1)}]                     # no sku → skipped
    by_sku, active, demo = fc.fold_sales(rows)
    assert by_sku == {"a": {d1: 5.0}, "b": {d2: 1.5}}
    assert active == {d1, d2}
    assert demo is False


def test_fold_sales_flags_demo_backfill_rows():
    """shared/scripts/backfill_demo_sales.py writes invented SALE rows labelled demo:true (refType demo_backfill). They
    feed the number, and the window says so, so every suggestion built on them is spoken and shown as demo
    history rather than real shop sales."""
    d1 = TODAY - timedelta(days=1)
    rows = [sale("a", 2, d1), {**sale("a", 30, d1), "refType": fc.DEMO_REF_TYPE, "demo": True}]
    assert fc.fold_sales(rows) == ({"a": {d1: 32.0}}, {d1}, True)
    assert fc.fold_sales([sale("a", 2, d1), {**sale("a", 1, d1), "demo": True}])[2] is True   # label alone
    assert fc.fold_sales([sale("a", 2, d1), {**sale("a", 1, d1), "refType": fc.DEMO_REF_TYPE}])[2] is True
    assert fc.is_demo_row({"demo": True}) and fc.is_demo_row({"refType": "demo_backfill"})
    assert not fc.is_demo_row({"demo": False, "refType": "sale"})


# ---------- daily_demand against the Firestore ledger ----------

@pytest.mark.asyncio
async def test_daily_demand_is_moving_average_of_sale_ledger_rows(ledger):
    ledger.add_sales([sale("sakthi_curd_1l", i, TODAY - timedelta(days=i)) for i in range(1, 8)])  # 1..7
    ledger.add_sales([
        {**sale("sakthi_curd_1l", 30, TODAY - timedelta(days=1)), "reason": "DELIVERY", "delta": 30.0},
        sale("sakthi_curd_1l", 50, TODAY),                                         # today: partial day
        sale("sakthi_curd_1l", 50, TODAY - timedelta(days=20)),                    # outside the window
    ])
    assert await fc.daily_demand(SHOP, "sakthi_curd_1l") == 4.0                    # 28 / 7


@pytest.mark.asyncio
async def test_daily_demand_none_with_fewer_than_three_days_of_data(ledger):
    ledger.add_sales([sale("sakthi_curd_1l", 9, TODAY - timedelta(days=i)) for i in (1, 2)])
    assert await fc.daily_demand(SHOP, "sakthi_curd_1l") is None


@pytest.mark.asyncio
async def test_daily_demand_none_without_any_history(ledger):
    assert await fc.daily_demand(SHOP, "sakthi_curd_1l") is None


@pytest.mark.asyncio
async def test_daily_demand_zero_for_sku_unsold_in_an_active_week(ledger):
    ledger.add_sales([sale("aavin_milk_1l", 5, TODAY - timedelta(days=i)) for i in range(1, 8)])
    assert await fc.daily_demand(SHOP, "aavin_ghee_500ml") == 0.0


@pytest.mark.asyncio
async def test_daily_demand_accepts_timestamp_at(ledger):
    rows = []
    for i in range(1, 8):
        row = sale("aavin_milk_500ml", 7, TODAY - timedelta(days=i))
        row["at"] = datetime.fromisoformat(row["at"])                              # Firestore Timestamp
        rows.append(row)
    ledger.add_sales(rows)
    assert await fc.daily_demand(SHOP, "aavin_milk_500ml") == 7.0


@pytest.mark.asyncio
async def test_daily_demand_shares_one_ledger_read_across_skus(ledger):
    ledger.add_sales([sale(sku, 3, TODAY - timedelta(days=i))
                      for i in range(1, 8) for sku in ("aavin_milk_1l", "sakthi_curd_1l", "arun_icecream_box")])
    skus = ["aavin_milk_1l", "sakthi_curd_1l", "arun_icecream_box", "aavin_paneer_200g"]
    values = await asyncio.gather(*(fc.daily_demand(SHOP, s) for s in skus))
    assert values == [3.0, 3.0, 3.0, 0.0]
    reads = len(ledger.streams)
    assert reads <= 2
    assert await fc.daily_demand(SHOP, "aavin_milk_1l") == 3.0 and len(ledger.streams) == reads


@pytest.mark.asyncio
async def test_daily_demand_returns_none_in_mock(ledger, monkeypatch):
    monkeypatch.setattr(fc.config, "load", lambda: SimpleNamespace(mock=True, gcp_project=None))
    ledger.add_sales([sale("sakthi_curd_1l", 9, TODAY - timedelta(days=i)) for i in range(1, 8)])
    assert await fc.daily_demand(SHOP, "sakthi_curd_1l") is None
    assert ledger.streams == []


@pytest.mark.asyncio
async def test_daily_demand_returns_none_without_project(ledger, monkeypatch):
    monkeypatch.setattr(fc.config, "load", lambda: SimpleNamespace(mock=False, gcp_project=None))
    assert await fc.daily_demand(SHOP, "sakthi_curd_1l") is None
    assert ledger.streams == []


@pytest.mark.asyncio
async def test_daily_demand_none_when_firestore_fails_and_retries_next_call(ledger):
    ledger.add_sales([sale("sakthi_curd_1l", 4, TODAY - timedelta(days=i)) for i in range(1, 8)])
    ledger.fail = True
    assert await fc.daily_demand(SHOP, "sakthi_curd_1l") is None
    ledger.fail = False
    assert await fc.daily_demand(SHOP, "sakthi_curd_1l") == 4.0                    # failure is not cached


@pytest.mark.asyncio
async def test_refresh_model_recomputes_every_product_of_the_demo_shop(ledger):
    for sku in ("aavin_milk_500ml", "sakthi_curd_1l", "aavin_ghee_500ml"):
        ledger.add(f"shops/{SHOP}/products/{sku}", {"sku": sku})
    ledger.add_sales([sale("sakthi_curd_1l", 2, TODAY - timedelta(days=i)) for i in range(1, 8)])
    assert await fc.daily_demand(SHOP, "aavin_milk_500ml") == 0.0                  # cached window
    ledger.add_sales([sale("aavin_milk_500ml", 14, TODAY - timedelta(days=1))])
    result = await fc.refresh_model()
    assert result == {"aavin_ghee_500ml": 0.0, "aavin_milk_500ml": 2.0, "sakthi_curd_1l": 2.0}
    assert await fc.daily_demand(SHOP, "aavin_milk_500ml") == 2.0


@pytest.mark.asyncio
async def test_daily_demand_all_includes_sold_skus_missing_from_products(ledger):
    ledger.add(f"shops/{SHOP}/products/aavin_milk_1l", {"sku": "aavin_milk_1l"})
    ledger.add_sales([sale("aavin_butter_100g", 1, TODAY - timedelta(days=i)) for i in range(1, 8)])
    assert await fc.daily_demand_all(SHOP) == {"aavin_butter_100g": 1.0, "aavin_milk_1l": 0.0}


@pytest.mark.asyncio
async def test_backfilled_demo_sales_give_a_plausible_forecast_and_are_flagged(ledger):
    bf = _backfill_module()
    assert await fc.demo_history(SHOP) is False  # nothing backfilled yet
    fc.clear_cache()
    products = [{"sku": "sakthi_curd_1l", "unit": "L", "packSizeL": 1},
                {"sku": "aavin_milk_500ml", "unit": "L", "packSizeL": 0.5},
                {"sku": "aavin_butter_100g", "unit": "pack"}]
    for path, data in bf.generate(products, days=30, end=TODAY - timedelta(days=1), seed=2026):
        ledger.add(path, data)
    curd = await fc.daily_demand(SHOP, "sakthi_curd_1l")
    milk = await fc.daily_demand(SHOP, "aavin_milk_500ml")
    assert 26 * 0.75 <= curd <= 26 * 1.3 * 1.25
    assert 40 * 0.5 * 0.75 <= milk <= 40 * 0.5 * 1.3 * 1.25                       # litres of 500 ml pouches
    fc.clear_cache()
    assert await fc.demo_history(SHOP) is True                                   # never presented as real data


def _backfill_module():
    spec = importlib.util.spec_from_file_location("backfill_demo_sales", BACKFILL)
    bf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bf)
    return bf


@pytest.mark.asyncio
async def test_backfilled_catalog_gives_a_positive_forecast_for_every_product(ledger):
    bf = _backfill_module()
    catalog = json.loads(bf.DEFAULT_CATALOG.read_text(encoding="utf-8"))
    for p in catalog["products"]:
        ledger.add(f"shops/{SHOP}/products/{p['sku']}", p)
    for path, data in bf.generate(catalog["products"], days=30, end=TODAY - timedelta(days=1), seed=2026):
        ledger.add(path, data)
    demand = await fc.daily_demand_all(SHOP)
    assert set(demand) == {p["sku"] for p in catalog["products"]} and len(demand) == 10
    assert all(v is not None and v > 0 for v in demand.values()), demand


@pytest.mark.asyncio
async def test_daily_demand_all_uses_the_product_doc_id_when_sku_field_is_missing(ledger):
    ledger.add(f"shops/{SHOP}/products/aavin_ghee_200ml", {"name": "Aavin ghee"})
    ledger.add_sales([sale("aavin_milk_1l", 2, TODAY - timedelta(days=i)) for i in range(1, 8)])
    assert await fc.daily_demand_all(SHOP) == {"aavin_ghee_200ml": 0.0, "aavin_milk_1l": 2.0}


@pytest.mark.asyncio
async def test_daily_demand_all_empty_in_mock(ledger, monkeypatch):
    monkeypatch.setattr(fc.config, "load", lambda: SimpleNamespace(mock=True, gcp_project=None))
    assert await fc.daily_demand_all(SHOP) == {}
    assert ledger.streams == []


def _mock_with_project(monkeypatch, project="demo-aira"):
    """An operator's shell: ADC and GCP_PROJECT, but no Gemini key, so config says mock."""
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    monkeypatch.setattr(fc.config, "load",
                        lambda: SimpleNamespace(mock=True, gcp_project=project, demo_shop_id=SHOP))


@pytest.mark.asyncio
async def test_ops_refresh_reads_in_mock_mode_when_firestore_is_configured(ledger, monkeypatch):
    _mock_with_project(monkeypatch)
    ledger.add(f"shops/{SHOP}/products/aavin_milk_1l", {"sku": "aavin_milk_1l"})
    ledger.add_sales([sale("aavin_milk_1l", 2, TODAY - timedelta(days=i)) for i in range(1, 8)])
    assert await fc.refresh_model() == {}                                   # the service in mock mode reads nothing
    assert ledger.streams == []
    assert await fc.refresh_model(ops=True) == {"aavin_milk_1l": 2.0}
    _mock_with_project(monkeypatch, project=None)
    fc.clear_cache()
    assert await fc.refresh_model(ops=True) == {}                           # nothing configured: still no read


def test_cli_prints_the_forecast_in_mock_mode(ledger, monkeypatch, capsys):
    _mock_with_project(monkeypatch)
    ledger.add_sales([sale("sakthi_curd_1l", 3, TODAY - timedelta(days=i)) for i in range(1, 8)])
    assert fc._main([SHOP]) == 0
    assert json.loads(capsys.readouterr().out) == {"sakthi_curd_1l": 3.0}


@pytest.mark.asyncio
async def test_cache_keeps_only_live_windows(ledger, monkeypatch):
    ledger.add_sales([sale("aavin_milk_1l", 2, TODAY - timedelta(days=i)) for i in range(1, 8)])
    assert await fc.daily_demand(SHOP, "aavin_milk_1l") == 2.0
    tomorrow = TODAY + timedelta(days=1)
    later = fc.time.monotonic() + fc.CACHE_TTL_S + 1
    monkeypatch.setattr(fc, "_today", lambda: tomorrow)
    monkeypatch.setattr(fc.time, "monotonic", lambda: later)
    await fc.daily_demand(SHOP, "aavin_milk_1l")
    assert list(fc._cache) == [(SHOP, tomorrow)]                                   # stale window dropped
