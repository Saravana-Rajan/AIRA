"""Demo catalog (content/catalog/murugan_dairy.json), its JSON schema and scripts/seed_firestore.py."""
import copy
import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = ROOT / "content" / "catalog" / "murugan_dairy.json"
SCHEMA_PATH = ROOT / "content" / "schemas" / "catalog.schema.json"

_spec = importlib.util.spec_from_file_location("seed_firestore", ROOT / "scripts" / "seed_firestore.py")
seed_firestore = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_firestore)

CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
SHOP = "shops/murugan_dairy"

UNITS = {
    "aavin_milk_500ml": "L",
    "aavin_milk_1l": "L",
    "sakthi_curd_500ml": "L",
    "sakthi_curd_1l": "L",
    "aavin_buttermilk_200ml": "pack",
    "aavin_ghee_200ml": "jar",
    "aavin_ghee_500ml": "jar",
    "aavin_paneer_200g": "pack",
    "aavin_butter_100g": "pack",
    "arun_icecream_box": "box",
}
# (packSizeL, packSizeG) per SKU
PACK_SIZES = {
    "aavin_milk_500ml": (0.5, None),
    "aavin_milk_1l": (1, None),
    "sakthi_curd_500ml": (0.5, None),
    "sakthi_curd_1l": (1, None),
    "aavin_buttermilk_200ml": (0.2, None),
    "aavin_ghee_200ml": (0.2, None),
    "aavin_ghee_500ml": (0.5, None),
    "aavin_paneer_200g": (None, 200),
    "aavin_butter_100g": (None, 100),
    "arun_icecream_box": (None, None),
}


def _validate(instance: dict) -> None:
    jsonschema.Draft202012Validator(SCHEMA).validate(instance)


def _product(catalog: dict, sku: str) -> dict:
    return next(p for p in catalog["products"] if p["sku"] == sku)


def _supplier(catalog: dict, supplier_id: str) -> dict:
    return next(s for s in catalog["suppliers"] if s["id"] == supplier_id)


# ---------- schema ----------

def test_schema_is_a_valid_2020_12_schema():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


def test_catalog_matches_schema():
    _validate(CATALOG)


def test_schema_rejects_an_unknown_unit():
    bad = copy.deepcopy(CATALOG)
    _product(bad, "aavin_paneer_200g")["unit"] = "kg"
    with pytest.raises(jsonschema.ValidationError):
        _validate(bad)


def test_schema_requires_a_litre_pack_size_for_litre_products():
    bad = copy.deepcopy(CATALOG)
    del _product(bad, "sakthi_curd_1l")["packSizeL"]
    with pytest.raises(jsonschema.ValidationError):
        _validate(bad)


def test_schema_accepts_optional_starting_inventory_and_demo_daily_base():
    ok = copy.deepcopy(CATALOG)
    _product(ok, "sakthi_curd_1l")["demoDailyBase"] = 26
    ok["inventory"] = [{"sku": "sakthi_curd_1l", "onHand": 4,
                        "batches": [{"batchId": "b1", "qty": 4, "expiryDate": "2026-10-12"}]}]
    _validate(ok)
    ok["inventory"][0]["batches"][0]["expiryDate"] = "12/10/2026"
    with pytest.raises(jsonschema.ValidationError):
        _validate(ok)


# ---------- catalog content ----------

def test_catalog_has_exactly_the_ten_demo_skus_with_their_units():
    assert {p["sku"]: p["unit"] for p in CATALOG["products"]} == UNITS
    skus = [p["sku"] for p in CATALOG["products"]]
    assert len(skus) == len(set(skus))


def test_every_product_carries_its_pack_size():
    for p in CATALOG["products"]:
        assert (p.get("packSizeL"), p.get("packSizeG")) == PACK_SIZES[p["sku"]], p["sku"]


def test_shop_and_demo_note():
    assert CATALOG["shop"]["id"] == "murugan_dairy"
    assert CATALOG["shop"]["name"] == "Murugan Dairy"
    assert "demo — verify locally" in CATALOG["note"]


def test_suppliers_are_the_demo_ids_and_cover_every_product():
    assert {s["id"] for s in CATALOG["suppliers"]} == {"aavin_vendor", "sakthi_vendor", "arun_distributor"}
    supplied = [sku for s in CATALOG["suppliers"] for sku in s["products"]]
    assert set(supplied) == set(UNITS)
    assert len(supplied) == len(set(supplied))


def test_no_phone_numbers_are_committed():
    for s in CATALOG["suppliers"]:
        assert "phone" not in s
        assert s["phoneEnv"].isupper()


@pytest.mark.parametrize("sku,ta,hi", [
    ("aavin_ghee_200ml", "நெய்", "घी"),
    ("aavin_ghee_500ml", "நெய்", "घी"),
    ("aavin_paneer_200g", "பனீர்", "पनीर"),
    ("aavin_buttermilk_200ml", "மோர்", "छाछ"),
    ("aavin_butter_100g", "வெண்ணெய்", "मक्खन"),
])
def test_aliases_cover_the_full_dairy_range(sku, ta, hi):
    aliases = _product(CATALOG, sku)["aliases"]
    assert any(ta in a for a in aliases["ta"])
    assert any(hi in a for a in aliases["hi"])
    assert aliases["en"]


def test_inventory_rows_if_present_name_known_skus():
    for row in CATALOG.get("inventory", []):
        assert row["sku"] in UNITS


# ---------- build_docs ----------

def test_build_docs_writes_shop_products_inventory_and_suppliers():
    docs = dict(seed_firestore.build_docs(CATALOG, {}))
    assert len(docs) == 1 + 10 + 10 + 3
    shop = docs[SHOP]
    assert shop["name"] == "Murugan Dairy"
    assert shop["currency"] == "INR" and shop["timezone"] == "Asia/Kolkata"
    for p in CATALOG["products"]:
        assert docs[f"{SHOP}/products/{p['sku']}"] == p
        assert f"{SHOP}/inventory/{p['sku']}" in docs
    for s in CATALOG["suppliers"]:
        assert f"{SHOP}/suppliers/{s['id']}" in docs


def test_inventory_starts_at_zero_without_starting_stock():
    catalog = copy.deepcopy(CATALOG)
    catalog.pop("inventory", None)
    docs = dict(seed_firestore.build_docs(catalog, {}))
    inv = docs[f"{SHOP}/inventory/sakthi_curd_1l"]
    assert inv["sku"] == "sakthi_curd_1l"
    assert inv["onHand"] == 0 and inv["batches"] == []
    assert inv["updatedAt"]


def test_inventory_uses_the_optional_starting_stock():
    catalog = copy.deepcopy(CATALOG)
    batches = [{"batchId": "b1", "qty": 4, "expiryDate": "2026-10-12"}]
    catalog["inventory"] = [{"sku": "sakthi_curd_1l", "onHand": 4, "batches": batches}]
    docs = dict(seed_firestore.build_docs(catalog, {}))
    assert docs[f"{SHOP}/inventory/sakthi_curd_1l"]["onHand"] == 4
    assert docs[f"{SHOP}/inventory/sakthi_curd_1l"]["batches"] == batches
    assert docs[f"{SHOP}/inventory/aavin_milk_1l"]["onHand"] == 0


def test_inventory_for_an_unknown_sku_is_rejected():
    catalog = copy.deepcopy(CATALOG)
    catalog["inventory"] = [{"sku": "mystery_item", "onHand": 1}]
    with pytest.raises(ValueError):
        seed_firestore.build_docs(catalog, {})


def test_supplier_phone_comes_from_env_and_phone_env_is_not_stored():
    sakthi_env = _supplier(CATALOG, "sakthi_vendor")["phoneEnv"]
    docs = dict(seed_firestore.build_docs(CATALOG, {sakthi_env: "+919800000001"}))
    sakthi = docs[f"{SHOP}/suppliers/sakthi_vendor"]
    assert sakthi["phone"] == "+919800000001"
    assert "phoneEnv" not in sakthi
    assert sakthi["products"] == ["sakthi_curd_500ml", "sakthi_curd_1l"]
    assert docs[f"{SHOP}/suppliers/aavin_vendor"]["phone"] == ""


def test_build_docs_does_not_mutate_the_catalog():
    catalog = copy.deepcopy(CATALOG)
    seed_firestore.build_docs(catalog, {"X": "1"})
    assert catalog == CATALOG


# ---------- seed / CLI (fake client; never touches real Firestore) ----------

class FakeBatch:
    def __init__(self, client):
        self.client = client
        self.pending: list[tuple[str, dict]] = []

    def set(self, ref, data):
        self.pending.append((ref, data))

    def commit(self):
        self.client.committed.extend(self.pending)
        self.client.commits += 1


class FakeClient:
    def __init__(self):
        self.committed: list[tuple[str, dict]] = []
        self.commits = 0

    def document(self, path):
        return path

    def batch(self):
        return FakeBatch(self)


def test_seed_writes_every_doc_in_one_batch():
    client = FakeClient()
    n = seed_firestore.seed(CATALOG_PATH, "demo-aira", "aira", env={}, client=client)
    assert n == 24 and client.commits == 1
    assert [p for p, _ in client.committed] == [p for p, _ in seed_firestore.build_docs(CATALOG, {})]


def test_seed_refuses_the_default_database():
    client = FakeClient()
    with pytest.raises(ValueError):
        seed_firestore.seed(CATALOG_PATH, "demo-aira", "(default)", env={}, client=client)
    assert client.committed == []


def test_target_reads_project_and_database_from_env():
    assert seed_firestore.target(None, None, {"GCP_PROJECT": "p1"}) == ("p1", "aira")
    assert seed_firestore.target(None, None, {"GCP_PROJECT": "p1", "FIRESTORE_DATABASE": "aira-test"}) == ("p1", "aira-test")
    assert seed_firestore.target("p2", "db2", {"GCP_PROJECT": "p1", "FIRESTORE_DATABASE": "x"}) == ("p2", "db2")


def test_target_requires_a_project_and_never_the_default_database():
    with pytest.raises(ValueError):
        seed_firestore.target(None, None, {})
    with pytest.raises(ValueError):
        seed_firestore.target("p1", "(default)", {})
    with pytest.raises(ValueError):
        seed_firestore.target(None, None, {"GCP_PROJECT": "p1", "FIRESTORE_DATABASE": "(default)"})


def test_load_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\nGCP_PROJECT=demo-aira\nexport FIRESTORE_DATABASE='aira'\n"
        'SUPPLIER_SAKTHI_PHONE="+919800000001"\nEMPTY=\nnot a pair\n',
        encoding="utf-8",
    )
    assert seed_firestore.load_env_file(env_file) == {
        "GCP_PROJECT": "demo-aira",
        "FIRESTORE_DATABASE": "aira",
        "SUPPLIER_SAKTHI_PHONE": "+919800000001",
        "EMPTY": "",
    }
    assert seed_firestore.load_env_file(tmp_path / "missing.env") == {}


def test_main_seeds_database_aira_from_env(monkeypatch, tmp_path, capsys):
    calls = []
    client = FakeClient()

    def fake_make_client(project, database):
        calls.append((project, database))
        return client

    monkeypatch.setattr(seed_firestore, "make_client", fake_make_client)
    monkeypatch.setenv("GCP_PROJECT", "demo-aira")
    monkeypatch.delenv("FIRESTORE_DATABASE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SUPPLIER_SAKTHI_PHONE=+919800000002\n", encoding="utf-8")

    assert seed_firestore.main(["--env-file", str(env_file)]) == 0
    assert calls == [("demo-aira", "aira")]
    assert "seeded 24 docs" in capsys.readouterr().out
    sakthi = dict(client.committed)[f"{SHOP}/suppliers/sakthi_vendor"]
    assert sakthi["phone"] == "+919800000002"


def test_main_fails_cleanly_without_a_project(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.setattr(seed_firestore, "make_client", lambda *a: pytest.fail("must not connect"))
    assert seed_firestore.main(["--env-file", str(tmp_path / "none.env")]) == 2
    assert "GCP_PROJECT" in capsys.readouterr().err
