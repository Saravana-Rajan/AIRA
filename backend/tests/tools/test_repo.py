"""Order/delivery repository and the message/contact indexes (kanish/02 Task 1: aira_live.tools._repo)."""
import pytest

from aira_live.business.store import MemoryStore, set_store
from aira_live.tools import _repo

SHOP = "sunrise_dairy"


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


@pytest.mark.parametrize("bad", ["a/b", "../o1", "", "x" * 65, "o 1", "o1\n"])
async def test_model_supplied_ids_never_reach_a_firestore_path(store, bad):
    """A model-supplied order/delivery id goes into `shops/{shop}/orders/{id}`: an id with "/" would read or write
    a different document (or make the client raise). Unknown-looking ids are simply "not found"."""
    from aira_live.business import paths

    with pytest.raises(ValueError):
        paths.doc_id(bad)
    for fn in (paths.order, paths.delivery, paths.sale, paths.payment, paths.supplier, paths.product):
        with pytest.raises(ValueError):
            fn(SHOP, bad)
    assert await _repo.get_order(SHOP, bad) is None
    assert await _repo.get_delivery(SHOP, bad) is None
    assert not [k for k in store.data if bad and bad in k]


def test_doc_id_accepts_the_ids_the_engine_makes():
    from aira_live.business import paths
    from aira_live.business._ids import doc_id_for

    for ok in ("o1", doc_id_for("p", "k:cash"), "sakthi_curd_1l", "aavin-vendor", "backfill-2026-10-10-x"):
        assert paths.doc_id(ok) == ok


async def test_index_keys_with_a_slash_stay_one_document(store):
    await _repo.index_message("telegram", "a/b:7", SHOP, "o9")
    assert f"shops/{SHOP}/msgIndex/telegram:a_b:7" in store.data
    assert await _repo.lookup_message("telegram", "a/b:7") == {"shopId": SHOP, "orderId": "o9"}
