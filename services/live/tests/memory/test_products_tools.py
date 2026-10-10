"""Task 3 (sarmitha/03): location_remember and product_identify Live tools."""
import sys
import types

import pytest

from aira_live.frames import Frame
from aira_live.tools import products
from aira_live.vision.gemini import VisionError
from aira_live.vision.identify import IdentifyResult, ProductRef

CURD = ProductRef(sku="sakthi_curd_1l", name="Sakthi curd", brand="Sakthi", pack="1 L")
MILK = ProductRef(sku="aavin_milk_500ml", name="Aavin milk", brand="Aavin", pack="0.5 L")


@pytest.fixture
def wired(fake_db, session, monkeypatch):
    fake_db.store[("shops", "demo", "products", "sakthi_curd_1l")] = {
        "name": "Sakthi curd", "brand": "Sakthi", "unit": "pack", "packSizeL": 1.0}
    fake_db.store[("shops", "demo", "products", "aavin_milk_500ml")] = {
        "name": "Aavin milk", "brand": "Aavin", "unit": "pack", "packSizeL": 0.5}
    monkeypatch.setattr(products, "get_session", lambda: session)

    async def resolve(shop_id, spoken, lang):
        return {"curd": CURD, "thayir": CURD, "milk": MILK}.get(spoken.lower())

    monkeypatch.setattr(products, "catalog_resolve", resolve)
    return session


def _identify_frame(session):
    session.frames.add(Frame(id="f1", jpeg_b64="x", w=10, h=10, ts=5.0, purpose="identify"))


# --- shop_products --------------------------------------------------------------------------------------------


async def test_shop_products_from_firestore(wired):
    refs = await products.shop_products("demo")
    assert {r.sku: r.pack for r in refs} == {"sakthi_curd_1l": "1 L", "aavin_milk_500ml": "0.5 L"}


async def test_shop_products_prefers_catalog_pack_label(wired, fake_db):
    fake_db.store[("shops", "demo", "products", "aavin_ghee_200ml")] = {
        "name": "Aavin ghee", "brand": "Aavin", "unit": "jar", "pack": "200 ml jar"}
    fake_db.store[("shops", "demo", "products", "aavin_paneer_200g")] = {
        "name": "Aavin paneer", "brand": "Aavin", "unit": "pack", "packSizeG": 200}
    refs = {r.sku: r.pack for r in await products.shop_products("demo")}
    assert refs["aavin_ghee_200ml"] == "200 ml jar" and refs["aavin_paneer_200g"] == "200 g"


async def test_shop_products_skips_doc_without_name(wired, fake_db):
    fake_db.store[("shops", "demo", "products", "broken")] = {"brand": "X"}
    refs = await products.shop_products("demo")
    assert "broken" not in {r.sku for r in refs}


# --- location_remember ----------------------------------------------------------------------------------------


async def test_location_remember_saves_user_confirmed(wired, fake_db):
    out = await products.location_remember("curd", "fridge two, middle shelf, left")
    assert out["zone"] == "fridge2/middle/left"
    assert out["say"] == "Saved: Sakthi curd is on fridge 2, middle shelf, left."
    doc = fake_db.store[("shops", "demo", "locations", "sakthi_curd_1l")]
    assert doc["confirmedBy"] == "user" and doc["confidence"] == 1.0
    events = [v for p, v in fake_db.store.items() if p[:3] == ("shops", "demo", "memoryEvents")]
    assert len(events) == 1 and events[0]["type"] == "location" and events[0]["refId"] == "sakthi_curd_1l"


async def test_location_remember_speaks_in_session_language(wired):
    wired.lang = "ta"
    out = await products.location_remember("curd", "fridge2/middle/left")
    assert out["say"] == "சேமித்தேன்: Sakthi curd ஃப்ரிட்ஜ் 2, நடு அலமாரி, இடது இல் உள்ளது."


async def test_location_remember_unknown_product(wired, fake_db):
    out = await products.location_remember("paneer", "counter")
    assert out["say"] == "I don't know the product \"paneer\"."
    assert out["zone"] is None
    assert not any(p[2] in ("locations", "memoryEvents") for p in fake_db.store if len(p) > 2)


async def test_location_remember_unclear_place_saves_nothing(wired, fake_db):
    out = await products.location_remember("curd", "  the shelf ")
    assert out["say"] == "Where is Sakthi curd kept? Say a place, like fridge 2, top shelf."
    assert out["zone"] is None
    assert not any(p[2] in ("locations", "memoryEvents") for p in fake_db.store if len(p) > 2)


# --- product_identify -----------------------------------------------------------------------------------------


async def test_product_identify_ok(wired, monkeypatch):
    _identify_frame(wired)

    async def ident(frame, candidates):
        assert frame.id == "f1"
        assert {c.sku for c in candidates} == {"sakthi_curd_1l", "aavin_milk_500ml"}
        return IdentifyResult(sku="aavin_milk_500ml", confidence=0.91, alternatives=[])

    monkeypatch.setattr(products, "identify_product", ident)
    out = await products.product_identify()
    assert wired.requested == ["identify"]
    assert out == {"say": "This is Aavin milk, 0.5 L.", "sku": "aavin_milk_500ml", "confidence": 0.91}


async def test_product_identify_low_confidence_says_not_sure(wired, monkeypatch):
    _identify_frame(wired)

    async def ident(frame, candidates):
        return IdentifyResult(sku="sakthi_curd_1l", confidence=0.4, alternatives=["aavin_milk_500ml"])

    monkeypatch.setattr(products, "identify_product", ident)
    out = await products.product_identify()
    assert out["sku"] is None and out["say"] == "Not sure. Hold it closer to the camera."


async def test_product_identify_lookalike_asks_for_label(wired, fake_db, monkeypatch):
    fake_db.store[("shops", "demo", "products", "aavin_milk_1l")] = {
        "name": "Aavin milk", "brand": "Aavin", "unit": "pack", "packSizeL": 1.0}
    _identify_frame(wired)

    async def ident(frame, candidates):  # vision withheld the sku: 500 ml vs 1 L, label not readable
        return IdentifyResult(sku=None, confidence=0.75, alternatives=["aavin_milk_500ml", "aavin_milk_1l"])

    monkeypatch.setattr(products, "identify_product", ident)
    out = await products.product_identify()
    assert out["sku"] is None
    assert out["say"] == "Not sure. Turn the pack so the label faces the camera."


async def test_product_identify_sku_outside_shop_says_not_sure(wired, monkeypatch):
    _identify_frame(wired)

    async def ident(frame, candidates):
        return IdentifyResult(sku="someone_elses_sku", confidence=0.95, alternatives=[])

    monkeypatch.setattr(products, "identify_product", ident)
    out = await products.product_identify()
    assert out["sku"] is None and out["say"] == "Not sure. Hold it closer to the camera."


async def test_product_identify_no_frame(wired):
    out = await products.product_identify()
    assert out["say"] == "I can't see a camera picture. Please check the camera." and out["sku"] is None


async def test_product_identify_vision_down_is_honest(wired, monkeypatch):
    _identify_frame(wired)

    async def ident(frame, candidates):
        raise VisionError("all models failed")

    monkeypatch.setattr(products, "identify_product", ident)
    out = await products.product_identify()
    assert out["sku"] is None and out["confidence"] == 0.0
    assert out["say"] == "I can't reach the AI right now. Please try again in a minute."


# --- catalog_resolve (Kanish's business.catalog, imported at call time) ---------------------------------------


async def test_catalog_resolve_uses_business_catalog(monkeypatch):
    fake = types.ModuleType(products.CATALOG_MODULE)
    seen = []

    async def resolve(shop_id, spoken, lang):
        seen.append((shop_id, spoken, lang))
        return CURD

    fake.resolve = resolve
    monkeypatch.setitem(sys.modules, products.CATALOG_MODULE, fake)
    assert await products.catalog_resolve("demo", "thayir", "ta") == CURD
    assert seen == [("demo", "thayir", "ta")]


@pytest.fixture
def no_catalog(fake_db, monkeypatch):
    monkeypatch.setattr(products, "_catalog_module", lambda: None)
    base = ("shops", "demo", "products")
    fake_db.store[base + ("aavin_milk_500ml",)] = {
        "sku": "aavin_milk_500ml", "name": "Aavin Milk 500 ml", "brand": "Aavin", "pack": "500 ml pouch",
        "defaultPack": True, "aliases": {"en": ["aavin milk", "milk"], "ta": ["பால்"]}}
    fake_db.store[base + ("aavin_milk_1l",)] = {
        "sku": "aavin_milk_1l", "name": "Aavin Milk 1 L", "brand": "Aavin", "pack": "1 L pouch",
        "defaultPack": False, "aliases": {"en": ["one litre milk", "aavin milk", "milk"], "ta": ["பால்"]}}
    fake_db.store[base + ("sakthi_curd_1l",)] = {
        "sku": "sakthi_curd_1l", "name": "Sakthi Curd 1 L", "brand": "Sakthi", "pack": "1 L pack",
        "aliases": {"en": ["curd"], "ta": ["தயிர்"]}}
    return fake_db


async def test_fallback_resolve_alias_name_and_sku(no_catalog):
    assert (await products.catalog_resolve("demo", "Curd", "en")).sku == "sakthi_curd_1l"
    assert (await products.catalog_resolve("demo", "தயிர்", "ta")).sku == "sakthi_curd_1l"
    assert (await products.catalog_resolve("demo", "aavin milk 1 l", "en")).sku == "aavin_milk_1l"
    assert (await products.catalog_resolve("demo", "aavin_milk_1l", "en")).sku == "aavin_milk_1l"
    assert (await products.catalog_resolve("demo", "one litre milk", "en")).pack == "1 L pouch"


async def test_fallback_resolve_shared_alias_picks_default_pack(no_catalog):
    assert (await products.catalog_resolve("demo", "milk", "en")).sku == "aavin_milk_500ml"


async def test_fallback_resolve_unknown_or_blank_is_none(no_catalog):
    assert await products.catalog_resolve("demo", "biscuit", "en") is None
    assert await products.catalog_resolve("demo", "   ", "en") is None


async def test_fallback_resolve_ambiguous_without_default_is_none(no_catalog):
    no_catalog.store[("shops", "demo", "products", "aavin_milk_500ml")]["defaultPack"] = False
    assert await products.catalog_resolve("demo", "milk", "en") is None


# --- registry ---------------------------------------------------------------------------------------------------


def test_registry_exposes_task3_tools():
    from aira_live.tools import TOOLS_BY_NAME

    assert TOOLS_BY_NAME["location_remember"] is products.location_remember
    assert TOOLS_BY_NAME["product_identify"] is products.product_identify
