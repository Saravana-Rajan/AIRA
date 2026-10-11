"""Shared fixtures for the messaging tests (kanish/02). Everything is faked; no test reaches Meta or Telegram.

The real engine (kanish/01) runs on a MemoryStore seeded from the real Sunrise Dairy catalog. Supplier phones come
through the seed's own env mechanism (the catalog's ``phoneEnv`` names), never from git.
"""
from pathlib import Path

import pytest

from aira_live.business import seed
from aira_live.business.store import MemoryStore, set_store

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "shared" / "catalog" / "sunrise_dairy.json"
SHOP = "sunrise_dairy"
SUPPLIER_PHONES = {
    "SUPPLIER_AAVIN_PHONE": "+919000000001",
    "SUPPLIER_SAKTHI_PHONE": "+919000000002",
    "SUPPLIER_ARUN_PHONE": "+919000000003",
}
# Secrets and switches a developer may have in their shell. Cleared for every test so nothing reaches the network.
MESSAGING_ENV = (
    "WHATSAPP_TOKEN", "WHATSAPP_PHONE_ID", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_TEMPLATE",
    "WHATSAPP_GRAPH_VERSION", "TELEGRAM_TOKEN", "TELEGRAM_WEBHOOK_SECRET",
)


@pytest.fixture(autouse=True)
def _offline_messaging_env(monkeypatch):
    for name in MESSAGING_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AIRA_DEMO_SHOP", SHOP)


@pytest.fixture(autouse=True)
def published():
    """Fake analytics publisher: no test ever reaches aira_live.analytics. Yields [(topic, payload), ...]."""
    from aira_live.business import outbox

    sent: list[tuple[str, dict]] = []

    async def fake(topic: str, payload: dict) -> str:
        sent.append((topic, payload))
        return f"fake-{len(sent)}"

    outbox.set_publisher(fake)
    yield sent
    outbox.set_publisher(None)


@pytest.fixture
async def store():
    """MemoryStore with the shop, products and suppliers (phones from SUPPLIER_PHONES), installed as get_store()."""
    s = MemoryStore()
    set_store(s)
    await seed.seed_into(s, seed.load_catalog(CATALOG), env=SUPPLIER_PHONES)
    yield s
    set_store(None)


@pytest.fixture
def notified(monkeypatch):
    """Fake sessions.notify: records [(shop_id, text, cue), ...] instead of speaking."""
    from aira_live import sessions

    said: list[tuple[str, str, str]] = []

    async def fake_notify(shop_id: str, text: str, cue: str = "ack") -> int:
        said.append((shop_id, text, cue))
        return 1

    monkeypatch.setattr(sessions, "notify", fake_notify)
    return said
