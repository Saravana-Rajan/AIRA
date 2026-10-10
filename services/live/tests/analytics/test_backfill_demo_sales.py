"""scripts/backfill_demo_sales.py: 30 days of plausible demo SALE ledger rows for Firestore database "aira"."""
import importlib.util
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "backfill_demo_sales.py"
spec = importlib.util.spec_from_file_location("backfill_demo_sales", SCRIPT)
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)

IST = ZoneInfo("Asia/Kolkata")
END = date(2026, 10, 10)
ALL_SKUS = ["aavin_milk_500ml", "aavin_milk_1l", "sakthi_curd_500ml", "sakthi_curd_1l", "aavin_buttermilk_200ml",
            "aavin_ghee_200ml", "aavin_ghee_500ml", "aavin_paneer_200g", "aavin_butter_100g", "arun_icecream_box"]
PACK_L = {"aavin_milk_500ml": 0.5, "aavin_milk_1l": 1.0, "sakthi_curd_500ml": 0.5, "sakthi_curd_1l": 1.0}
# Same units as content/catalog/murugan_dairy.json: milk and curd sell in litres, the rest per pack/jar/box.
PRODUCTS = [{"sku": s, "unit": "L", "packSizeL": PACK_L[s]} if s in PACK_L else {"sku": s, "unit": "pack"}
            for s in ALL_SKUS]
LEDGER_KEYS = {"sku", "delta", "reason", "refType", "refId", "idemKey", "at", "actor"}


def ist_day(row):
    return datetime.fromisoformat(row["at"]).astimezone(IST).date()


def sold(docs, sku=None):
    return [(ist_day(d), -d["delta"]) for _, d in docs if sku is None or d["sku"] == sku]


def test_daily_base_covers_all_ten_demo_skus():
    assert set(bf.DAILY_BASE) == set(ALL_SKUS)
    high = min(bf.DAILY_BASE[s] for s in ALL_SKUS[:4])
    assert high > bf.DAILY_BASE["aavin_buttermilk_200ml"] > max(
        bf.DAILY_BASE[s] for s in ("aavin_ghee_200ml", "aavin_ghee_500ml", "aavin_paneer_200g", "aavin_butter_100g"))


def test_generate_is_deterministic_and_covers_every_sku_every_day():
    a = bf.generate(PRODUCTS, days=30, end=END, seed=7)
    b = bf.generate(PRODUCTS, days=30, end=END, seed=7)
    assert a == b and a != bf.generate(PRODUCTS, days=30, end=END, seed=8)
    days = {END - timedelta(days=i) for i in range(30)}
    per_sku = defaultdict(set)
    for _, data in a:
        per_sku[data["sku"]].add(ist_day(data))
    assert set(per_sku) == set(ALL_SKUS)
    assert all(seen == days for seen in per_sku.values())
    assert len(a) == 300


def test_rows_are_sale_ledger_docs_with_fixed_ids():
    docs = bf.generate(PRODUCTS, days=30, end=END, seed=1)
    paths = [p for p, _ in docs]
    assert len(set(paths)) == len(paths)
    for path, data in docs:
        assert path == f"shops/murugan_dairy/ledger/backfill-{ist_day(data).isoformat()}-{data['sku']}"
        assert set(data) == LEDGER_KEYS
        assert data["reason"] == "SALE" and data["delta"] < 0 and data["actor"] == "system"
        assert data["refType"] == bf.REF_TYPE == "demo_backfill" and data["idemKey"] == path.rsplit("/", 1)[1]
        assert datetime.fromisoformat(data["at"]).utcoffset() == timedelta(0)        # ISO-8601 UTC, like now_iso()
        assert 6 <= datetime.fromisoformat(data["at"]).astimezone(IST).hour < 21      # shop hours


def test_shop_id_is_used_in_paths():
    docs = bf.generate(PRODUCTS[:1], days=2, end=END, seed=1, shop_id="other_shop")
    assert all(p.startswith("shops/other_shop/ledger/") for p, _ in docs)


def test_litre_products_sell_whole_pouches():
    for sku, size in PACK_L.items():
        for _, qty in sold(bf.generate(PRODUCTS, days=30, end=END, seed=4), sku):
            assert abs(qty / size - round(qty / size)) < 1e-9
    for _, qty in sold(bf.generate(PRODUCTS, days=30, end=END, seed=4), "aavin_paneer_200g"):
        assert qty == int(qty)


def test_weekends_sell_more_and_ice_cream_is_weekend_heavy():
    def ratio(sku):
        rows = sold(bf.generate([{"sku": sku, "unit": "pack"}], days=56, end=END, seed=3))
        wk = [q for d, q in rows if d.weekday() >= 5]
        wd = [q for d, q in rows if d.weekday() < 5]
        return (sum(wk) / len(wk)) / (sum(wd) / len(wd))
    assert ratio("sakthi_curd_1l") > 1.05
    assert ratio("arun_icecream_box") > 2.0


def test_catalog_demo_daily_base_overrides_the_default():
    rows = sold(bf.generate([{"sku": "sakthi_curd_1l", "unit": "pack", "demoDailyBase": 100}],
                            days=14, end=END, seed=2))
    assert sum(q for _, q in rows) / len(rows) > 70


def test_target_needs_a_project_and_refuses_the_default_database():
    assert bf.target(None, None, {"GCP_PROJECT": "demo-aira"}) == ("demo-aira", "aira")
    assert bf.target("p", None, {"FIRESTORE_DATABASE": "aira-test"}) == ("p", "aira-test")
    with pytest.raises(ValueError):
        bf.target(None, None, {})
    with pytest.raises(ValueError):
        bf.target("p", "(default)", {})
    with pytest.raises(ValueError):
        bf.target("p", None, {"FIRESTORE_DATABASE": "(default)"})


class FakeClient:
    def __init__(self):
        self.commits: list[list[tuple[str, dict]]] = []

    def document(self, path):
        return path

    def batch(self):
        client, pending = self, []

        class _Batch:
            def set(self, ref, data):
                pending.append((ref, data))

            def commit(self):
                client.commits.append(list(pending))
        return _Batch()


def test_write_commits_in_batches_of_at_most_500():
    docs = [(f"shops/s/ledger/t{i}", {"i": i}) for i in range(1201)]
    client = FakeClient()
    assert bf.write(docs, client) == 1201
    assert [len(c) for c in client.commits] == [500, 500, 201]
    assert [d for c in client.commits for d in c] == docs


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"shop": {"id": "murugan_dairy", "name": "Murugan Dairy", "city": "Chennai"},
                                "products": PRODUCTS, "suppliers": []}), encoding="utf-8")
    return path


def test_main_dry_run_writes_nothing(catalog, monkeypatch, capsys):
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("dry run must not create a Firestore client"))
    assert bf.main(["--dry-run", "--catalog", str(catalog), "--end", "2026-10-10"]) == 0
    out = capsys.readouterr().out
    assert "300" in out and "2026-09-11" in out and "2026-10-10" in out


def test_main_writes_to_database_aira(catalog, monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("FIRESTORE_DATABASE", raising=False)
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    seen, client = [], FakeClient()
    monkeypatch.setattr(bf, "make_client", lambda project, database: seen.append((project, database)) or client)
    args = ["--project", "demo-aira", "--catalog", str(catalog), "--end", "2026-10-10",
            "--env-file", str(tmp_path / "missing.env")]
    assert bf.main(args) == 0
    assert seen == [("demo-aira", "aira")]
    assert sum(len(c) for c in client.commits) == 300
    assert "300" in capsys.readouterr().out


def test_repo_catalog_backfills_all_ten_skus_for_thirty_days(monkeypatch, capsys):
    catalog = json.loads(bf.DEFAULT_CATALOG.read_text(encoding="utf-8"))
    assert {p["sku"] for p in catalog["products"]} == set(ALL_SKUS)
    docs = bf.generate(catalog["products"], days=30, end=END, seed=2026)
    assert len(docs) == 300 and {d["sku"] for _, d in docs} == set(ALL_SKUS)
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("dry run must not create a Firestore client"))
    assert bf.main(["--dry-run", "--end", "2026-10-10"]) == 0
    assert "300" in capsys.readouterr().out


def test_main_refuses_default_database(catalog, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("must not connect"))
    args = ["--project", "p", "--database", "(default)", "--catalog", str(catalog),
            "--env-file", str(tmp_path / "missing.env")]
    assert bf.main(args) == 2
    assert "(default)" in capsys.readouterr().err
