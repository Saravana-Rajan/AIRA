"""GET /api/forecast (aira_live.api): the dashboard's Forecast card, served read-only from the demo shop with CORS
limited to the hosting origin. No Firestore: the engine Store is a MemoryStore and the forecast is faked."""
import httpx
import pytest

from aira_live import main as main_mod
from aira_live.analytics import forecast
from aira_live.business import paths
from aira_live.business.store import MemoryStore, set_store

DEMO = "murugan_dairy"
CURD, MILK = "sakthi_curd_1l", "aavin_milk_500ml"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("AIRA_DEMO_SHOP", raising=False)
    monkeypatch.delenv("AIRA_CORS_ORIGINS", raising=False)


@pytest.fixture
async def store():
    s = MemoryStore()
    set_store(s)
    await s.set(paths.product(DEMO, CURD), {"sku": CURD, "name": "Sakthi Curd 1 L", "unit": "L"})
    await s.set(paths.product(DEMO, MILK), {"sku": MILK, "name": "Aavin Milk 500 ml", "unit": "L"})
    await s.set(paths.inventory(DEMO, CURD), {"sku": CURD, "onHand": 4.0, "batches": []})
    yield s
    set_store(None)


@pytest.fixture
def demand(monkeypatch):
    values = {CURD: 10.0, MILK: None}

    async def fake(shop_id, *, ops=False):
        assert shop_id == DEMO
        return dict(values)

    monkeypatch.setattr(forecast, "daily_demand_all", fake)
    return values


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=main_mod.app), base_url="http://test")


def test_api_router_is_mounted():
    assert "aira_live.api" in main_mod.OPTIONAL_ROUTERS and "aira_live.api" in main_mod.MOUNTED_ROUTERS


async def test_forecast_lists_only_skus_with_a_forecast(store, demand):
    async with client() as c:
        r = await c.get("/api/forecast", params={"shop": DEMO})
    assert r.status_code == 200
    assert r.json() == {"items": [
        {"sku": CURD, "name": "Sakthi Curd 1 L", "dailyDemand": 10.0, "onHand": 4.0, "suggestedOrder": 8},
    ]}  # ceil(10 * 1.15 - 4) = 8; milk has no history, so it is skipped rather than guessed


async def test_forecast_refuses_other_shops_and_bad_ids(store, demand):
    async with client() as c:
        other = await c.get("/api/forecast", params={"shop": "other_shop"})
        path = await c.get("/api/forecast", params={"shop": "../x"})
        none = await c.get("/api/forecast")
    assert other.status_code == 403 and path.status_code == 422 and none.status_code == 422


async def test_forecast_is_empty_without_history(store, monkeypatch):
    async def nothing(shop_id, *, ops=False):
        return {}

    monkeypatch.setattr(forecast, "daily_demand_all", nothing)
    async with client() as c:
        r = await c.get("/api/forecast", params={"shop": DEMO})
    assert r.status_code == 200 and r.json() == {"items": []}


async def test_forecast_is_503_when_the_store_fails(demand, monkeypatch):
    class Broken:
        async def list(self, path):
            raise RuntimeError("firestore down")

    set_store(Broken())
    try:
        async with client() as c:
            r = await c.get("/api/forecast", params={"shop": DEMO})
    finally:
        set_store(None)
    assert r.status_code == 503


async def test_cors_allows_the_hosting_origin_only(store, demand):
    async with client() as c:
        ok = await c.get("/api/forecast", params={"shop": DEMO}, headers={"Origin": "https://aira-shop.web.app"})
        alias = await c.get("/api/forecast", params={"shop": DEMO},
                            headers={"Origin": "https://aira-shop.firebaseapp.com"})
        other = await c.get("/api/forecast", params={"shop": DEMO}, headers={"Origin": "https://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "https://aira-shop.web.app"
    assert alias.headers.get("access-control-allow-origin") == "https://aira-shop.firebaseapp.com"
    assert "access-control-allow-origin" not in other.headers


def test_cors_origins_adds_env_origins(monkeypatch):
    assert main_mod.cors_origins() == list(main_mod.HOSTING_ORIGINS)
    monkeypatch.setenv("AIRA_CORS_ORIGINS", "http://localhost:5173/, http://127.0.0.1:5173")
    assert main_mod.cors_origins() == [*main_mod.HOSTING_ORIGINS, "http://localhost:5173", "http://127.0.0.1:5173"]
