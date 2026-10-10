"""Backfill 30 days of plausible demo sales for the demo shop into Firestore database "aira".

Writes SALE rows to shops/{shop}/ledger/{txnId} with the spec §5 ledger fields, the same shape that
business.ledger writes for a real sale, so aira_live.analytics.forecast has a 7-day moving average on demo
day. Rows are seeded and use fixed doc ids (backfill-<day>-<sku>), so a re-run overwrites instead of adding.
Only ledger history is written: inventory, sales and payment docs are not touched. The window ends yesterday
(Asia/Kolkata), so today's live demo sales stay separate. Rows carry refType "demo_backfill".

Reads GCP_PROJECT and FIRESTORE_DATABASE (default "aira") from flags, the environment or the repo's .env.
Never writes to the "(default)" database.

  uv run --project services/live python scripts/backfill_demo_sales.py --dry-run
  FIRESTORE_EMULATOR_HOST=127.0.0.1:8085 uv run --project services/live python scripts/backfill_demo_sales.py --project demo-aira
  uv run --project services/live python scripts/backfill_demo_sales.py
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "content" / "catalog" / "murugan_dairy.json"
DEFAULT_DATABASE = "aira"
FORBIDDEN_DATABASE = "(default)"
DEFAULT_SHOP = "murugan_dairy"
IST = ZoneInfo("Asia/Kolkata")
REF_TYPE = "demo_backfill"
BATCH_LIMIT = 500  # Firestore batched-write limit

# Pouches / packs / jars / boxes sold on an ordinary weekday. A catalog product's `demoDailyBase` wins.
DAILY_BASE: dict[str, float] = {
    "aavin_milk_500ml": 40,        # milk and curd: high
    "aavin_milk_1l": 18,
    "sakthi_curd_500ml": 20,
    "sakthi_curd_1l": 26,
    "aavin_buttermilk_200ml": 12,  # buttermilk: medium
    "aavin_paneer_200g": 3,        # paneer, butter and ghee: low
    "aavin_butter_100g": 3,
    "aavin_ghee_200ml": 2,
    "aavin_ghee_500ml": 1,
    "arun_icecream_box": 2,        # ice cream: weekend-heavy, see WEEKDAY_FACTOR_BY_SKU
}
FALLBACK_BASE = 5
# Day-of-week multipliers (Mon=0 ... Sun=6).
WEEKDAY_FACTOR: dict[int, float] = {5: 1.15, 6: 1.3}
WEEKDAY_FACTOR_BY_SKU: dict[str, dict[int, float]] = {"arun_icecream_box": {4: 1.4, 5: 2.5, 6: 3.0}}
OPEN_MINUTE, CLOSE_MINUTE = 6 * 60, 21 * 60  # shop hours, IST


def selling_units(product: Mapping, packs: int) -> float:
    """Packs → the ledger's selling unit: litres for unit "L" products (packSizeL each), else packs."""
    if product.get("unit") == "L" and product.get("packSizeL"):
        return round(packs * float(product["packSizeL"]), 3)
    return float(packs)


def generate(products: list[dict], days: int, end: date, seed: int,
             shop_id: str = DEFAULT_SHOP) -> list[tuple[str, dict]]:
    """(path, ledger doc) pairs: one SALE row per product per IST day, for the `days` days ending on `end`."""
    rng = random.Random(seed)
    docs: list[tuple[str, dict]] = []
    for offset in range(days - 1, -1, -1):
        day = end - timedelta(days=offset)
        for product in products:
            sku = product["sku"]
            base = product.get("demoDailyBase")
            base = float(base if base is not None else DAILY_BASE.get(sku, FALLBACK_BASE))
            factor = WEEKDAY_FACTOR_BY_SKU.get(sku, WEEKDAY_FACTOR).get(day.weekday(), 1.0)
            packs = max(round(base * factor * rng.uniform(0.75, 1.25)), 0)
            minute = rng.randrange(OPEN_MINUTE, CLOSE_MINUTE)
            if packs == 0:
                continue
            at = datetime.combine(day, time(minute // 60, minute % 60), IST).astimezone(timezone.utc)
            doc_id = f"backfill-{day.isoformat()}-{sku}"
            docs.append((f"shops/{shop_id}/ledger/{doc_id}", {
                "sku": sku,
                "delta": -selling_units(product, packs),
                "reason": "SALE",
                "refType": REF_TYPE,
                "refId": f"backfill-{day.isoformat()}",
                "idemKey": doc_id,
                "at": at.isoformat(),
                "actor": "system",
            }))
    return docs


def load_env_file(path: Path) -> dict[str, str]:
    """Minimal .env reader: KEY=VALUE lines, optional `export `, optional matching quotes."""
    path = Path(path)
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def target(project: str | None, database: str | None, env: Mapping[str, str]) -> tuple[str, str]:
    """(project, database) from flags, else env GCP_PROJECT / FIRESTORE_DATABASE (default "aira")."""
    project = project or env.get("GCP_PROJECT", "")
    database = database or env.get("FIRESTORE_DATABASE") or DEFAULT_DATABASE
    if not project:
        raise ValueError("GCP_PROJECT is not set (env or .env) and --project was not given")
    if database == FORBIDDEN_DATABASE:
        raise ValueError('refusing to write to the "(default)" database; AIRA uses database "aira"')
    return project, database


def make_client(project: str, database: str) -> Any:
    from google.cloud import firestore

    return firestore.Client(project=project, database=database)


def write(docs: list[tuple[str, dict]], client: Any) -> int:
    """Set every doc, committing at most BATCH_LIMIT writes per batch. Returns the number written."""
    for start in range(0, len(docs), BATCH_LIMIT):
        batch = client.batch()
        for path, data in docs[start:start + BATCH_LIMIT]:
            batch.set(client.document(path), data)
        batch.commit()
    return len(docs)


def _positive(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill demo SALE ledger rows into Firestore database 'aira'.")
    ap.add_argument("--project", help="GCP project id (default: env GCP_PROJECT)")
    ap.add_argument("--database", help='Firestore database id (default: env FIRESTORE_DATABASE, else "aira")')
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--shop", help="shop id (default: the catalog's shop.id)")
    ap.add_argument("--days", type=_positive, default=30)
    ap.add_argument("--end", type=date.fromisoformat, help="last day, YYYY-MM-DD (default: yesterday in IST)")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--env-file", type=Path, default=ROOT / ".env")
    ap.add_argument("--dry-run", action="store_true", help="generate and count the rows; write nothing")
    ns = ap.parse_args(argv)

    catalog = json.loads(ns.catalog.read_text(encoding="utf-8"))
    shop = ns.shop or catalog.get("shop", {}).get("id") or DEFAULT_SHOP
    end = ns.end or (datetime.now(IST).date() - timedelta(days=1))
    first = end - timedelta(days=ns.days - 1)
    docs = generate(catalog["products"], ns.days, end, ns.seed, shop_id=shop)
    if ns.dry_run:
        print(f"dry run: {len(docs)} SALE ledger rows for shops/{shop}, {first} to {end}; nothing written")
        return 0
    env = {**load_env_file(ns.env_file), **os.environ}
    try:
        project, database = target(ns.project, ns.database, env)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    count = write(docs, make_client(project, database))
    print(f"backfilled {count} SALE ledger rows into shops/{shop}/ledger, {first} to {end}, "
          f"Firestore database '{database}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
