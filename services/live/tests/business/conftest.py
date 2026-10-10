"""Shared fixtures for the business-engine tests. Everything runs on MemoryStore; nothing touches the network."""
from pathlib import Path

import pytest

from aira_live.business import seed
from aira_live.business.store import MemoryStore, set_store

ROOT = Path(__file__).resolve().parents[4]
CATALOG = ROOT / "content" / "catalog" / "murugan_dairy.json"


def pytest_configure(config):
    config.addinivalue_line("markers", "emulator: needs FIRESTORE_EMULATOR_HOST")


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
    """MemoryStore seeded with the Murugan Dairy catalog (shop, products, suppliers) and installed as get_store()."""
    s = MemoryStore()
    set_store(s)
    await seed.seed_into(s, seed.load_catalog(CATALOG), env={})
    yield s
    set_store(None)
