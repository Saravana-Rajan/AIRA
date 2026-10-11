"""Seed a shop's catalog (shop doc, products, suppliers) into a Store. Supplier phones come from env, never git.

`seed_into` writes no stock. Starting stock (the catalog's optional `inventory`) goes through the ledger in
`seed_inventory` (reason ADJUST, actor "system"), so it has a ledger trail.
The catalog docs are the same as shared/scripts/seed_firestore.py writes for these paths.

CLI, from backend. It writes to Firestore database "aira" through aira_live.db, or to the emulator when
FIRESTORE_EMULATOR_HOST is set. Each supplier's phone is read from the env var its `phoneEnv` names:
    uv run python -m aira_live.business.seed --catalog ../shared/catalog/sunrise_dairy.json
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from aira_live.business import ledger, paths
from aira_live.business.store import Store

FORBIDDEN_DATABASE = "(default)"


def load_catalog(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _docs(catalog: dict, env: Mapping[str, str]) -> list[tuple[str, dict]]:
    """(path, doc) pairs for the shop, its products and its suppliers. Pure: does not mutate `catalog`."""
    shop = catalog["shop"]
    shop_id = shop["id"]
    skus = {p["sku"] for p in catalog["products"]}
    for s in catalog["suppliers"]:
        unknown = sorted(set(s.get("products") or []) - skus)
        if unknown:
            raise ValueError(f"supplier {s['id']} lists products not in the catalog: {', '.join(unknown)}")

    docs: list[tuple[str, dict]] = [(paths.shop(shop_id), {
        "name": shop["name"],
        "city": shop["city"],
        "currency": shop.get("currency", "INR"),
        "timezone": shop.get("timezone", "Asia/Kolkata"),
        "languages": list(shop.get("languages", ["ta", "en"])),  # mirrors shared/scripts/seed_firestore.py
    })]
    for p in catalog["products"]:
        docs.append((paths.product(shop_id, p["sku"]), copy.deepcopy(p)))
    for s in catalog["suppliers"]:
        doc = {k: copy.deepcopy(v) for k, v in s.items() if k != "phoneEnv"}
        doc["phone"] = env.get(s.get("phoneEnv") or "", "")
        docs.append((paths.supplier(shop_id, s["id"]), doc))
    return docs


async def seed_into(store: Store, catalog: dict, *, env: Mapping[str, str] | None = None) -> None:
    """Write the shop, products and suppliers. Idempotent: a re-run writes the same docs.
    `env` (default os.environ) supplies the supplier phone numbers."""
    for path, doc in _docs(catalog, os.environ if env is None else env):
        await store.set(path, doc)


def _lots(row: dict) -> list[tuple[str, float, str | None]]:
    """(idem-key suffix, qty, expiryDate) per starting lot of one `inventory` row.

    A row is {sku, qty[, expiryDate]} or the shared/scripts/seed_firestore.py shape {sku, onHand, batches: [...]}."""
    if row.get("batches"):
        return [(f":{b.get('batchId') or i}", float(b.get("qty") or 0), b.get("expiryDate"))
                for i, b in enumerate(row["batches"])]
    return [("", float(row.get("qty", row.get("onHand", 0)) or 0), row.get("expiryDate"))]


async def seed_inventory(store: Store, catalog: dict, *, today: date | None = None) -> None:
    """Starting stock through the ledger (ADJUST, actor "system"). Idempotent per shop + sku (+ lot), so a
    re-run adds nothing. Admin action: publishes no analytics events. Run `seed_into` first (needs products)."""
    sid = catalog["shop"]["id"]
    for row in catalog.get("inventory") or []:
        sku = row["sku"]
        for suffix, qty, exp in _lots(row):
            if qty <= 0:
                continue

            async def fn(txn, sku=sku, qty=qty, exp=exp, key=f"seed:{sid}:{sku}{suffix}"):
                prep = await ledger.prepare_movement(txn, sid, sku, qty, "ADJUST", "seed", "seed", key,
                                                     expiry_date=exp, today=today, actor="system")
                return ledger.commit_movement(txn, prep)

            await store.run(fn)


def _firestore_store() -> Store:
    from aira_live.business.store import FirestoreStore

    return FirestoreStore()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed a shop catalog into Firestore database 'aira'.")
    ap.add_argument("--catalog", required=True, type=Path)
    ns = ap.parse_args(argv)
    if os.getenv("FIRESTORE_DATABASE") == FORBIDDEN_DATABASE:
        print('error: refusing to write to the "(default)" database; AIRA uses database "aira"', file=sys.stderr)
        return 2
    catalog = load_catalog(ns.catalog)
    store = _firestore_store()

    async def run() -> None:
        await seed_into(store, catalog)
        await seed_inventory(store, catalog)

    asyncio.run(run())
    print(f"seeded {catalog['shop']['id']} from {ns.catalog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
