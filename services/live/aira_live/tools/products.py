"""Live tools: product_find, product_identify, location_remember (spec §2 steps 5–6). OWNER: Sarmitha (sarmitha/03).

Memory only remembers and guides: these tools never write stock or money. A location the owner states is saved
as user-confirmed; a camera read below 0.6 confidence is spoken as "Not sure", never as a guess.
"""
import importlib
import logging

from aira_live import db as dbmod
from aira_live.i18n import t
from aira_live.memory import episodic, spatial
from aira_live.memory.zones import normalize_zone, speak_zone
from aira_live.session import get_session
from aira_live.vision.gemini import VisionError
from aira_live.vision.identify import ProductRef, identify_product, next_hint

log = logging.getLogger(__name__)

MIN_ID_CONF = 0.6
CATALOG_MODULE = "aira_live.business.catalog"


# --- this shop's products -------------------------------------------------------------------------------------


def _pack(d: dict) -> str:
    """Spoken pack label: the catalog's own label ("500 ml pouch") first, else built from the pack size."""
    if d.get("pack"):
        return str(d["pack"])
    if d.get("packSizeL"):
        return f"{float(d['packSizeL']):g} L"
    if d.get("packSizeG"):
        return f"{float(d['packSizeG']):g} g"
    return str(d.get("unit", ""))


def _ref(sku: str, d: dict) -> ProductRef | None:
    if not d.get("name"):
        return None
    return ProductRef(sku=sku, name=str(d["name"]), brand=str(d.get("brand", "")), pack=_pack(d))


async def _product_docs(shop_id: str) -> dict[str, dict]:
    coll = dbmod.db().collection("shops").document(shop_id).collection("products")
    return {snap.id: snap.to_dict() or {} async for snap in coll.stream()}


async def shop_products(shop_id: str) -> list[ProductRef]:
    """Every product of ``shop_id`` as a ProductRef (the candidate list for identify / point)."""
    refs = [_ref(sku, d) for sku, d in (await _product_docs(shop_id)).items()]
    return [r for r in refs if r is not None]


# --- spoken name -> product (Kanish's business.catalog.resolve) -----------------------------------------------


def _catalog_module():
    """business.catalog, imported at call time so the tool registry loads while that lane is landing."""
    try:
        return importlib.import_module(CATALOG_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name != CATALOG_MODULE:
            raise
        return None


async def _fallback_resolve(shop_id: str, spoken: str, lang: str) -> ProductRef | None:
    """Exact sku / name / alias match, used only while business.catalog is missing.

    A word shared by several packs ("milk") resolves only to the one marked ``defaultPack``; otherwise None.
    """
    q = " ".join(spoken.casefold().split())
    if not q:
        return None
    hits: list[tuple[str, dict]] = []
    for sku, d in (await _product_docs(shop_id)).items():
        aliases = d.get("aliases") or {}
        words = [sku, d.get("name", "")]
        for key in (lang, "en"):
            words += aliases.get(key, []) if isinstance(aliases, dict) else []
        if q in {" ".join(str(w).casefold().split()) for w in words}:
            hits.append((sku, d))
    if len(hits) > 1:
        hits = [h for h in hits if h[1].get("defaultPack")]
    return _ref(*hits[0]) if len(hits) == 1 else None


async def catalog_resolve(shop_id: str, spoken: str, lang: str) -> ProductRef | None:
    mod = _catalog_module()
    if mod is None:
        return await _fallback_resolve(shop_id, spoken, lang)
    return await mod.resolve(shop_id, spoken, lang)


# --- Live tools -------------------------------------------------------------------------------------------------


async def location_remember(product: str, zone: str) -> dict:
    """Save where a product is kept, as confirmed by the owner, e.g. zone "fridge2/middle/left".

    Pass the place in English words ("fridge two, middle shelf, left"); AIRA reads it back in the owner's language.
    """
    s = get_session()
    ref = await catalog_resolve(s.shop_id, product, s.lang)
    if ref is None:
        return {"say": t("products.unknown", s.lang, spoken=product), "zone": None}
    try:
        canonical = normalize_zone(zone)
    except ValueError:
        return {"say": t("loc.unclear", s.lang, name=ref.name), "zone": None}
    await spatial.remember(s.shop_id, ref.sku, canonical, "user", 1.0)
    await episodic.log(s.shop_id, "location", f"{ref.name} -> {canonical}", "product", ref.sku)
    return {"say": t("loc.saved", s.lang, name=ref.name, zone=speak_zone(canonical, s.lang)), "zone": canonical}


async def product_identify() -> dict:
    """Identify the pack in the owner's hand against the shop catalog."""
    s = get_session()
    frames = await s.request_frames("identify", count=1, timeout_s=4.0)
    if not frames:
        return {"say": t("camera.none", s.lang), "sku": None, "confidence": 0.0}
    candidates = await shop_products(s.shop_id)
    try:
        res = await identify_product(frames[-1], candidates)
    except VisionError as exc:
        log.warning("product_identify: vision failed for %s: %s", s.shop_id, exc)
        return {"say": t("live.unavailable", s.lang), "sku": None, "confidence": 0.0}
    ref = next((c for c in candidates if c.sku == res.sku), None)
    if ref is None or res.confidence < MIN_ID_CONF:
        key = "pick.unsure_label" if next_hint(res, candidates) == "vision.aim.show_label" else "pick.unsure"
        return {"say": t(key, s.lang), "sku": None, "confidence": res.confidence}
    return {"say": t("identify.ok", s.lang, name=ref.name, pack=ref.pack), "sku": ref.sku,
            "confidence": res.confidence}


async def product_find(product: str, count_needed: int = 1) -> dict:
    """Guide the owner's hand to a product: saved location first, then the camera.

    count_needed is how many packs to pick (set it for a customer sale). Sends target messages for hand guidance.
    """
    raise NotImplementedError  # sarmitha/03 Task 4 (guidance loop)
