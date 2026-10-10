"""Backfill 30 days of plausible demo sales for the demo shop into Firestore database "aira".

Writes SALE rows to shops/{shop}/ledger/{txnId} with the spec §5 ledger fields, the same shape that
business.ledger writes for a real sale, so aira_live.analytics.forecast has a 7-day moving average on demo
day. Rows are seeded and use fixed doc ids (backfill-<day>-<sku>), so a re-run overwrites instead of adding.
Only ledger history is written: inventory, sales and payment docs are not touched. The window ends yesterday
(Asia/Kolkata), so today's live demo sales stay separate. Rows carry refType "demo_backfill".

Safe by default: without a write flag it is a dry run and writes nothing.
- --emulator writes to the Firestore emulator. The host comes from FIRESTORE_EMULATOR_HOST (environment or
  the repo's .env), else 127.0.0.1:8085 (firebase.json), and is exported before the client is created.
- The cloud needs BOTH an explicit --project and --yes-write-cloud. GCP_PROJECT from the environment or .env
  never selects a cloud write, and a cloud write is refused while FIRESTORE_EMULATOR_HOST is set.
FIRESTORE_DATABASE (default "aira") comes from --database, the environment or .env. Never writes to the
"(default)" database.

  uv run --project services/live python scripts/backfill_demo_sales.py --dry-run
  uv run --project services/live python scripts/backfill_demo_sales.py --emulator
  uv run --project services/live python scripts/backfill_demo_sales.py --project <id> --yes-write-cloud
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
EMULATOR_HOST_ENV = "FIRESTORE_EMULATOR_HOST"
DEFAULT_EMULATOR_HOST = "127.0.0.1:8085"  # firebase.json emulators.firestore.port
EMULATOR_PROJECT = "demo-aira"  # a demo-* project id only ever exists in the emulator

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
                "id": doc_id,
                "sku": sku,
                "delta": -selling_units(product, packs),
                "unit": product.get("unit", "pack"),
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


def database_of(database: str | None, env: Mapping[str, str]) -> str:
    """--database, else env FIRESTORE_DATABASE, else "aira". "(default)" is refused."""
    database = database or env.get("FIRESTORE_DATABASE") or DEFAULT_DATABASE
    if database == FORBIDDEN_DATABASE:
        raise ValueError('refusing to write to the "(default)" database; AIRA uses database "aira"')
    return database


def target(project: str | None, database: str | None, env: Mapping[str, str], *, emulator: bool = False,
           yes_write_cloud: bool = False) -> tuple[str, str, str | None]:
    """(project, database, emulator host or None) for a write. ValueError when the write is not allowed.

    Emulator: project from --project, else env GCP_PROJECT, else "demo-aira"; host from env
    FIRESTORE_EMULATOR_HOST, else 127.0.0.1:8085. Cloud: only an explicit --project with --yes-write-cloud,
    and never while FIRESTORE_EMULATOR_HOST is set (that run was meant for the emulator).
    """
    database = database_of(database, env)
    if emulator and yes_write_cloud:
        raise ValueError("choose --emulator or --yes-write-cloud, not both")
    if emulator:
        host = env.get(EMULATOR_HOST_ENV) or DEFAULT_EMULATOR_HOST
        return project or env.get("GCP_PROJECT") or EMULATOR_PROJECT, database, host
    if not yes_write_cloud:
        raise ValueError("refusing to write: pass --emulator, or --project <id> --yes-write-cloud")
    if not project:
        raise ValueError("refusing to write to the cloud without --project "
                         "(GCP_PROJECT from the environment or .env is not enough)")
    if env.get(EMULATOR_HOST_ENV):
        raise ValueError(f"refusing to write to the cloud while {EMULATOR_HOST_ENV} is set; "
                         "use --emulator, or unset it")
    return project, database, None


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
    ap = argparse.ArgumentParser(
        description="Backfill demo SALE ledger rows into Firestore database 'aira'. Dry run unless "
                    "--emulator, or --project with --yes-write-cloud, is given.")
    ap.add_argument("--project", help="GCP project id; a cloud write also needs --yes-write-cloud")
    ap.add_argument("--database", help='Firestore database id (default: env FIRESTORE_DATABASE, else "aira")')
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--shop", help="shop id (default: the catalog's shop.id)")
    ap.add_argument("--days", type=_positive, default=30)
    ap.add_argument("--end", type=date.fromisoformat, help="last day, YYYY-MM-DD (default: yesterday in IST)")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--env-file", type=Path, default=ROOT / ".env")
    ap.add_argument("--dry-run", action="store_true", help="generate and count the rows; write nothing (default)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--emulator", action="store_true",
                      help=f"write to the Firestore emulator ({EMULATOR_HOST_ENV}, else {DEFAULT_EMULATOR_HOST})")
    mode.add_argument("--yes-write-cloud", action="store_true",
                      help="write to the real Firestore of --project (required for any cloud write)")
    ns = ap.parse_args(argv)

    catalog = json.loads(ns.catalog.read_text(encoding="utf-8"))
    shop = ns.shop or catalog.get("shop", {}).get("id") or DEFAULT_SHOP
    end = ns.end or (datetime.now(IST).date() - timedelta(days=1))
    first = end - timedelta(days=ns.days - 1)
    docs = generate(catalog["products"], ns.days, end, ns.seed, shop_id=shop)
    if ns.dry_run or not (ns.emulator or ns.yes_write_cloud):
        print(f"dry run: {len(docs)} SALE ledger rows for shops/{shop}, {first} to {end}; nothing written. "
              "To write, pass --emulator, or --project <id> --yes-write-cloud.")
        return 0
    env = {**load_env_file(ns.env_file), **os.environ}
    try:
        project, database, emulator_host = target(ns.project, ns.database, env, emulator=ns.emulator,
                                                  yes_write_cloud=ns.yes_write_cloud)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    if emulator_host:
        # google-cloud-firestore reads only os.environ: a host that is only in .env must be exported, or the
        # client would connect to the real project.
        os.environ[EMULATOR_HOST_ENV] = emulator_host
    count = write(docs, make_client(project, database))
    where = f"the emulator at {emulator_host}" if emulator_host else f"project '{project}'"
    print(f"backfilled {count} SALE ledger rows into shops/{shop}/ledger, {first} to {end}, "
          f"Firestore database '{database}' in {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
