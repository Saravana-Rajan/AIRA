"""Seed a shop's catalog (shop doc, products, suppliers) into a Store. Supplier phones come from env, never git.

Stock is not written here: starting stock goes through the ledger, so it has a ledger trail.
The docs are the same as scripts/seed_firestore.py writes for these paths.

CLI, from services/live. It writes to Firestore database "aira" through aira_live.db, or to the emulator when
FIRESTORE_EMULATOR_HOST is set. Each supplier's phone is read from the env var its `phoneEnv` names:
    uv run python -m aira_live.business.seed --catalog ../../content/catalog/murugan_dairy.json
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from aira_live.business import paths
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
    asyncio.run(seed_into(_firestore_store(), catalog))
    print(f"seeded {catalog['shop']['id']} from {ns.catalog}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
