"""Task 4: idempotent FIFO stock ledger, expiry scan, audit log and seeded starting stock.

Uses the shared `store` (MemoryStore seeded with the Sunrise Dairy catalog) and `published` (fake analytics
publisher) fixtures from tests/business/conftest.py.
"""
import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

from aira_live.business import audit, expiry, ledger, seed
from aira_live.business.ledger import InsufficientStock
from aira_live.business.store import MemoryStore, set_store

SHOP = "sunrise_dairy"
D0 = date(2026, 10, 12)
CATALOG = Path(__file__).resolve().parents[3] / "shared" / "catalog" / "sunrise_dairy.json"


def _docs(store, collection: str) -> list[dict]:
    prefix = f"shops/{SHOP}/{collection}/"
    return [d for p, d in store.data.items() if p.startswith(prefix) and "/" not in p[len(prefix):]]


async def _product(store, sku: str) -> dict:
    return await store.get(f"shops/{SHOP}/products/{sku}")


async def test_delivery_adds_batch_with_shelf_life(store):
    inv = await ledger.apply(SHOP, "sakthi_curd_1l", 28, "DELIVERY", "delivery", "d1", "k1", today=D0)
    product = await _product(store, "sakthi_curd_1l")
    assert inv["onHand"] == 28 and inv["sku"] == "sakthi_curd_1l"
    assert len(inv["batches"]) == 1
    assert inv["batches"][0]["qty"] == 28.0
    assert inv["batches"][0]["expiryDate"] == (D0 + timedelta(days=int(product["shelfLifeDays"]))).isoformat()
    led = _docs(store, "ledger")
    assert len(led) == 1
    assert led[0]["reason"] == "DELIVERY" and led[0]["delta"] == 28 and led[0]["idemKey"] == "k1"
    assert led[0]["refType"] == "delivery" and led[0]["refId"] == "d1" and led[0]["actor"] == "aira"
    assert led[0]["unit"] == product["unit"] and led[0]["id"].startswith("t_")
    assert await store.get(f"shops/{SHOP}/inventory/sakthi_curd_1l") == inv


async def test_delivery_accepts_iso_expiry_string(store):
    inv = await ledger.apply(SHOP, "sakthi_curd_1l", 3, "DELIVERY", "delivery", "d1", "k1",
                             expiry_date="2026-10-14", today=D0)
    assert inv["batches"][0]["expiryDate"] == "2026-10-14"


async def test_sale_consumes_fifo(store):
    await ledger.apply(SHOP, "sakthi_curd_1l", 5, "DELIVERY", "delivery", "d2", "k2",
                       expiry_date=date(2026, 10, 15), today=D0)
    await ledger.apply(SHOP, "sakthi_curd_1l", 5, "DELIVERY", "delivery", "d1", "k1",
                       expiry_date=date(2026, 10, 13), today=D0)
    inv = await ledger.apply(SHOP, "sakthi_curd_1l", -7, "SALE", "sale", "s1", "k3", today=D0)
    assert inv["onHand"] == 3
    assert [(b["qty"], b["expiryDate"]) for b in inv["batches"]] == [(3.0, "2026-10-15")]


async def test_fractional_quantities_round_cleanly(store):
    # Milk is sold in litres (selling unit "L"): 2.5 L in, 0.5 L out.
    await ledger.apply(SHOP, "aavin_milk_500ml", 2.5, "DELIVERY", "delivery", "d1", "k1", today=D0)
    inv = await ledger.apply(SHOP, "aavin_milk_500ml", -0.5, "SALE", "sale", "s1", "k2", today=D0)
    assert inv["onHand"] == 2.0 and inv["batches"][0]["qty"] == 2.0


async def test_untracked_on_hand_is_consumed_before_batches(store):
    # shared/scripts/seed_firestore.py may write onHand without batches; that stock still sells, oldest first.
    await store.set(f"shops/{SHOP}/inventory/sakthi_curd_1l",
                    {"sku": "sakthi_curd_1l", "onHand": 4, "batches": [], "updatedAt": "2026-10-11T00:00:00+00:00"})
    await ledger.apply(SHOP, "sakthi_curd_1l", 5, "DELIVERY", "delivery", "d1", "k1",
                       expiry_date=date(2026, 10, 15), today=D0)
    inv = await ledger.apply(SHOP, "sakthi_curd_1l", -6, "SALE", "sale", "s1", "k2", today=D0)
    assert inv["onHand"] == 3
    assert [(b["qty"], b["expiryDate"]) for b in inv["batches"]] == [(3.0, "2026-10-15")]


async def test_negative_stock_raises(store, published):
    await ledger.apply(SHOP, "sakthi_curd_1l", 2, "DELIVERY", "delivery", "d1", "k1", today=D0)
    published.clear()
    with pytest.raises(InsufficientStock):
        await ledger.apply(SHOP, "sakthi_curd_1l", -3, "SALE", "sale", "s1", "k2", today=D0)
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 2
    assert len(_docs(store, "ledger")) == 1
    assert [d["key"] for p, d in store.data.items() if p.startswith("idempotency/")] == ["k1"]
    assert published == []


async def test_idempotent_apply_returns_same_and_no_double(store):
    a = await ledger.apply(SHOP, "aavin_milk_500ml", 20, "DELIVERY", "delivery", "d1", "same", today=D0)
    b = await ledger.apply(SHOP, "aavin_milk_500ml", 20, "DELIVERY", "delivery", "d1", "same", today=D0)
    assert a == b and await ledger.on_hand(SHOP, "aavin_milk_500ml") == 20
    assert len(_docs(store, "ledger")) == 1
    idem = [d for p, d in store.data.items() if p.startswith("idempotency/")]
    assert len(idem) == 1 and idem[0]["key"] == "same"


async def test_invalid_reason_sign_raises(store):
    with pytest.raises(ValueError):
        await ledger.apply(SHOP, "aavin_milk_500ml", 5, "SALE", "sale", "s1", "k1", today=D0)
    with pytest.raises(ValueError):
        await ledger.apply(SHOP, "aavin_milk_500ml", -5, "DELIVERY", "delivery", "d1", "k2", today=D0)
    with pytest.raises(ValueError):
        await ledger.apply(SHOP, "aavin_milk_500ml", 5, "BOGUS", "x", "y", "k3", today=D0)
    with pytest.raises(ValueError):
        await ledger.apply(SHOP, "aavin_milk_500ml", 0, "ADJUST", "x", "y", "k4", today=D0)
    with pytest.raises(KeyError):
        await ledger.apply(SHOP, "never_stocked", 1, "DELIVERY", "delivery", "d1", "k5", today=D0)
    assert _docs(store, "ledger") == []


async def test_adjust_either_sign(store):
    await ledger.apply(SHOP, "arun_icecream_box", 3, "ADJUST", "count", "c1", "k1", today=D0, actor="owner")
    inv = await ledger.apply(SHOP, "arun_icecream_box", -1, "ADJUST", "count", "c2", "k2", today=D0, actor="owner")
    assert inv["onHand"] == 2
    assert {d["actor"] for d in _docs(store, "ledger")} == {"owner"}


async def test_on_hand_unknown_sku_zero(store):
    assert await ledger.on_hand(SHOP, "never_stocked") == 0.0


async def test_expiry_scan_statuses(store):
    for i, d in enumerate([date(2026, 10, 11), date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 20)]):
        await ledger.apply(SHOP, "sakthi_curd_1l", 2, "DELIVERY", "delivery", f"d{i}", f"k{i}",
                           expiry_date=d, today=D0)
    alerts = await expiry.scan(SHOP, D0)
    assert [a["status"] for a in alerts] == ["expired", "expires_today", "expires_tomorrow"]
    assert [a["daysLeft"] for a in alerts] == [-1, 0, 1]
    assert [a["expiryDate"] for a in alerts] == ["2026-10-11", "2026-10-12", "2026-10-13"]
    assert all(a["sku"] == "sakthi_curd_1l" and a["name"] == "Sakthi Curd 1 L" and a["qty"] == 2
               and a["unit"] == "L" and a["batchId"] for a in alerts)


async def test_expired_write_off_removes_earliest_batch(store):
    await ledger.apply(SHOP, "sakthi_curd_1l", 3, "DELIVERY", "delivery", "d1", "k1",
                       expiry_date=date(2026, 10, 20), today=D0)
    await ledger.apply(SHOP, "sakthi_curd_1l", 2, "DELIVERY", "delivery", "d0", "k0",
                       expiry_date=date(2026, 10, 11), today=D0)
    inv = await ledger.apply(SHOP, "sakthi_curd_1l", -2, "EXPIRED", "expiry", "scan-1", "k2", today=D0,
                             actor="system")
    assert [(b["qty"], b["expiryDate"]) for b in inv["batches"]] == [(3.0, "2026-10-20")]
    assert await expiry.scan(SHOP, D0) == []


async def test_apply_publishes_inventory_movement_once(store, published):
    await ledger.apply(SHOP, "sakthi_curd_1l", 28, "DELIVERY", "delivery", "d1", "pub-k", today=D0)
    await ledger.apply(SHOP, "sakthi_curd_1l", 28, "DELIVERY", "delivery", "d1", "pub-k", today=D0)  # replay
    moves = [p for topic, p in published if topic == "inventory-movements"]
    assert len(moves) == 1
    assert moves[0]["shop_id"] == SHOP and moves[0]["sku"] == "sakthi_curd_1l" and moves[0]["delta"] == 28.0
    assert moves[0]["reason"] == "DELIVERY" and moves[0]["txn_id"].startswith("t_")
    assert store.data.get(f"shops/{SHOP}/ledger/{moves[0]['txn_id']}") is not None


async def test_prepare_then_commit_two_lines_in_one_txn(store):
    await ledger.apply(SHOP, "sakthi_curd_1l", 5, "DELIVERY", "delivery", "d1", "k1", today=D0)
    await ledger.apply(SHOP, "aavin_milk_500ml", 5, "DELIVERY", "delivery", "d1", "k2", today=D0)

    async def fn(txn):
        preps = [
            await ledger.prepare_movement(txn, SHOP, "sakthi_curd_1l", -2, "SALE", "sale", "s1", "multi:a",
                                          today=D0),
            await ledger.prepare_movement(txn, SHOP, "aavin_milk_500ml", -9, "SALE", "sale", "s1", "multi:b",
                                          today=D0),
        ]
        return [ledger.commit_movement(txn, p) for p in preps]

    with pytest.raises(InsufficientStock):
        await store.run(fn)                       # second line short: nothing from the first line lands
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 5
    assert len(_docs(store, "ledger")) == 2


async def test_seed_inventory_idempotent(store, published):
    catalog = {**seed.load_catalog(CATALOG), "inventory": [
        {"sku": "sakthi_curd_1l", "qty": 4},
        {"sku": "aavin_milk_500ml", "qty": 20},
        {"sku": "arun_icecream_box", "onHand": 3, "batches": [
            {"batchId": "lot1", "qty": 1, "expiryDate": "2026-10-13"},
            {"batchId": "lot2", "qty": 2, "expiryDate": "2026-12-01"}]},
    ]}
    await seed.seed_inventory(store, catalog, today=D0)
    await seed.seed_inventory(store, catalog, today=D0)
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 4
    assert await ledger.on_hand(SHOP, "aavin_milk_500ml") == 20
    assert await ledger.on_hand(SHOP, "arun_icecream_box") == 3
    alerts = await expiry.scan(SHOP, D0)
    assert [(a["sku"], a["qty"], a["status"]) for a in alerts] == [("arun_icecream_box", 1.0, "expires_tomorrow")]
    led = _docs(store, "ledger")
    assert len(led) == 4 and {d["reason"] for d in led} == {"ADJUST"} and {d["actor"] for d in led} == {"system"}
    assert published == []                                   # seeding is admin, not analytics


async def test_seed_inventory_without_inventory_is_a_no_op(store):
    await seed.seed_inventory(store, seed.load_catalog(CATALOG), today=D0)
    assert _docs(store, "ledger") == [] and _docs(store, "inventory") == []


def test_seed_cli_seeds_catalog_then_starting_stock(tmp_path, monkeypatch, published):
    catalog = {**seed.load_catalog(CATALOG), "inventory": [{"sku": "aavin_paneer_200g", "qty": 6}]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    mem = MemoryStore()
    monkeypatch.setattr(seed, "_firestore_store", lambda: mem)
    monkeypatch.delenv("FIRESTORE_DATABASE", raising=False)
    assert seed.main(["--catalog", str(path)]) == 0
    assert mem.data[f"shops/{SHOP}/products/aavin_paneer_200g"]["name"] == "Aavin Paneer 200 g"
    assert mem.data[f"shops/{SHOP}/inventory/aavin_paneer_200g"]["onHand"] == 6
    assert published == []


async def test_audit_log_writes(store):
    aid = await audit.log(SHOP, "owner", "order_confirmed", "order", "o1", "ok")
    doc = await store.get(f"shops/{SHOP}/auditLogs/{aid}")
    assert doc["actor"] == "owner" and doc["action"] == "order_confirmed" and doc["evidenceUri"] is None
    assert doc["id"] == aid and doc["refType"] == "order" and doc["refId"] == "o1" and doc["outcome"] == "ok"
    assert doc["at"].endswith("+00:00")


async def test_audit_log_in_txn_and_bad_actor(store):
    async def fn(txn):
        return audit.log_in_txn(txn, SHOP, "system", "expiry_scan", "inventory", "sakthi_curd_1l", "3 alerts",
                                evidence_uri="gs://bucket/x.jpg")

    aid = await store.run(fn)
    doc = await store.get(f"shops/{SHOP}/auditLogs/{aid}")
    assert doc["actor"] == "system" and doc["evidenceUri"] == "gs://bucket/x.jpg"
    with pytest.raises(ValueError):
        await audit.log(SHOP, "robot", "x", "y", "z", "ok")


@pytest.mark.emulator
@pytest.mark.skipif(not os.getenv("FIRESTORE_EMULATOR_HOST"), reason="needs Firestore emulator")
async def test_emulator_concurrent_same_idem_applies_once():
    from google.cloud import firestore

    from aira_live.business.store import FirestoreStore

    fs = FirestoreStore(client=firestore.AsyncClient(project="demo-aira",
                                                     database=os.getenv("FIRESTORE_DATABASE", "aira")))
    set_store(fs)
    try:
        await seed.seed_into(fs, seed.load_catalog(CATALOG), env={})
        key = f"emu-{os.getpid()}"
        await asyncio.gather(*[
            ledger.apply(SHOP, "arun_icecream_box", 3, "DELIVERY", "delivery", "d-emu", key, today=D0)
            for _ in range(5)
        ])
        led = await fs.list(f"shops/{SHOP}/ledger")
        assert sum(1 for _, d in led if d["idemKey"] == key) == 1
    finally:
        set_store(None)
