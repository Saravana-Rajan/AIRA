"""Task 3 (kanish/01): seed catalog, ta/hi/en product + supplier resolution, to_selling_units and the seeder."""
import copy
import importlib.util
from pathlib import Path

import pytest

from aira_live.business import catalog, seed
from aira_live.business.store import MemoryStore, set_store

SHOP = "sunrise_dairy"
ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "shared" / "catalog" / "sunrise_dairy.json"
ALL_SKUS = ["aavin_milk_500ml", "aavin_milk_1l", "sakthi_curd_500ml", "sakthi_curd_1l", "aavin_buttermilk_200ml",
            "aavin_ghee_200ml", "aavin_ghee_500ml", "aavin_paneer_200g", "aavin_butter_100g", "arun_icecream_box"]


def _product(sku: str) -> dict:
    return copy.deepcopy(next(p for p in seed.load_catalog(CATALOG)["products"] if p["sku"] == sku))


def _supplier(supplier_id: str) -> dict:
    return copy.deepcopy(next(s for s in seed.load_catalog(CATALOG)["suppliers"] if s["id"] == supplier_id))


# ---------- product resolution ----------

@pytest.mark.parametrize("spoken,lang,sku", [
    # the plan's core cases
    ("curd", "en", "sakthi_curd_1l"),
    ("half litre sakthi curd", "en", "sakthi_curd_500ml"),
    ("aavin milk", "en", "aavin_milk_500ml"),
    ("one litre aavin milk", "en", "aavin_milk_1l"),
    ("give me two litres of curd please", "en", "sakthi_curd_1l"),
    ("சக்தி தயிர் அரை லிட்டர்", "ta", "sakthi_curd_500ml"),
    ("दही", "hi", "sakthi_curd_1l"),
    ("ice cream", "en", "arun_icecream_box"),
    ("shakti curd", "en", "sakthi_curd_1l"),                 # ASR spelling
    # the full dairy range, in all three languages
    ("milk", "en", "aavin_milk_500ml"),
    ("ஆவின் பால்", "ta", "aavin_milk_500ml"),
    ("ஒரு லிட்டர் பால்", "ta", "aavin_milk_1l"),
    ("एक लीटर दूध", "hi", "aavin_milk_1l"),
    ("आधा लीटर दही", "hi", "sakthi_curd_500ml"),
    ("milk 1 litre", "en", "aavin_milk_1l"),
    ("500ml curd", "en", "sakthi_curd_500ml"),
    ("one milk", "en", "aavin_milk_500ml"),                 # a count, not a size: the default pouch
    ("buttermilk", "en", "aavin_buttermilk_200ml"),
    ("butter milk", "en", "aavin_buttermilk_200ml"),
    ("மோர்", "ta", "aavin_buttermilk_200ml"),
    ("छाछ", "hi", "aavin_buttermilk_200ml"),
    ("ghee", "en", "aavin_ghee_200ml"),
    ("big ghee jar", "en", "aavin_ghee_500ml"),
    ("ghee 500 ml", "en", "aavin_ghee_500ml"),
    ("நெய் அரை லிட்டர்", "ta", "aavin_ghee_500ml"),
    ("दो सौ एमएल घी", "hi", "aavin_ghee_200ml"),
    ("पांच सौ एमएल घी", "hi", "aavin_ghee_500ml"),          # size in number words picks the non-default jar
    ("ஐநூறு மில்லி நெய்", "ta", "aavin_ghee_500ml"),
    ("five hundred ml ghee", "en", "aavin_ghee_500ml"),
    ("aavin butter milk", "en", "aavin_buttermilk_200ml"),
    ("paneer", "en", "aavin_paneer_200g"),
    ("பனீர்", "ta", "aavin_paneer_200g"),
    ("पनीर", "hi", "aavin_paneer_200g"),
    ("butter", "en", "aavin_butter_100g"),
    ("வெண்ணெய்", "ta", "aavin_butter_100g"),
    ("मक्खन", "hi", "aavin_butter_100g"),
    ("ஐஸ்கிரீம்", "ta", "arun_icecream_box"),
    ("आइसक्रीम", "hi", "arun_icecream_box"),
    ("icecream", "en", "arun_icecream_box"),
    ("thayir", "ta", "sakthi_curd_1l"),                     # romanised Tamil from the Live transcript
    ("doodh", "hi", "aavin_milk_500ml"),                    # romanised Hindi
    ("दही", "en", "sakthi_curd_1l"),                        # owners mix languages: every alias counts
    ("aavin_ghee_500ml", "en", "aavin_ghee_500ml"),         # a SKU id passes straight through
])
async def test_resolve(store, spoken, lang, sku):
    ref = await catalog.resolve(SHOP, spoken, lang)
    assert ref is not None and ref.sku == sku


async def test_resolve_returns_product_ref_fields(store):
    ref = await catalog.resolve(SHOP, "sakthi curd one litre", "en")
    assert ref.model_dump() == {"sku": "sakthi_curd_1l", "name": "Sakthi Curd 1 L", "brand": "Sakthi",
                                "pack": "1 L pack"}


@pytest.mark.parametrize("spoken", [
    "biscuit",          # not sold here
    "",
    "   ",
    "aavin",            # brand only: seven Aavin products
    "milk and curd",    # two different products in one name
    "500 ml",           # a size with no product
])
async def test_resolve_none(store, spoken):
    assert await catalog.resolve(SHOP, spoken, "en") is None


async def test_every_sku_resolves_from_its_own_name(store):
    for sku in ALL_SKUS:
        ref = await catalog.resolve(SHOP, _product(sku)["name"], "en")
        assert ref is not None and ref.sku == sku, sku


@pytest.fixture
async def bare_store():
    """Two size variants of one product and NO default pack: naming it without a size is ambiguous."""
    s = MemoryStore()
    set_store(s)
    for sku, name, size in [("x_milk_500ml", "X Milk 500 ml", 0.5), ("x_milk_1l", "X Milk 1 L", 1.0)]:
        await s.set(f"shops/{SHOP}/products/{sku}", {
            "sku": sku, "name": name, "brand": "X", "pack": name[7:], "unit": "L", "packSizeL": size,
            "defaultPack": False, "aliases": {"en": ["x milk"], "ta": ["எக்ஸ் பால்"], "hi": ["एक्स दूध"]}})
    yield s
    set_store(None)


async def test_resolve_ambiguous_without_default_pack(bare_store):
    assert await catalog.resolve(SHOP, "x milk", "en") is None              # tool asks biz.product.ambiguous
    assert (await catalog.resolve(SHOP, "x milk half litre", "en")).sku == "x_milk_500ml"
    assert (await catalog.resolve(SHOP, "எக்ஸ் பால் ஒரு லிட்டர்", "ta")).sku == "x_milk_1l"
    assert await catalog.resolve(SHOP, "two litres x milk", "en") is None   # a size no pack has: still ask


# ---------- supplier resolution ----------

@pytest.mark.parametrize("spoken,lang,supplier_id", [
    ("sakthi vendor", "en", "sakthi_vendor"),
    ("milk vendor", "en", "aavin_vendor"),
    ("ice cream", "en", "arun_distributor"),       # through the product it supplies
    ("paneer guy", "en", "aavin_vendor"),          # paneer is an Aavin product
    ("Sakthi", "en", "sakthi_vendor"),             # the brand alone
    ("தயிர் வியாபாரி", "ta", "sakthi_vendor"),
    ("பால்காரர்", "ta", "aavin_vendor"),
    ("दूध वाले", "hi", "aavin_vendor"),
    ("अरुण डिस्ट्रीब्यूटर", "hi", "arun_distributor"),
    ("arun_distributor", "en", "arun_distributor"),  # an id passes straight through
    ("biscuit guy", "en", None),
    ("", "en", None),
    ("vendor", "en", None),                        # every supplier is a vendor: ask which
])
async def test_resolve_supplier(store, spoken, lang, supplier_id):
    sup = await catalog.resolve_supplier(SHOP, spoken, lang)
    assert (sup["id"] if sup else None) == supplier_id


async def test_resolve_supplier_returns_the_order_fields(store):
    sup = await catalog.resolve_supplier(SHOP, "sakthi vendor", "en")
    assert {"id", "name", "phone", "channel", "products", "aliases"} <= set(sup)
    assert sup["name"] == "Sakthi curd vendor" and sup["channel"] == "telegram" and sup["phone"] == ""
    assert sup["products"] == ["sakthi_curd_500ml", "sakthi_curd_1l"]
    assert "phoneEnv" not in sup


# ---------- to_selling_units ----------

@pytest.mark.parametrize("sku,qty,unit,expected", [
    ("aavin_paneer_200g", 5, "kg", (25.0, "pack")),          # "5 kg paneer" → 25 packs of 200 g
    ("aavin_milk_500ml", 2, "litres", (2.0, "L")),           # "2 litres milk"
    ("aavin_milk_1l", 2, "L", (2.0, "L")),
    ("aavin_ghee_200ml", 3, "jar", (3.0, "jar")),            # "3 ghee"
    ("aavin_ghee_200ml", 3, "", (3.0, "jar")),
    ("sakthi_curd_500ml", 2, "pack", (1.0, "L")),            # counted packs of a litre-unit product
    ("sakthi_curd_500ml", 3, "packets", (1.5, "L")),
    ("sakthi_curd_1l", 30, "L", (30.0, "L")),
    ("aavin_milk_500ml", 500, "ml", (0.5, "L")),
    ("aavin_buttermilk_200ml", 1, "litre", (5.0, "pack")),
    ("aavin_buttermilk_200ml", 3, "pouch", (3.0, "pack")),
    ("aavin_ghee_200ml", 1, "L", (5.0, "jar")),
    ("aavin_ghee_500ml", 1000, "ml", (2.0, "jar")),
    ("aavin_paneer_200g", 400, "g", (2.0, "pack")),
    ("aavin_paneer_200g", 1, "pack", (1.0, "pack")),
    ("aavin_butter_100g", 1, "kilo", (10.0, "pack")),
    ("arun_icecream_box", 2, "box", (2.0, "box")),
    ("arun_icecream_box", 2, "pack", (2.0, "box")),
    ("sakthi_curd_1l", 2, "லிட்டர்", (2.0, "L")),             # Tamil unit words
    ("sakthi_curd_500ml", 3, "பாக்கெட்", (1.5, "L")),
    ("aavin_paneer_200g", 1, "கிலோ", (5.0, "pack")),
    ("aavin_milk_1l", 2, "लीटर", (2.0, "L")),                # Hindi unit words
    ("aavin_paneer_200g", 1, "किलो", (5.0, "pack")),
    ("aavin_butter_100g", 200, "ग्राम", (2.0, "pack")),
    ("aavin_milk_500ml", 0, "L", (0.0, "L")),
    ("aavin_paneer_200g", 2, " Kg ", (10.0, "pack")),          # whitespace and case are ignored
])
def test_to_selling_units(sku, qty, unit, expected):
    result = catalog.to_selling_units(_product(sku), qty, unit)
    assert result == expected
    assert isinstance(result[0], float)


@pytest.mark.parametrize("sku,qty,unit", [
    ("aavin_milk_500ml", 1, "kg"),            # mass of a litre product
    ("aavin_paneer_200g", 1, "litre"),        # volume of a gram product
    ("arun_icecream_box", 1, "L"),            # no pack size to convert with
    ("arun_icecream_box", 500, "g"),
    ("aavin_milk_500ml", -1, "pack"),         # negative quantity
    ("aavin_milk_500ml", float("nan"), "L"),
    ("aavin_milk_500ml", 1, "bucket"),        # unknown unit word
    ("aavin_milk_500ml", 2, "jar"),           # a container this product is not sold in
    ("aavin_ghee_200ml", 2, "box"),
    ("aavin_paneer_200g", 300, "g"),          # 1.5 packs: never invent a half pack
    ("aavin_milk_1l", 1.5, "L"),
    ("aavin_ghee_200ml", 0.5, "L"),
    ("sakthi_curd_1l", 1.5, "pack"),
])
def test_to_selling_units_rejects(sku, qty, unit):
    with pytest.raises(ValueError):
        catalog.to_selling_units(_product(sku), qty, unit)


def test_one_pack_of_every_product_is_its_pack_size():
    for sku in ALL_SKUS:
        p = _product(sku)
        qty, unit = catalog.to_selling_units(p, 1, "pack")
        assert unit == p["unit"], sku
        assert qty == (p["packSizeL"] if p["unit"] == "L" else 1.0), sku


def test_to_selling_units_does_not_mutate_the_product():
    p = _product("aavin_paneer_200g")
    before = copy.deepcopy(p)
    catalog.to_selling_units(p, 5, "kg")
    assert p == before


# ---------- seeder ----------

async def test_seed_writes_shop_products_and_suppliers(store):
    assert (await store.get(f"shops/{SHOP}"))["name"] == "Sunrise Dairy"
    curd = await store.get(f"shops/{SHOP}/products/sakthi_curd_1l")
    assert curd["price"] == 60 and curd["sku"] == "sakthi_curd_1l" and curd["unit"] == "L"
    assert (await store.get(f"shops/{SHOP}/suppliers/sakthi_vendor"))["channel"] == "telegram"
    assert (await store.get(f"shops/{SHOP}/suppliers/arun_distributor"))["channel"] == "call"
    assert [sku for sku, _ in await store.list(f"shops/{SHOP}/products")] == sorted(ALL_SKUS)
    assert [sid for sid, _ in await store.list(f"shops/{SHOP}/suppliers")] == \
        ["aavin_vendor", "arun_distributor", "sakthi_vendor"]
    assert await store.list(f"shops/{SHOP}/inventory") == []          # stock is seeded through the ledger


async def test_seed_reads_supplier_phones_from_env(monkeypatch):
    sakthi_env = _supplier("sakthi_vendor")["phoneEnv"]
    monkeypatch.setenv(sakthi_env, "+919876543210")
    monkeypatch.delenv(_supplier("aavin_vendor")["phoneEnv"], raising=False)
    s = MemoryStore()
    await seed.seed_into(s, seed.load_catalog(CATALOG))
    sakthi = await s.get(f"shops/{SHOP}/suppliers/sakthi_vendor")
    assert sakthi["phone"] == "+919876543210"
    assert (await s.get(f"shops/{SHOP}/suppliers/aavin_vendor"))["phone"] == ""
    assert "phoneEnv" not in sakthi


async def test_seed_env_mapping_is_used_instead_of_os_environ(monkeypatch):
    arun_env = _supplier("arun_distributor")["phoneEnv"]
    monkeypatch.setenv(arun_env, "+910000000000")
    s = MemoryStore()
    await seed.seed_into(s, seed.load_catalog(CATALOG), env={arun_env: "+919800000003"})
    assert (await s.get(f"shops/{SHOP}/suppliers/arun_distributor"))["phone"] == "+919800000003"


async def test_seed_is_idempotent_and_leaves_the_catalog_unchanged():
    cat = seed.load_catalog(CATALOG)
    before = copy.deepcopy(cat)
    s = MemoryStore()
    await seed.seed_into(s, cat, env={})
    first = copy.deepcopy(s.data)
    await seed.seed_into(s, cat, env={})
    assert s.data == first
    assert cat == before


async def test_seed_rejects_a_supplier_product_that_is_not_in_the_catalog():
    cat = seed.load_catalog(CATALOG)
    cat["suppliers"][0]["products"].append("mystery_item")
    with pytest.raises(ValueError):
        await seed.seed_into(MemoryStore(), cat, env={})


async def test_seed_docs_match_the_firestore_seed_script():
    """shared/scripts/seed_firestore.py (foundation) and seed_into write the same shop/product/supplier docs."""
    spec = importlib.util.spec_from_file_location("seed_firestore", ROOT / "shared" / "scripts" / "seed_firestore.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    cat = seed.load_catalog(CATALOG)
    env = {s["phoneEnv"]: f"+91980000000{i}" for i, s in enumerate(cat["suppliers"])}
    expected = {p: d for p, d in script.build_docs(cat, env) if "/inventory/" not in p}
    s = MemoryStore()
    await seed.seed_into(s, cat, env=env)
    assert s.data == expected


def test_seed_cli_seeds_the_given_catalog(monkeypatch, capsys):
    s = MemoryStore()
    monkeypatch.setattr(seed, "_firestore_store", lambda: s)
    monkeypatch.delenv("FIRESTORE_DATABASE", raising=False)
    assert seed.main(["--catalog", str(CATALOG)]) == 0
    assert "products/sakthi_curd_1l" in " ".join(s.data)
    assert "seeded" in capsys.readouterr().out


def test_seed_cli_refuses_the_default_database(monkeypatch, capsys):
    monkeypatch.setattr(seed, "_firestore_store", lambda: pytest.fail("must not connect"))
    monkeypatch.setenv("FIRESTORE_DATABASE", "(default)")
    assert seed.main(["--catalog", str(CATALOG)]) == 2
    assert "aira" in capsys.readouterr().err
