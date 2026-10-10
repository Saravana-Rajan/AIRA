"""Order/delivery repository and the message/contact indexes (kanish/02 Task 1: aira_live.tools._repo)."""
import pytest

from aira_live.business.store import MemoryStore, set_store
from aira_live.tools import _repo

SHOP = "murugan_dairy"


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("AIRA_DEMO_SHOP", SHOP)
    s = MemoryStore()
    set_store(s)
    yield s
    set_store(None)


async def test_orders_get_save_latest(store):
    await _repo.save_order(SHOP, {"id": "o1", "state": "ORDER_PLACED", "supplierId": "sakthi_vendor",
                                  "updatedAt": "2026-10-12T09:00:00+00:00"})
    await _repo.save_order(SHOP, {"id": "o2", "state": "ORDER_PLACED", "supplierId": "aavin_vendor",
                                  "updatedAt": "2026-10-12T10:00:00+00:00"})
    await _repo.save_order(SHOP, {"id": "o3", "state": "CLOSED", "supplierId": "sakthi_vendor",
                                  "updatedAt": "2026-10-12T11:00:00+00:00"})
    assert store.data[f"shops/{SHOP}/orders/o1"]["state"] == "ORDER_PLACED"
    assert (await _repo.get_order(SHOP, "o1"))["id"] == "o1"
    assert await _repo.get_order(SHOP, "nope") is None
    assert (await _repo.latest_order(SHOP, ["ORDER_PLACED"]))["id"] == "o2"
    assert (await _repo.latest_order(SHOP, ["ORDER_PLACED"], supplier_id="sakthi_vendor"))["id"] == "o1"
    assert (await _repo.latest_order(SHOP, ["ORDER_PLACED", "CLOSED"]))["id"] == "o3"
    assert await _repo.latest_order(SHOP, ["DRAFT"]) is None


async def test_deliveries_get_save_for_order(store):
    await _repo.save_delivery(SHOP, {"id": "d1", "orderId": "o1", "updatedAt": "2026-10-12T09:30:00+00:00"})
    await _repo.save_delivery(SHOP, {"id": "d2", "orderId": "o1", "updatedAt": "2026-10-12T09:45:00+00:00"})
    assert store.data[f"shops/{SHOP}/deliveries/d1"]["orderId"] == "o1"
    assert (await _repo.get_delivery(SHOP, "d1"))["orderId"] == "o1"
    assert await _repo.get_delivery(SHOP, "nope") is None
    assert (await _repo.delivery_for_order(SHOP, "o1"))["id"] == "d2"
    assert await _repo.delivery_for_order(SHOP, "o2") is None


async def test_message_and_contact_indexes_live_under_the_shop(store):
    await _repo.index_message("whatsapp", "wamid.1", SHOP, "o1")
    await _repo.index_contact("whatsapp", "919000000002", SHOP, "sakthi_vendor")
    assert store.data[f"shops/{SHOP}/msgIndex/whatsapp:wamid.1"] == {"shopId": SHOP, "orderId": "o1"}
    assert store.data[f"shops/{SHOP}/contactIndex/whatsapp:919000000002"] == \
        {"shopId": SHOP, "supplierId": "sakthi_vendor"}
    # a webhook does not know the shop: lookups default to the demo shop (env AIRA_DEMO_SHOP)
    assert await _repo.lookup_message("whatsapp", "wamid.1") == {"shopId": SHOP, "orderId": "o1"}
    assert await _repo.lookup_message("whatsapp", "wamid.1", SHOP) == {"shopId": SHOP, "orderId": "o1"}
    assert await _repo.lookup_contact("whatsapp", "919000000002") == {"shopId": SHOP, "supplierId": "sakthi_vendor"}
    assert await _repo.lookup_message("whatsapp", "wamid.unknown") is None
    assert await _repo.lookup_contact("telegram", "919000000002") is None
    assert await _repo.lookup_message("whatsapp", "wamid.1", "other_shop") is None


async def test_index_keys_with_a_slash_stay_one_document(store):
    await _repo.index_message("telegram", "a/b:7", SHOP, "o9")
    assert f"shops/{SHOP}/msgIndex/telegram:a_b:7" in store.data
    assert await _repo.lookup_message("telegram", "a/b:7") == {"shopId": SHOP, "orderId": "o9"}
