"""Backfill the `embedding` vector of every product in Firestore database `aira` (shops/*/products/*).

Dry run by default: lists each product and the text it would be embedded from, and writes nothing.
Writes only with BOTH --yes-write-cloud and --project (never to the "(default)" database):

  uv run --project backend python shared/scripts/embed_catalog.py
  uv run --project backend python shared/scripts/embed_catalog.py --yes-write-cloud --project <gcp-project>

--catalog <json> embeds the catalog file instead of reading the products from Firestore (dry run shows the
texts without any cloud call). --force re-embeds products that already have a vector.
Needs GEMINI_API_KEY (env or .env) to write; see shared/docs/ops/vector-search.md for the index commands.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from seed_firestore import DEFAULT_DATABASE, FORBIDDEN_DATABASE, default_env_file, load_env_file  # noqa: E402

EMBEDDING_FIELD = "embedding"
Embed = Callable[[dict], Awaitable[Any]]   # product doc -> Vector | None


def product_text(doc: dict) -> str:
    from aira_live import embeddings

    return embeddings.product_text(doc)


def catalog_products(path: Path) -> list[tuple[str, dict]]:
    """(doc path, product) for every product of a catalog file."""
    catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    shop = catalog["shop"]["id"]
    return [(f"shops/{shop}/products/{p['sku']}", p) for p in catalog["products"]]


def firestore_products(client: Any) -> list[tuple[str, dict]]:
    """(doc path, product) for every shops/*/products/* doc (sync client)."""
    out = []
    for snap in client.collection_group("products").stream():
        ref = snap.reference
        if ref.parent.parent is None or ref.parent.parent.parent.id != "shops":
            continue
        out.append((ref.path, snap.to_dict() or {}))
    return out


def plan(products: list[tuple[str, dict]], force: bool = False) -> list[tuple[str, dict]]:
    """The products that need a vector: those without one, or all with `force`."""
    return [(p, d) for p, d in products if force or d.get(EMBEDDING_FIELD) is None]


async def backfill(products: list[tuple[str, dict]], embed: Embed, client: Any) -> int:
    """Embed each product and write its `embedding` field (merge). Returns how many were written."""
    n = 0
    for path, doc in products:
        vector = await embed(doc)
        if vector is None:
            print(f"skip {path}: no embedding", file=sys.stderr)
            continue
        client.document(path).set({EMBEDDING_FIELD: vector}, merge=True)
        n += 1
    return n


def _gemini_embed() -> Embed:
    from aira_live import embeddings

    return embeddings.product_vector


def main(argv: list[str] | None = None, *, client: Any = None, embed: Embed | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill product embeddings in Firestore database 'aira'.")
    ap.add_argument("--yes-write-cloud", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--project", help="GCP project id (required to write)")
    ap.add_argument("--database", help='Firestore database id (default: env FIRESTORE_DATABASE, else "aira")')
    ap.add_argument("--catalog", type=Path, help="embed this catalog file's products instead of reading Firestore")
    ap.add_argument("--force", action="store_true", help="re-embed products that already have a vector")
    ap.add_argument("--env-file", type=Path)
    ns = ap.parse_args(argv)
    env = {**load_env_file(ns.env_file or default_env_file()), **os.environ}
    database = ns.database or env.get("FIRESTORE_DATABASE") or DEFAULT_DATABASE
    if database == FORBIDDEN_DATABASE:
        print('error: refusing to write to the "(default)" database; AIRA uses database "aira"', file=sys.stderr)
        return 2
    write = ns.yes_write_cloud and bool(ns.project)
    if ns.yes_write_cloud and not ns.project:
        print("error: --yes-write-cloud needs --project", file=sys.stderr)
        return 2

    if ns.catalog:
        products = catalog_products(ns.catalog)
    elif write or client is not None:
        if client is None:
            from google.cloud import firestore

            client = firestore.Client(project=ns.project, database=database)
        products = firestore_products(client)
    else:
        print("dry run with no --catalog and no --project: nothing to read; showing the demo catalog", file=sys.stderr)
        products = catalog_products(ROOT / "shared" / "catalog" / "sunrise_dairy.json")

    todo = plan(products, ns.force)
    for path, doc in todo:
        print(f"{'embed' if write else 'would embed'} {path}: {product_text(doc)[:100]}")
    if not write:
        print(f"dry run: {len(todo)} of {len(products)} products would get an embedding (no writes, no model calls)")
        return 0
    if embed is None:
        if not env.get("GEMINI_API_KEY"):
            print("error: GEMINI_API_KEY is not set (env or .env)", file=sys.stderr)
            return 2
        os.environ.setdefault("GEMINI_API_KEY", env["GEMINI_API_KEY"])
        embed = _gemini_embed()
    if client is None:
        from google.cloud import firestore

        client = firestore.Client(project=ns.project, database=database)
    n = asyncio.run(backfill(todo, embed, client))
    print(f"wrote {n} embeddings to Firestore database '{database}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
