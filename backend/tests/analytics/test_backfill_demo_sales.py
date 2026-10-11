"""shared/scripts/backfill_demo_sales.py: 30 days of plausible demo SALE ledger rows for Firestore database "aira"."""
import importlib.util
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "shared" / "scripts" / "backfill_demo_sales.py"
spec = importlib.util.spec_from_file_location("backfill_demo_sales", SCRIPT)
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)

IST = ZoneInfo("Asia/Kolkata")
END = date(2026, 10, 10)
ALL_SKUS = ["aavin_milk_500ml", "aavin_milk_1l", "sakthi_curd_500ml", "sakthi_curd_1l", "aavin_buttermilk_200ml",
            "aavin_ghee_200ml", "aavin_ghee_500ml", "aavin_paneer_200g", "aavin_butter_100g", "arun_icecream_box"]
PACK_L = {"aavin_milk_500ml": 0.5, "aavin_milk_1l": 1.0, "sakthi_curd_500ml": 0.5, "sakthi_curd_1l": 1.0}
# Same units as shared/catalog/sunrise_dairy.json: milk and curd sell in litres, the rest per pack/jar/box.
PRODUCTS = [{"sku": s, "unit": "L", "packSizeL": PACK_L[s]} if s in PACK_L else {"sku": s, "unit": "pack"}
            for s in ALL_SKUS]
# The shape business.ledger.prepare_movement writes (contract LedgerTxn needs `id`; the ledger also keeps `unit`).
LEDGER_KEYS = {"id", "sku", "delta", "unit", "reason", "refType", "demo", "refId", "idemKey", "at", "actor"}
UNIT = {p["sku"]: p["unit"] for p in PRODUCTS}


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
        assert path == f"shops/sunrise_dairy/ledger/backfill-{ist_day(data).isoformat()}-{data['sku']}"
        assert set(data) == LEDGER_KEYS
        assert data["reason"] == "SALE" and data["delta"] < 0 and data["actor"] == "system"
        assert data["refType"] == bf.REF_TYPE == "demo_backfill" and data["idemKey"] == path.rsplit("/", 1)[1]
        assert data["demo"] is True                                                   # invented rows are labelled
        assert data["id"] == path.rsplit("/", 1)[1] and data["unit"] == UNIT[data["sku"]]
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


def test_target_cloud_needs_an_explicit_project_and_the_yes_flag():
    assert bf.target("demo-aira", None, {}, yes_write_cloud=True) == ("demo-aira", "aira", None)
    assert bf.target("p", None, {"FIRESTORE_DATABASE": "aira-test"}, yes_write_cloud=True) == ("p", "aira-test", None)
    with pytest.raises(ValueError, match="--project"):     # GCP_PROJECT from env or .env never picks the cloud
        bf.target(None, None, {"GCP_PROJECT": "demo-aira"}, yes_write_cloud=True)
    with pytest.raises(ValueError, match="--yes-write-cloud"):
        bf.target("p", None, {})
    with pytest.raises(ValueError, match="FIRESTORE_EMULATOR_HOST"):   # meant for the emulator: never the cloud
        bf.target("p", None, {"FIRESTORE_EMULATOR_HOST": "127.0.0.1:8085"}, yes_write_cloud=True)


def test_target_emulator_uses_the_env_host_else_the_firebase_json_port():
    env = {"FIRESTORE_EMULATOR_HOST": "127.0.0.1:9999", "GCP_PROJECT": "demo-x"}
    assert bf.target(None, None, env, emulator=True) == ("demo-x", "aira", "127.0.0.1:9999")
    assert bf.target(None, None, {}, emulator=True) == ("demo-aira", "aira", "127.0.0.1:8085")
    assert bf.target("p", None, {}, emulator=True) == ("p", "aira", "127.0.0.1:8085")
    with pytest.raises(ValueError):
        bf.target("p", None, {}, emulator=True, yes_write_cloud=True)


def test_target_refuses_the_default_database():
    for kw in ({"emulator": True}, {"yes_write_cloud": True}):
        with pytest.raises(ValueError):
            bf.target("p", "(default)", {}, **kw)
        with pytest.raises(ValueError):
            bf.target("p", None, {"FIRESTORE_DATABASE": "(default)"}, **kw)


def test_docstring_examples_never_write_to_the_cloud_by_accident():
    runs = [ln for ln in (bf.__doc__ or "").splitlines() if "backfill_demo_sales.py" in ln and "uv run" in ln]
    assert runs
    for ln in runs:
        assert "--dry-run" in ln or "--emulator" in ln or ("--project" in ln and "--yes-write-cloud" in ln), ln


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
    path.write_text(json.dumps({"shop": {"id": "sunrise_dairy", "name": "Sunrise Dairy", "city": "Chennai"},
                                "products": PRODUCTS, "suppliers": []}), encoding="utf-8")
    return path


def test_main_dry_run_writes_nothing(catalog, monkeypatch, capsys):
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("dry run must not create a Firestore client"))
    assert bf.main(["--dry-run", "--catalog", str(catalog), "--end", "2026-10-10"]) == 0
    out = capsys.readouterr().out
    assert "300" in out and "2026-09-11" in out and "2026-10-10" in out


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No emulator host, project or database in os.environ (restored afterwards) and no .env file."""
    for name in ("FIRESTORE_EMULATOR_HOST", "GCP_PROJECT", "FIRESTORE_DATABASE"):
        monkeypatch.setenv(name, "x")   # setenv then delenv: monkeypatch restores the original value
        monkeypatch.delenv(name)
    return tmp_path / "missing.env"


def _spy(monkeypatch):
    seen, client = [], FakeClient()

    def make_client(project, database):
        seen.append((project, database, os.environ.get("FIRESTORE_EMULATOR_HOST")))
        return client
    monkeypatch.setattr(bf, "make_client", make_client)
    return seen, client


def test_default_env_file_is_the_root_dotenv_then_shared_dotenv(tmp_path, monkeypatch):
    monkeypatch.setattr(bf, "ROOT", tmp_path)
    assert bf.default_env_file() == tmp_path / ".env"
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / ".env").write_text("GCP_PROJECT=shared\n", encoding="utf-8")
    assert bf.default_env_file() == tmp_path / "shared" / ".env"
    (tmp_path / ".env").write_text("GCP_PROJECT=root\n", encoding="utf-8")
    assert bf.default_env_file() == tmp_path / ".env"


def test_main_default_is_a_dry_run_even_with_gcp_project_set(catalog, monkeypatch, clean_env, tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("GCP_PROJECT=real-project\n", encoding="utf-8")
    monkeypatch.setenv("GCP_PROJECT", "real-project")
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("no write flag: must not create a client"))
    for extra in ([], ["--project", "real-project"]):
        assert bf.main(["--catalog", str(catalog), "--end", "2026-10-10", "--env-file", str(env_file), *extra]) == 0
        out = capsys.readouterr().out
        assert "dry run" in out and "300" in out and "--emulator" in out and "--yes-write-cloud" in out


def test_main_cloud_write_refuses_without_explicit_project(catalog, monkeypatch, clean_env, tmp_path, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("GCP_PROJECT=real-project\n", encoding="utf-8")
    monkeypatch.setenv("GCP_PROJECT", "real-project")
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("must not connect"))
    args = ["--yes-write-cloud", "--catalog", str(catalog), "--end", "2026-10-10", "--env-file", str(env_file)]
    assert bf.main(args) == 2
    assert "--project" in capsys.readouterr().err


def test_main_cloud_write_refuses_when_the_env_file_names_an_emulator(catalog, monkeypatch, clean_env, tmp_path,
                                                                    capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("FIRESTORE_EMULATOR_HOST=127.0.0.1:8085\n", encoding="utf-8")
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("must not connect"))
    args = ["--project", "p", "--yes-write-cloud", "--catalog", str(catalog), "--env-file", str(env_file)]
    assert bf.main(args) == 2
    assert "FIRESTORE_EMULATOR_HOST" in capsys.readouterr().err


def test_main_writes_to_database_aira_with_project_and_yes_flag(catalog, monkeypatch, clean_env, capsys):
    seen, client = _spy(monkeypatch)
    args = ["--project", "demo-aira", "--yes-write-cloud", "--catalog", str(catalog), "--end", "2026-10-10",
            "--env-file", str(clean_env)]
    assert bf.main(args) == 0
    assert seen == [("demo-aira", "aira", None)]
    assert sum(len(c) for c in client.commits) == 300
    assert "300" in capsys.readouterr().out


def test_main_emulator_exports_the_env_file_host_before_connecting(catalog, monkeypatch, clean_env, tmp_path,
                                                                  capsys):
    env_file = tmp_path / ".env"
    env_file.write_text("FIRESTORE_EMULATOR_HOST=127.0.0.1:9999\nGCP_PROJECT=demo-x\n", encoding="utf-8")
    seen, client = _spy(monkeypatch)
    args = ["--emulator", "--catalog", str(catalog), "--end", "2026-10-10", "--env-file", str(env_file)]
    assert bf.main(args) == 0
    assert seen == [("demo-x", "aira", "127.0.0.1:9999")]          # google-cloud-firestore reads os.environ only
    assert sum(len(c) for c in client.commits) == 300
    assert "emulator" in capsys.readouterr().out


def test_main_emulator_defaults_to_the_firebase_json_port(catalog, monkeypatch, clean_env):
    seen, _ = _spy(monkeypatch)
    assert bf.main(["--emulator", "--catalog", str(catalog), "--env-file", str(clean_env)]) == 0
    assert seen == [("demo-aira", "aira", "127.0.0.1:8085")]


def test_main_dry_run_flag_wins_over_write_flags(catalog, monkeypatch, clean_env, capsys):
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("dry run must not create a Firestore client"))
    assert bf.main(["--dry-run", "--emulator", "--catalog", str(catalog), "--env-file", str(clean_env)]) == 0
    assert "dry run" in capsys.readouterr().out


def test_main_emulator_and_cloud_flags_are_exclusive(catalog, monkeypatch, clean_env):
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("must not connect"))
    with pytest.raises(SystemExit) as exc:
        bf.main(["--emulator", "--project", "p", "--yes-write-cloud", "--catalog", str(catalog),
                 "--env-file", str(clean_env)])
    assert exc.value.code == 2


def test_repo_catalog_backfills_all_ten_skus_for_thirty_days(monkeypatch, capsys):
    catalog = json.loads(bf.DEFAULT_CATALOG.read_text(encoding="utf-8"))
    assert {p["sku"] for p in catalog["products"]} == set(ALL_SKUS)
    docs = bf.generate(catalog["products"], days=30, end=END, seed=2026)
    assert len(docs) == 300 and {d["sku"] for _, d in docs} == set(ALL_SKUS)
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("dry run must not create a Firestore client"))
    assert bf.main(["--dry-run", "--end", "2026-10-10"]) == 0
    assert "300" in capsys.readouterr().out


def test_main_refuses_default_database(catalog, monkeypatch, clean_env, capsys):
    monkeypatch.setattr(bf, "make_client", lambda *a: pytest.fail("must not connect"))
    for mode in (["--emulator"], ["--project", "p", "--yes-write-cloud"]):
        args = [*mode, "--database", "(default)", "--catalog", str(catalog), "--env-file", str(clean_env)]
        assert bf.main(args) == 2
        assert "(default)" in capsys.readouterr().err
