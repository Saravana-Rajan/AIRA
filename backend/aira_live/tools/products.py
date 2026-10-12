"""Live tools: product_find, product_identify, location_remember (spec §2 steps 5–6). OWNER: Sarmitha (sarmitha/03).

Memory only remembers and guides: these tools never write stock or money. A location the owner states is saved
as user-confirmed; a camera read below 0.6 confidence is spoken as "Not sure", never as a guess.
"""
import logging

from aira_live import db as dbmod
from aira_live.business.catalog import resolve as catalog_resolve
from aira_live.i18n import t
from aira_live.memory import episodic, spatial
from aira_live.memory.zones import normalize_zone, speak_zone
from aira_live.session import get_session
from aira_live.tools import guidance
from aira_live.tools.camera import side_key
from aira_live.vision.gemini import VisionError
from aira_live.vision.identify import ProductRef, identify_product, next_hint
from aira_live.vision.listing import observe, publish_boxes
from aira_live.vision.point import point_to

log = logging.getLogger(__name__)

MIN_ID_CONF = 0.6
FREE_PREFIX = guidance.FREE_PREFIX
FREE_MIN_CONF = 0.5     # a free-text point below this is "I can't find it", never a guided guess
FREE_FRAME_TIMEOUT_S = 4.0


def free_ref(label: str) -> ProductRef:
    """A ProductRef for an item named by the owner that is not in the catalog ("water bottle")."""
    label = (label or "").strip()
    return ProductRef(sku=FREE_PREFIX + label, name=label, brand="", pack="")


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


async def shop_products(shop_id: str) -> list[ProductRef]:
    """Every named product of ``shop_id`` as a ProductRef (the candidate list for identify / point)."""
    coll = dbmod.db().collection("shops").document(shop_id).collection("products")
    out: list[ProductRef] = []
    async for snap in coll.stream():
        d = snap.to_dict() or {}
        if d.get("name") and d.get("active") is not False:      # a product disabled by voice is not a candidate
            out.append(ProductRef(sku=snap.id, name=str(d["name"]), brand=str(d.get("brand", "")), pack=_pack(d)))
    return out


# --- Live tools -------------------------------------------------------------------------------------------------


async def location_remember(product: str, zone: str) -> dict:
    """Save where a product is kept, as confirmed by the owner, e.g. zone "fridge2/middle/left".

    Pass the place in English words ("fridge two, middle shelf, left"); AIRA reads it back in the language the owner last spoke.
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


def _count(value) -> int:
    """count_needed as a whole number of packs, at least 1 (Live may send "2", 2.0 or nothing)."""
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 1


async def _find_free_text(s, label: str, needed: int) -> dict:
    """Spec §4 fallback: point at an item by its words ("water bottle", "red jam jar") and guide the hand.

    One fresh frame; ER 2 must give a confident point before any guidance starts. No point means
    "I can't find it, show me the shelf": never a tone toward a guess.
    """
    base = {"found": False, "sku": None, "free_text": True, "label": label}
    frames = await s.request_frames("ask", count=1, timeout_s=FREE_FRAME_TIMEOUT_S)
    if not frames:
        return {"say": t("camera.none", s.lang), **base, "confidence": 0.0}
    try:
        res = await point_to(frames[-1], label)
    except VisionError as exc:
        log.warning("product_find: free-text point failed for %r: %s", label, exc)
        return {"say": t("live.unavailable", s.lang), **base, "confidence": 0.0}
    if res is None or res.confidence < FREE_MIN_CONF:
        return {"say": t("find.free_none", s.lang), **base, "confidence": res.confidence if res else 0.0}
    seen = [{"label": label, "point": list(res.point), "box": list(res.box) if res.box else None,
             "confidence": res.confidence}]
    await observe(s, seen, "product_find")
    await publish_boxes(s, seen, "product_find", target=label, found=True, frame_id=frames[-1].id)
    ref = free_ref(label)
    await guidance.start(s, ref, needed, [])
    await episodic.log(s.shop_id, "find", f"Finding {label}", "free_text", label)
    side = t(side_key(res.point[1]), s.lang)
    say = f"{t('find.free_start', s.lang, item=label, side=side)} {t('guide.start', s.lang)}"
    return {"say": say, **base, "found": True, "point": list(res.point), "confidence": res.confidence}


async def product_find(product: str, count_needed: int = 1) -> dict:
    """Guide the owner's hand to a product or any named item: saved location first, then the camera.

    A catalog product uses its saved place. Anything else the owner names ("water bottle", "red jam jar",
    "my keys") is found by the camera from the words alone, with the same beeps.
    count_needed is how many packs to pick (set it for a customer sale). Sends target messages for hand guidance.
    """
    s = get_session()
    spoken = (product or "").strip()
    if not spoken:
        return {"say": t("products.unknown", s.lang, spoken=spoken), "found": False}
    ref = await catalog_resolve(s.shop_id, spoken, s.lang)
    if ref is None:  # not a catalog product: find it by its words (spec §4)
        return await _find_free_text(s, spoken, _count(count_needed))
    now = spatial._utcnow()
    loc = await spatial.where(s.shop_id, ref.sku)
    stale = loc is None or spatial.is_stale(loc, now)
    candidates = await shop_products(s.shop_id)
    if not any(c.sku == ref.sku for c in candidates):
        candidates.append(ref)
    await guidance.start(s, ref, _count(count_needed), candidates)
    await episodic.log(s.shop_id, "find", f"Finding {guidance._item(ref)}", "product", ref.sku)
    say = f"{spatial.describe_location(ref.name, loc, s.lang, now)} {t('guide.start', s.lang)}"
    return {"say": say, "found": True, "sku": ref.sku, "stale": stale}
