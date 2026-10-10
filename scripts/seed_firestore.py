"""Seed the demo shop from content/catalog/murugan_dairy.json into Firestore database `aira`.

Reads GCP_PROJECT and FIRESTORE_DATABASE (default "aira") from the environment or the repo's .env.
Never writes to the "(default)" database. Supplier phone numbers come from the env var named by each
supplier's `phoneEnv` and are never committed.

  uv run --project services/live python scripts/seed_firestore.py
  FIRESTORE_EMULATOR_HOST=127.0.0.1:8085 uv run --project services/live python scripts/seed_firestore.py --project demo-aira

Writes shops/{shop}, shops/{shop}/products/{sku}, shops/{shop}/inventory/{sku} (onHand 0 unless the
catalog's optional `inventory` gives starting stock) and shops/{shop}/suppliers/{id}.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "content" / "catalog" / "murugan_dairy.json"
DEFAULT_DATABASE = "aira"
FORBIDDEN_DATABASE = "(default)"


def build_docs(catalog: dict, env: Mapping[str, str]) -> list[tuple[str, dict]]:
    """Firestore (path, data) pairs for the catalog. Pure: does not mutate `catalog`."""
    shop = catalog["shop"]
    base = f"shops/{shop['id']}"
    now = datetime.now(timezone.utc).isoformat()
    skus = {p["sku"] for p in catalog["products"]}
    stock = {row["sku"]: row for row in catalog.get("inventory", [])}
    unknown = sorted(set(stock) - skus)
    if unknown:
        raise ValueError(f"inventory rows for unknown SKUs: {', '.join(unknown)}")

    docs: list[tuple[str, dict]] = [(base, {
        "name": shop["name"],
        "city": shop["city"],
        "currency": shop.get("currency", "INR"),
        "timezone": shop.get("timezone", "Asia/Kolkata"),
    })]
    for p in catalog["products"]:
        sku = p["sku"]
        start = stock.get(sku, {})
        docs.append((f"{base}/products/{sku}", copy.deepcopy(p)))
        docs.append((f"{base}/inventory/{sku}", {
            "sku": sku,
            "onHand": start.get("onHand", 0),
            "batches": copy.deepcopy(start.get("batches", [])),
            "updatedAt": now,
        }))
    for s in catalog["suppliers"]:
        doc = {k: copy.deepcopy(v) for k, v in s.items() if k != "phoneEnv"}
        doc["phone"] = env.get(s["phoneEnv"], "")
        docs.append((f"{base}/suppliers/{s['id']}", doc))
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


def seed(catalog_path: Path, project: str, database: str = DEFAULT_DATABASE, *,
         env: Mapping[str, str] | None = None, client: Any = None) -> int:
    """Write every catalog doc in one batch. Returns the number of docs written."""
    if database == FORBIDDEN_DATABASE:
        raise ValueError('refusing to write to the "(default)" database; AIRA uses database "aira"')
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    docs = build_docs(catalog, os.environ if env is None else env)
    client = client if client is not None else make_client(project, database)
    batch = client.batch()
    for path, data in docs:
        batch.set(client.document(path), data)
    batch.commit()
    return len(docs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed the AIRA demo shop into Firestore database 'aira'.")
    ap.add_argument("--project", help="GCP project id (default: env GCP_PROJECT)")
    ap.add_argument("--database", help='Firestore database id (default: env FIRESTORE_DATABASE, else "aira")')
    ap.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    ap.add_argument("--env-file", type=Path, default=ROOT / ".env")
    ns = ap.parse_args(argv)
    env = {**load_env_file(ns.env_file), **os.environ}
    try:
        project, database = target(ns.project, ns.database, env)
        count = seed(ns.catalog, project, database, env=env)
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    print(f"seeded {count} docs into Firestore database '{database}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
