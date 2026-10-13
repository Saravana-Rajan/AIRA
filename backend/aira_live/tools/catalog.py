"""The catalog by voice: products, suppliers and stock that arrives without an order. OWNER: Kanish.

"Hey AIRA, add buttermilk": the model proposes, the backend commits. Every tool here reads the change back
through ``confirm.propose`` and writes only in its committer, after the owner's "yes", inside one Store
transaction with an idempotency doc (``ids.current_idem_key()``) and an audit entry. A repeated "yes" or a
retried call finds the idempotency doc and changes nothing.

Tools return FACTS (names, numbers, ids, error codes) plus a short English ``say_en`` meaning hint; Gemini Live
phrases them in whatever language the owner is speaking.

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

from aira_live import confirm, embeddings, ids
from aira_live.business import audit, catalog, hints, ledger, paths
from aira_live.business.clock import now_iso
from aira_live.business.store import Txn, get_store
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


def _duplicate_sku(sku: str, label: str) -> dict:
    return {"ok": False, "error": "duplicate_sku", "sku": sku, "product": label,
            "say_en": f"{label} is already in the shop. Say a different name or size."}


def _duplicate_supplier(name: str) -> dict:
    return {"ok": False, "error": "duplicate_supplier", "supplier": name, "say_en": f"{name} is already a vendor."}


def _price_q(name: str) -> dict:
    return {"ok": False, "missing": "price_rupees", "product": name,
            "say_en": f"What is the selling price of {name}, in rupees?"}


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
        return {"ok": False, "missing": "name", "say_en": "Which product should I add?"}
    price, u = _price(price_rupees), _unit(unit)
    if price is None or u is None:
        # "add buttermilk" with no details, for a product the shop already has: stock, or a new product?
        ref = await catalog.resolve(s.shop_id, name, s.lang)
        if ref is not None:
            return {"ok": False, "exists": ref.sku, "product": ref.name,
                    "say_en": f"{ref.name} is already in the shop. Add stock, or a new product?"}
    if price is None:
        return _price_q(name)
    if u is None:
        return {"ok": False, "missing": "unit", "product": name, "units": list(UNITS),
                "say_en": f"Is {name} sold by the litre, pack, jar or box?"}
    sku = slug(brand, name, size)
    label = _label(_title(brand), _title(name), size)
    if await get_store().get(paths.product(s.shop_id, sku)) is not None:
        return _duplicate_sku(sku, label)
    sup_id = _clean(supplier_id)
    if sup_id:
        sup = await catalog.resolve_supplier(s.shop_id, sup_id, s.lang)
        if sup is None:
            return hints.fact("SUPPLIER_UNKNOWN", supplier=sup_id)
        sup_id = sup["id"]
    doc = {"sku": sku, "name": label, "brand": _title(brand), "unit": u, "pack": size or u,
           **_pack_size(size), "defaultPack": True, "price": price, "costPrice": price,
           "reorderThreshold": DEFAULT_REORDER, "shelfLifeDays": DEFAULT_SHELF_DAYS,
           "aliases": _aliases(name, brand, label), "active": True}
    prompt = f"Add {label} at {price} rupees per {u}?"
    return await confirm.propose("generic", prompt, {"sku": sku, "doc": doc, "supplier_id": sup_id or None,
                                                     "idem": ids.current_idem_key()}, "catalog.product_add",
                                 facts={"sku": sku, "product": label, "price": price, "unit": u,
                                        "supplier": sup_id or None})


async def _commit_product_add(payload: dict, value: str | None) -> dict:
    s = get_session()
    sku, doc, sup_id, idem = payload["sku"], payload["doc"], payload.get("supplier_id"), payload["idem"]
    label = doc.get("name", sku)
    vector = await embeddings.product_vector(doc)      # before the transaction: never a network call inside one
    if vector is not None:
        doc = {**doc, embeddings.FIELD: vector}

    async def fn(txn: Txn) -> dict:
        seen = await txn.get(paths.idem(idem))
        if seen is not None:
            return seen["result"]
        if await txn.get(paths.product(s.shop_id, sku)) is not None:
            return _duplicate_sku(sku, label)
        sup = await txn.get(paths.supplier(s.shop_id, sup_id)) if sup_id else None
        now = now_iso()
        txn.set(paths.product(s.shop_id, sku), {**doc, "createdAt": now, "updatedAt": now})
        if sup is not None and sku not in (sup.get("products") or []):
            txn.set(paths.supplier(s.shop_id, sup_id), {**sup, "products": [*(sup.get("products") or []), sku]})
        audit.log_in_txn(txn, s.shop_id, "owner", "product_added", "product", sku, "added")
        result = {"ok": True, "sku": sku, "product": label, "say_en": f"{label} added."}
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
        return await catalog.did_you_mean(s.shop_id, product)
    changes: dict = {}
    words: list[str] = []
    price = _price(price_rupees)
    if price_rupees is not None and price is None:
        return _price_q(ref.name)
    if price is not None:
        changes["price"] = price
        words.append(f"price {price} rupees")
    new_name = _clean(name)
    if new_name:
        changes["name"] = _title(new_name)
        words.append(f"name {changes['name']}")
    sup_id, supplier_name = _clean(supplier_id), None
    if sup_id:
        sup = await catalog.resolve_supplier(s.shop_id, sup_id, s.lang)
        if sup is None:
            return hints.fact("SUPPLIER_UNKNOWN", supplier=sup_id)
        sup_id, supplier_name = sup["id"], sup["name"]
        words.append(f"vendor {supplier_name}")
    if not changes and not sup_id:
        return {"ok": False, "error": "nothing_to_change", "product": ref.name,
                "say_en": f"Nothing to change for {ref.name}."}
    prompt = f"Change {ref.name}: {', '.join(words)}?"
    return await confirm.propose("generic", prompt, {"sku": ref.sku, "changes": changes,
                                                     "supplier_id": sup_id or None, "idem": ids.current_idem_key()},
                                 "catalog.product_update",
                                 facts={"sku": ref.sku, "product": ref.name, "changes": changes,
                                        "supplier": supplier_name})


async def _commit_product_update(payload: dict, value: str | None) -> dict:
    s = get_session()
    sku, changes, sup_id, idem = payload["sku"], payload.get("changes") or {}, payload.get("supplier_id"), payload["idem"]
    vector = None
    if "name" in changes:                               # the words changed: re-embed outside the transaction
        current = await get_store().get(paths.product(s.shop_id, sku)) or {}
        vector = await embeddings.product_vector({**current, **changes})

    async def fn(txn: Txn) -> dict:
        seen = await txn.get(paths.idem(idem))
        if seen is not None:
            return seen["result"]
        doc = await txn.get(paths.product(s.shop_id, sku))
        if doc is None:
            return hints.fact("PRODUCT_UNKNOWN", product=sku)
        sup = await txn.get(paths.supplier(s.shop_id, sup_id)) if sup_id else None
        now = now_iso()
        new_doc = {**doc, **changes, "updatedAt": now}
        if vector is not None:
            new_doc[embeddings.FIELD] = vector
        old_price = doc.get("price")
        if "price" in changes and changes["price"] != old_price:
            # The price board's history: server-only, one entry per committed change (same idem → same doc).
            new_doc["priceUpdatedAt"] = now
            txn.set(paths.price_history(s.shop_id, f"{sku}-{hashlib.sha1(idem.encode('utf-8')).hexdigest()[:12]}"),
                    {"sku": sku, "old": old_price, "new": changes["price"], "at": now, "actor": "owner"})
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
        label = new_doc.get("name", sku)
        result = {"ok": True, "sku": sku, "product": label, "changes": changes, "say_en": f"{label} updated."}
        txn.set(paths.idem(idem), {"key": idem, "result": result, "at": now})
        return result

    return await get_store().run(fn)


# ---------------------------------------------------------------------------------------------------- product_disable

async def product_disable(product: str) -> dict:
    """Take a product out of the shop ("we don't sell ice cream any more"). Its history is kept. Waits for the owner's yes."""
    s = get_session()
    ref = await catalog.resolve(s.shop_id, product, s.lang)
    if ref is None:
        return await catalog.did_you_mean(s.shop_id, product)
    return await confirm.propose("generic", f"Remove {ref.name} from the shop?",
                                 {"sku": ref.sku, "idem": ids.current_idem_key()}, "catalog.product_disable",
                                 facts={"sku": ref.sku, "product": ref.name})


async def _commit_product_disable(payload: dict, value: str | None) -> dict:
    s = get_session()
    sku, idem = payload["sku"], payload["idem"]

    async def fn(txn: Txn) -> dict:
        seen = await txn.get(paths.idem(idem))
        if seen is not None:
            return seen["result"]
        doc = await txn.get(paths.product(s.shop_id, sku))
        if doc is None:
            return hints.fact("PRODUCT_UNKNOWN", product=sku)
        now = now_iso()
        txn.set(paths.product(s.shop_id, sku), {**doc, "active": False, "disabledAt": now, "updatedAt": now})
        audit.log_in_txn(txn, s.shop_id, "owner", "product_disabled", "product", sku, "disabled")
        label = doc.get("name", sku)
        result = {"ok": True, "sku": sku, "product": label, "say_en": f"{label} removed. Its history is kept."}
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
        return {"ok": False, "error": "PRODUCT_UNKNOWN", "unknown": product, "product": product,
                "say_en": f"I don't know {product}. Add it as a new product?"}
    doc = await product_doc(s.shop_id, ref.sku)
    spoken_unit = _canon_unit(unit or "")      # a bare count is packs, as for orders and sales
    qty_q = {"ok": False, "missing": "quantity", "product": ref.name, "say_en": f"How much {ref.name} came in?"}
    try:
        qty, sell_unit = catalog.to_selling_units(doc, quantity, spoken_unit)
    except (TypeError, ValueError):
        return qty_q
    if qty <= 0:
        return qty_q
    phrase = qty_phrase(doc, qty, sell_unit, quantity, spoken_unit).replace(f" {doc['name']}", "", 1)
    prompt = f"Add {phrase} of {doc['name']} to stock?"
    return await confirm.propose("generic", prompt, {"sku": ref.sku, "qty": qty, "unit": sell_unit,
                                                     "idem": ids.current_idem_key()}, "catalog.stock_add",
                                 facts={"sku": ref.sku, "product": doc["name"], "qty": qty, "unit": sell_unit})


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
    name, unit = doc.get("name", sku), doc.get("unit", "")
    return {"ok": True, "sku": sku, "product": name, "qty": qty, "unit": unit, "onHand": on_hand,
            "say_en": f"Added. {name}: {f'{fmt_qty(on_hand)} {unit}'.strip()} in stock."}


# ---------------------------------------------------------------------------------------------------- supplier_add

async def supplier_add(name: str, channel: Literal["telegram", "call", "whatsapp"]) -> dict:
    """Add a new vendor, e.g. "add Hatsun agent on Telegram". channel: telegram, whatsapp or call. For Telegram
    AIRA gives the onboarding link to send to the vendor. Waits for the owner's yes (confirm_action)."""
    s = get_session()
    name = _clean(name)
    if not name:
        return {"ok": False, "missing": "name", "say_en": "Which vendor should I add?"}
    ch = _clean(channel).lower()
    if ch not in CHANNELS:
        return {"ok": False, "missing": "channel", "supplier": name, "channels": list(CHANNELS),
                "say_en": f"How do you reach {name}: Telegram, WhatsApp or a call?"}
    sid = slug(name)
    if (await get_store().get(paths.supplier(s.shop_id, sid)) is not None
            or await catalog.resolve_supplier(s.shop_id, name, s.lang) is not None):
        return _duplicate_supplier(name)
    doc = {"id": sid, "name": name, "phone": "", "channel": ch, "products": [],
           "aliases": {"ta": [], "hi": [], "en": [name.lower()]}, "active": True}
    return await confirm.propose("generic", f"Add vendor {name} on {ch}?",
                                 {"supplier_id": sid, "doc": doc, "idem": ids.current_idem_key()},
                                 "catalog.supplier_add", facts={"supplierId": sid, "supplier": name, "channel": ch})


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
            return _duplicate_supplier(doc["name"])
        now = now_iso()
        txn.set(paths.supplier(s.shop_id, sid), {**doc, "createdAt": now})
        audit.log_in_txn(txn, s.shop_id, "owner", "supplier_added", "supplier", sid, doc["channel"])
        result = {"ok": True, "supplierId": sid, "supplier": doc["name"], "channel": doc["channel"],
                  "say_en": f"Vendor {doc['name']} added."}
        if doc["channel"] == "telegram":
            param, link = _telegram_link(sid)
            result["startParam"], result["link"] = param, link
            if link or param:
                result["say_en"] += f" Send this Telegram link to {doc['name']}: {link or param}"
            else:
                result["say_en"] += " The Telegram secret is not set, so the vendor link cannot be made yet."
        txn.set(paths.idem(idem), {"key": idem, "result": result, "at": now})
        return result

    return await get_store().run(fn)


confirm.register_committer("catalog.product_add", _commit_product_add)
confirm.register_committer("catalog.product_update", _commit_product_update)
confirm.register_committer("catalog.product_disable", _commit_product_disable)
confirm.register_committer("catalog.stock_add", _commit_stock_add)
confirm.register_committer("catalog.supplier_add", _commit_supplier_add)
