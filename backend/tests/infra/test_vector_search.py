"""The vector index definitions, the embed role, the seeder's vectors and shared/scripts/embed_catalog.py."""
import importlib.util
import json
from pathlib import Path

import pytest

from aira_live import embeddings, models

ROOT = Path(__file__).resolve().parents[3]
INDEXES = ROOT / "shared" / "infra" / "firestore.indexes.json"
DOC = ROOT / "shared" / "docs" / "ops" / "vector-search.md"
CATALOG = ROOT / "shared" / "catalog" / "sunrise_dairy.json"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "shared" / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


seed_firestore = _load("seed_firestore")
embed_catalog = _load("embed_catalog")


# ---------- model + dimension ----------

def test_embed_role_and_dimension():
    assert models.DEFAULTS["embed"] == "gemini-embedding-2"
    assert models.model_for("embed") == "gemini-embedding-2"
    assert embeddings.DIM == 768 and embeddings.DIM <= 2048       # Firestore's vector index limit


# ---------- index JSON ----------

def _vector_indexes() -> dict[str, dict]:
    data = json.loads(INDEXES.read_text(encoding="utf-8"))
    return {ix["collectionGroup"]: ix for ix in data["indexes"] if any("vectorConfig" in f for f in ix["fields"])}


def test_vector_indexes_for_products_and_memory():
    vec = _vector_indexes()
    assert set(vec) == {"products", "memory"}
    for ix in vec.values():
        assert ix["queryScope"] == "COLLECTION"
        assert ix["fields"] == [{"fieldPath": "embedding", "vectorConfig": {"dimension": embeddings.DIM, "flat": {}}}]


def test_doc_has_the_exact_gcloud_commands():
    text = DOC.read_text(encoding="utf-8")
    for coll in ("products", "memory"):
        cmd = (f"gcloud firestore indexes composite create --collection-group={coll} --query-scope=COLLECTION "
               f"--field-config=field-path=embedding,vector-config='{{\"dimension\":\"{embeddings.DIM}\",\"flat\":\"{{}}\"}}' "
               "--database=aira")
        assert cmd in text, cmd


# ---------- seeder ----------

def test_seeder_writes_the_vector_when_an_embedder_is_given():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    seen = []

    def embed(product):
        seen.append(product["sku"])
        return embeddings.to_vector([1.0] * embeddings.DIM) if product["sku"] != "arun_icecream_box" else None

    docs = dict(seed_firestore.build_docs(catalog, {}, embed))
    assert sorted(seen) == sorted(p["sku"] for p in catalog["products"])
    curd = docs["shops/sunrise_dairy/products/sakthi_curd_1l"]
    assert len(embeddings.values_of(curd["embedding"])) == embeddings.DIM
    assert "embedding" not in docs["shops/sunrise_dairy/products/arun_icecream_box"]
    assert "embedding" not in docs["shops/sunrise_dairy/inventory/sakthi_curd_1l"]
    assert "embedding" not in catalog["products"][0]      # pure


def test_seeder_without_key_has_no_embedder(monkeypatch):
    assert seed_firestore.gemini_embedder({}) is None


# ---------- embed_catalog.py ----------

class FakeDocRef:
    def __init__(self, client, path):
        self.client, self.path = client, path

    def set(self, data, merge=False):
        self.client.writes.append((self.path, data, merge))


class FakeClient:
    def __init__(self):
        self.writes = []

    def document(self, path):
        return FakeDocRef(self, path)


def test_dry_run_prints_and_writes_nothing(capsys):
    client = FakeClient()
    rc = embed_catalog.main(["--catalog", str(CATALOG)], client=client)
    out = capsys.readouterr().out
    assert rc == 0 and client.writes == []
    assert "would embed shops/sunrise_dairy/products/sakthi_curd_500ml" in out
    assert "dry run: 10 of 10 products" in out


def test_write_needs_both_flags(capsys):
    assert embed_catalog.main(["--yes-write-cloud", "--catalog", str(CATALOG)], client=FakeClient()) == 2
    assert embed_catalog.main(["--project", "p", "--catalog", str(CATALOG), "--database", "(default)"]) == 2


def test_backfill_writes_only_products_without_a_vector(capsys):
    client = FakeClient()
    calls = []

    async def embed(doc):
        calls.append(doc["sku"])
        return embeddings.to_vector([0.5] * embeddings.DIM)

    rc = embed_catalog.main(["--yes-write-cloud", "--project", "p", "--catalog", str(CATALOG)], client=client, embed=embed)
    assert rc == 0 and len(client.writes) == 10 and len(calls) == 10
    path, data, merge = client.writes[0]
    assert path.startswith("shops/sunrise_dairy/products/") and merge is True
    assert set(data) == {"embedding"}
    assert "wrote 10 embeddings" in capsys.readouterr().out


def test_plan_skips_embedded_products_unless_forced():
    rows = [("a", {"sku": "a", "embedding": [1.0]}), ("b", {"sku": "b"})]
    assert [p for p, _ in embed_catalog.plan(rows)] == ["b"]
    assert [p for p, _ in embed_catalog.plan(rows, force=True)] == ["a", "b"]


@pytest.mark.parametrize("v", [None, "x", 3, []])
def test_values_of_rejects_non_vectors(v):
    assert embeddings.values_of(v) is None
