"""Task 3 (sarmitha/03): location_remember and product_identify Live tools."""
from pathlib import Path

import pytest

from aira_live.business import catalog, seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.frames import Frame
from aira_live.tools import products
from aira_live.vision.gemini import VisionError
from aira_live.vision.identify import IdentifyResult, ProductRef

SUNRISE_CATALOG = Path(__file__).resolve().parents[4] / "content" / "catalog" / "murugan_dairy.json"

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


# --- the real business.catalog.resolve over the seeded Sunrise Dairy catalog ----------------------------------


@pytest.fixture
async def sunrise(fake_db, session, monkeypatch):
    """Sunrise Dairy seeded into both backends: business.store (catalog.resolve) and aira_live.db (shop_products)."""
    data = seed.load_catalog(SUNRISE_CATALOG)
    store = MemoryStore()
    await seed.seed_into(store, data, env={})
    for p in data["products"]:
        fake_db.store[("shops", "sunrise_dairy", "products", p["sku"])] = dict(p)
    set_store(store)
    session.shop_id = "sunrise_dairy"
    monkeypatch.setattr(products, "get_session", lambda: session)
    yield session
    set_store(None)


def test_catalog_resolve_is_business_catalog():
    assert products.catalog_resolve is catalog.resolve


async def test_location_remember_real_catalog_tamil_alias(sunrise, fake_db):
    sunrise.lang = "ta"
    out = await products.location_remember("தயிர்", "fridge two, middle shelf, left")
    assert out["zone"] == "fridge2/middle/left"
    assert out["say"].startswith("சேமித்தேன்: Sakthi Curd 1 L ")
    doc = fake_db.store[("shops", "sunrise_dairy", "locations", "sakthi_curd_1l")]
    assert doc["confirmedBy"] == "user" and doc["zone"] == "fridge2/middle/left"


async def test_location_remember_real_catalog_size_hint(sunrise, fake_db):
    out = await products.location_remember("half litre curd", "fridge 2 top shelf")
    assert out["zone"] == "fridge2/top"
    assert ("shops", "sunrise_dairy", "locations", "sakthi_curd_500ml") in fake_db.store
    assert ("shops", "sunrise_dairy", "locations", "sakthi_curd_1l") not in fake_db.store


async def test_location_remember_real_catalog_unknown_saves_nothing(sunrise, fake_db):
    out = await products.location_remember("biscuit", "counter")
    assert out == {"say": "I don't know the product \"biscuit\".", "zone": None}
    assert not any(p[2] in ("locations", "memoryEvents") for p in fake_db.store if len(p) > 2)


async def test_shop_products_match_catalog_refs(sunrise):
    """identify / product_find compare a camera sku with a resolved ref: both sides must name packs the same."""
    refs = await products.shop_products("sunrise_dairy")
    assert len(refs) == 10
    for ref in refs:
        assert await catalog.resolve("sunrise_dairy", ref.sku, "en") == ref


async def test_product_identify_real_catalog_speaks_pack(sunrise, monkeypatch):
    _identify_frame(sunrise)

    async def ident(frame, candidates):
        assert len(candidates) == 10
        return IdentifyResult(sku="aavin_buttermilk_200ml", confidence=0.88, alternatives=[])

    monkeypatch.setattr(products, "identify_product", ident)
    out = await products.product_identify()
    assert out == {"say": "This is Aavin Buttermilk 200 ml, 200 ml pouch.", "sku": "aavin_buttermilk_200ml",
                   "confidence": 0.88}


# --- registry ---------------------------------------------------------------------------------------------------


def test_registry_exposes_task3_tools():
    from aira_live.tools import TOOLS_BY_NAME

    assert TOOLS_BY_NAME["location_remember"] is products.location_remember
    assert TOOLS_BY_NAME["product_identify"] is products.product_identify
