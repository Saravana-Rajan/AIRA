"""The catalog by voice: products, suppliers and stock that arrives without an order. OWNER: Kanish.

"Hey AIRA, add buttermilk": the model proposes, the backend commits. Every tool here reads the change back
through ``confirm.propose`` and writes only in its committer, after the owner's "yes", inside one Store
transaction with an idempotency doc (``ids.current_idem_key()``) and an audit entry. A repeated "yes" or a
retried call finds the idempotency doc and changes nothing.

- ``product_add``: asks for the price and the unit before proposing; "add buttermilk" for a product the shop
  already has asks "add as stock, or a new product?" instead of making a duplicate.
- ``product_disable``: ``active=false``. The doc, its inventory and its ledger stay; ``catalog.resolve`` and the
  stock / dashboard lists skip it.
- ``stock_add``: a ledger movement for stock that came without an order (reason ADJUST, refType "receive":
  the contracts' ledger reasons are frozen).
- ``supplier_add``: for Telegram, the reply carries the signed onboarding link (``webhooks.start_param``).
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Literal

from aira_live import confirm, ids
from aira_live.business import audit, catalog, hints, ledger, paths
from aira_live.business.clock import now_iso
from aira_live.business.store import Txn, get_store
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import get_session
from aira_live.tools.orders import _canon_unit, product_doc, qty_phrase

UNITS = ("L", "pack", "jar", "box")
CHANNELS = ("telegram", "call", "whatsapp")
DEFAULT_REORDER = 2        # a new product is "low" below this many units until the owner changes it
DEFAULT_SHELF_DAYS = 2     # dairy default; the ledger uses the same when a product has none
_TAMIL = re.compile(r"[஀-௿]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ml|l|litre|litres|liter|liters|g|gm|gms|gram|grams|kg)\s*$", re.I)
_UNIT_WORDS = {"l": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L", "ltr": "L",
               "pack": "pack", "packs": "pack", "packet": "pack", "packets": "pack", "pouch": "pack",
               "jar": "jar", "jars": "jar", "box": "box", "boxes": "box"}


# ---------------------------------------------------------------------------------------------------- helpers

def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def slug(*parts: str | None) -> str:
    """``aavin_lassi_200ml``: a Firestore-safe id from brand + name + size. Non-Latin names hash instead."""
    text = " ".join(_clean(p) for p in parts if p)
    joined = re.sub(r"(\d)\s+(ml|l|g|kg)\b", r"\1\2", text.lower())     # "200 ml" → "200ml", like the seed skus
    s = re.sub(r"[^a-z0-9]+", "_", joined).strip("_")
    if not s:
        s = "p_" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return s[:64]


def _unit(unit) -> str | None:
    u = _clean(unit)
    if u in UNITS:
        return u
    return _UNIT_WORDS.get(u.lower().rstrip("."))


def _price(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(n)) if n > 0 else None


def _pack_size(text) -> dict:
    """"200 ml" → {"packSizeL": 0.2}; "500 g" → {"packSizeG": 500}; anything else → {}."""
    m = _SIZE.match(_clean(text))
    if not m:
        return {}
    n, u = float(m.group(1)), m.group(2).lower()
    if u == "ml":
        return {"packSizeL": n / 1000}
    if u.startswith("l"):
        return {"packSizeL": n}
    if u == "kg":
        return {"packSizeG": n * 1000}
    return {"packSizeG": n}


def _label(brand: str, name: str, size: str) -> str:
    parts = [name]
    if brand and not name.lower().startswith(brand.lower()):
        parts.insert(0, brand)
    if size and size.lower() not in name.lower():
        parts.append(size)
    return " ".join(p for p in parts if p)


def _aliases(name: str, brand: str, label: str) -> dict[str, list[str]]:
    """The spoken name (and brand + name) in the language of its script, so it resolves by voice."""
    out: dict[str, list[str]] = {"ta": [], "hi": [], "en": []}
    lang = "ta" if _TAMIL.search(name) else "hi" if _DEVANAGARI.search(name) else "en"
    forms = [name.lower(), f"{brand} {name}".strip().lower(), label.lower()]
    seen: list[str] = []
    for f in forms:
        if f and f not in seen:
            seen.append(f)
    out[lang] = seen
    return out


def _title(s: str) -> str:
    return " ".join(w if not w.isascii() else w[:1].upper() + w[1:] for w in s.split())


# ---------------------------------------------------------------------------------------------------- product_add

async def product_add(name: str, brand: str | None = None, unit: str | None = None, pack_size: str | None = None,
                      price_rupees: float | None = None, supplier_id: str | None = None) -> dict:
    """Add a NEW product to the shop when the owner asks, e.g. "add buttermilk" or "add Aavin lassi, 200 ml, 20 rupees".

    name: the product as spoken. brand: optional ("Aavin"). unit: how it is sold, one of L, pack, jar, box.
    pack_size: optional, like "200 ml" or "500 g". price_rupees: selling price per unit. supplier_id: optional,
    the vendor that supplies it. If the price or unit is missing AIRA asks for it; if the product already exists
    it asks whether to add stock instead. Nothing is saved until the owner says yes (confirm_action).
    """
    s = get_session()
    name, brand, size = _clean(name), _clean(brand), _clean(pack_size)
    if not name:
        return {"ok": False, "missing": "name", "say": t("k3.product.name_q", s.lang)}
    price, u = _price(price_rupees), _unit(unit)
    if price is None or u is None:
        # "add buttermilk" with no details, for a product the shop already has: stock, or a new product?
        ref = await catalog.resolve(s.shop_id, name, s.lang)
        if ref is not None:
            return {"ok": False, "exists": ref.sku, "say": t("k3.product.exists_q", s.lang, product=ref.name)}
    if price is None:
        return {"ok": False, "missing": "price_rupees", "say": t("k3.product.price_q", s.lang, product=name)}
    if u is None:
        return {"ok": False, "missing": "unit", "say": t("k3.product.unit_q", s.lang, product=name)}
    sku = slug(brand, name, size)
    label = _label(_title(brand), _title(name), size)
    if await get_store().get(paths.product(s.shop_id, sku)) is not None:
        return {"ok": False, "error": "duplicate_sku", "sku": sku,
                "say": t("k3.product.duplicate", s.lang, product=label)}
    sup_id = _clean(supplier_id)
    if sup_id:
        sup = await catalog.resolve_supplier(s.shop_id, sup_id, s.lang)
        if sup is None:
            return {"ok": False, "say": hints.say("SUPPLIER_UNKNOWN", s.lang, supplier=sup_id)}
        sup_id = sup["id"]
    doc = {"sku": sku, "name": label, "brand": _title(brand), "unit": u, "pack": size or u,
           **_pack_size(size), "defaultPack": True, "price": price, "costPrice": price,
           "reorderThreshold": DEFAULT_REORDER, "shelfLifeDays": DEFAULT_SHELF_DAYS,
           "aliases": _aliases(name, brand, label), "active": True}
    prompt = t("k3.product.add_q", s.lang, product=label, price=price, unit=u)
    return await confirm.propose("generic", prompt, {"sku": sku, "doc": doc, "supplier_id": sup_id or None,
                                                     "idem": ids.current_idem_key()}, "catalog.product_add")


async def _commit_product_add(payload: dict, value: str | None) -> dict:
    s = get_session()
    sku, doc, sup_id, idem = payload["sku"], payload["doc"], payload.get("supplier_id"), payload["idem"]
    label = doc.get("name", sku)

    async def fn(txn: Txn) -> dict:
        seen = await txn.get(paths.idem(idem))
        if seen is not None:
            return seen["result"]
        if await txn.get(paths.product(s.shop_id, sku)) is not None:
            return {"ok": False, "error": "duplicate_sku", "sku": sku,
                    "say": t("k3.product.duplicate", s.lang, product=label)}
        sup = await txn.get(paths.supplier(s.shop_id, sup_id)) if sup_id else None
        now = now_iso()
        txn.set(paths.product(s.shop_id, sku), {**doc, "createdAt": now, "updatedAt": now})
        if sup is not None and sku not in (sup.get("products") or []):
            txn.set(paths.supplier(s.shop_id, sup_id), {**sup, "products": [*(sup.get("products") or []), sku]})
        audit.log_in_txn(txn, s.shop_id, "owner", "product_added", "product", sku, "added")
        result = {"ok": True, "sku": sku, "say": t("k3.product.added", s.lang, product=label)}
        txn.set(paths.idem(idem), {"key": idem, "result": result, "at": now})
        return result

    return await get_store().run(fn)


# ---------------------------------------------------------------------------------------------------- product_update

async def product_update(product: str, price_rupees: float | None = None, name: str | None = None,
                         supplier_id: str | None = None) -> dict:
    """Change a product's selling price, name or vendor, e.g. "curd is now 65 rupees". Waits for the owner's yes."""
    s = get_session()
    ref = await catalog.resolve(s.shop_id, product, s.lang)
    if ref is None:
        return {"ok": False, "say": hints.say("PRODUCT_UNKNOWN", s.lang, product=product)}
    changes: dict = {}
    words: list[str] = []
    price = _price(price_rupees)
    if price_rupees is not None and price is None:
        return {"ok": False, "missing": "price_rupees", "say": t("k3.product.price_q", s.lang, product=ref.name)}
    if price is not None:
        changes["price"] = price
        words.append(t("k3.change.price", s.lang, price=price))
    new_name = _clean(name)
    if new_name:
        changes["name"] = _title(new_name)
        words.append(t("k3.change.name", s.lang, name=changes["name"]))
    sup_id = _clean(supplier_id)
    if sup_id:
        sup = await catalog.resolve_supplier(s.shop_id, sup_id, s.lang)
        if sup is None:
            return {"ok": False, "say": hints.say("SUPPLIER_UNKNOWN", s.lang, supplier=sup_id)}
        sup_id = sup["id"]
        words.append(t("k3.change.supplier", s.lang, supplier=sup["name"]))
    if not changes and not sup_id:
        return {"ok": False, "say": t("k3.product.nothing", s.lang, product=ref.name)}
    prompt = t("k3.product.update_q", s.lang, product=ref.name, changes=", ".join(words))
    return await confirm.propose("generic", prompt, {"sku": ref.sku, "changes": changes,
                                                     "supplier_id": sup_id or None, "idem": ids.current_idem_key()},
                                 "catalog.product_update")


async def _commit_product_update(payload: dict, value: str | None) -> dict:
    s = get_session()
    sku, changes, sup_id, idem = payload["sku"], payload.get("changes") or {}, payload.get("supplier_id"), payload["idem"]

    async def fn(txn: Txn) -> dict:
        seen = await txn.get(paths.idem(idem))
        if seen is not None:
            return seen["result"]
        doc = await txn.get(paths.product(s.shop_id, sku))
        if doc is None:
            return {"ok": False, "say": hints.say("PRODUCT_UNKNOWN", s.lang, product=sku)}
        sup = await txn.get(paths.supplier(s.shop_id, sup_id)) if sup_id else None
        now = now_iso()
        new_doc = {**doc, **changes, "updatedAt": now}
        if "name" in changes:
            en = [a for a in (doc.get("aliases") or {}).get("en", [])]
            if changes["name"].lower() not in en:
                en.append(changes["name"].lower())
            new_doc["aliases"] = {**(doc.get("aliases") or {}), "en": en}
        txn.set(paths.product(s.shop_id, sku), new_doc)
        if sup is not None and sku not in (sup.get("products") or []):
            txn.set(paths.supplier(s.shop_id, sup_id), {**sup, "products": [*(sup.get("products") or []), sku]})
        audit.log_in_txn(txn, s.shop_id, "owner", "product_updated", "product", sku,
                         ",".join(sorted([*changes, *(["supplier"] if sup_id else [])])))
        result = {"ok": True, "sku": sku, "say": t("k3.product.updated", s.lang, product=new_doc.get("name", sku))}
        txn.set(paths.idem(idem), {"key": idem, "result": result, "at": now})
        return result

    return await get_store().run(fn)


# ---------------------------------------------------------------------------------------------------- product_disable

async def product_disable(product: str) -> dict:
    """Take a product out of the shop ("we don't sell ice cream any more"). Its history is kept. Waits for the owner's yes."""
    s = get_session()
    ref = await catalog.resolve(s.shop_id, product, s.lang)
    if ref is None:
        return {"ok": False, "say": hints.say("PRODUCT_UNKNOWN", s.lang, product=product)}
    return await confirm.propose("generic", t("k3.product.disable_q", s.lang, product=ref.name),
                                 {"sku": ref.sku, "idem": ids.current_idem_key()}, "catalog.product_disable")


async def _commit_product_disable(payload: dict, value: str | None) -> dict:
    s = get_session()
    sku, idem = payload["sku"], payload["idem"]

    async def fn(txn: Txn) -> dict:
        seen = await txn.get(paths.idem(idem))
        if seen is not None:
            return seen["result"]
        doc = await txn.get(paths.product(s.shop_id, sku))
        if doc is None:
            return {"ok": False, "say": hints.say("PRODUCT_UNKNOWN", s.lang, product=sku)}
        now = now_iso()
        txn.set(paths.product(s.shop_id, sku), {**doc, "active": False, "disabledAt": now, "updatedAt": now})
        audit.log_in_txn(txn, s.shop_id, "owner", "product_disabled", "product", sku, "disabled")
        result = {"ok": True, "sku": sku, "say": t("k3.product.disabled", s.lang, product=doc.get("name", sku))}
        txn.set(paths.idem(idem), {"key": idem, "result": result, "at": now})
        return result

    return await get_store().run(fn)


# ---------------------------------------------------------------------------------------------------- stock_add

async def stock_add(product: str, quantity: float, unit: str | None = None) -> dict:
    """Record stock that arrived WITHOUT a supplier order (bought at the market, a gift, a correction), e.g.
    "add 10 litres of curd to stock". unit: L, ml, kg, g, pack, jar or box (optional: the product's own unit).
    Deliveries of an order go through delivery_start instead. Waits for the owner's yes (confirm_action)."""
    s = get_session()
    ref = await catalog.resolve(s.shop_id, product, s.lang)
    if ref is None:
        return {"ok": False, "unknown": product, "say": t("k3.stock.unknown_q", s.lang, product=product)}
    doc = await product_doc(s.shop_id, ref.sku)
    spoken_unit = _canon_unit(unit or doc.get("unit") or "pack")
    try:
        qty, sell_unit = catalog.to_selling_units(doc, quantity, spoken_unit)
    except (TypeError, ValueError):
        return {"ok": False, "say": t("k3.stock.qty_q", s.lang, product=ref.name)}
    if qty <= 0:
        return {"ok": False, "say": t("k3.stock.qty_q", s.lang, product=ref.name)}
    phrase = qty_phrase(doc, qty, sell_unit, quantity, spoken_unit, s.lang)
    prompt = t("k3.stock.add_q", s.lang, qty=phrase.replace(f" {doc['name']}", "", 1), product=doc["name"])
    return await confirm.propose("generic", prompt, {"sku": ref.sku, "qty": qty, "unit": sell_unit,
                                                     "idem": ids.current_idem_key()}, "catalog.stock_add")


async def _commit_stock_add(payload: dict, value: str | None) -> dict:
    s = get_session()
    sku, qty, idem = payload["sku"], float(payload["qty"]), payload["idem"]

    async def fn(txn: Txn) -> tuple[dict, dict]:
        prep = await ledger.prepare_movement(txn, s.shop_id, sku, qty, "ADJUST", "receive", idem, idem,
                                             actor="owner")
        inv = ledger.commit_movement(txn, prep)
        if not prep["done"]:
            audit.log_in_txn(txn, s.shop_id, "owner", "stock_received", "product", sku, f"+{qty:g}")
        return prep, inv

    prep, inv = await get_store().run(fn)
    await ledger.publish_movement(prep)
    doc = await product_doc(s.shop_id, sku)
    on_hand = float(inv.get("onHand") or 0)
    return {"ok": True, "sku": sku, "qty": qty, "onHand": on_hand,
            "say": t("k3.stock.added", s.lang, product=doc.get("name", sku),
                     qty=f"{fmt_qty(on_hand)} {doc.get('unit', '')}".strip())}


# ---------------------------------------------------------------------------------------------------- supplier_add

async def supplier_add(name: str, channel: Literal["telegram", "call", "whatsapp"]) -> dict:
    """Add a new vendor, e.g. "add Hatsun agent on Telegram". channel: telegram, whatsapp or call. For Telegram
    AIRA gives the onboarding link to send to the vendor. Waits for the owner's yes (confirm_action)."""
    s = get_session()
    name = _clean(name)
    if not name:
        return {"ok": False, "missing": "name", "say": t("k3.supplier.name_q", s.lang)}
    ch = _clean(channel).lower()
    if ch not in CHANNELS:
        return {"ok": False, "missing": "channel", "say": t("k3.supplier.channel_q", s.lang, supplier=name)}
    sid = slug(name)
    if (await get_store().get(paths.supplier(s.shop_id, sid)) is not None
            or await catalog.resolve_supplier(s.shop_id, name, s.lang) is not None):
        return {"ok": False, "error": "duplicate_supplier", "say": t("k3.supplier.duplicate", s.lang, supplier=name)}
    doc = {"id": sid, "name": name, "phone": "", "channel": ch, "products": [],
           "aliases": {"ta": [], "hi": [], "en": [name.lower()]}, "active": True}
    return await confirm.propose("generic", t("k3.supplier.add_q", s.lang, supplier=name, channel=ch),
                                 {"supplier_id": sid, "doc": doc, "idem": ids.current_idem_key()},
                                 "catalog.supplier_add")


def _telegram_link(supplier_id: str) -> tuple[str | None, str | None]:
    from aira_live.messaging.webhooks import start_param
    try:
        param = start_param(supplier_id)
    except RuntimeError:
        return None, None
    bot = os.getenv("TELEGRAM_BOT_USERNAME", "").lstrip("@")
    return param, (f"https://t.me/{bot}?start={param}" if bot else None)


async def _commit_supplier_add(payload: dict, value: str | None) -> dict:
    s = get_session()
    sid, doc, idem = payload["supplier_id"], payload["doc"], payload["idem"]

    async def fn(txn: Txn) -> dict:
        seen = await txn.get(paths.idem(idem))
        if seen is not None:
            return seen["result"]
        if await txn.get(paths.supplier(s.shop_id, sid)) is not None:
            return {"ok": False, "error": "duplicate_supplier",
                    "say": t("k3.supplier.duplicate", s.lang, supplier=doc["name"])}
        now = now_iso()
        txn.set(paths.supplier(s.shop_id, sid), {**doc, "createdAt": now})
        audit.log_in_txn(txn, s.shop_id, "owner", "supplier_added", "supplier", sid, doc["channel"])
        result = {"ok": True, "supplierId": sid, "say": t("k3.supplier.added", s.lang, supplier=doc["name"])}
        if doc["channel"] == "telegram":
            param, link = _telegram_link(sid)
            result["startParam"], result["link"] = param, link
            if link or param:
                result["say"] += " " + t("k3.supplier.telegram_link", s.lang, supplier=doc["name"], link=link or param)
            else:
                result["say"] += " " + t("k3.supplier.no_link", s.lang)
        txn.set(paths.idem(idem), {"key": idem, "result": result, "at": now})
        return result

    return await get_store().run(fn)


confirm.register_committer("catalog.product_add", _commit_product_add)
confirm.register_committer("catalog.product_update", _commit_product_update)
confirm.register_committer("catalog.product_disable", _commit_product_disable)
confirm.register_committer("catalog.stock_add", _commit_stock_add)
confirm.register_committer("catalog.supplier_add", _commit_supplier_add)
