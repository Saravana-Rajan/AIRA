"""Supplier ordering. OWNER: Kanish (kanish/02).

The model proposes, the backend commits: ``order_create`` only saves a DRAFT and reads the order back through
``confirm.propose``. The committers ``order.send`` and ``order.mark_sent`` run after the owner's spoken "yes":
- ``order.send`` sends the order (Telegram / WhatsApp via ``messaging.send_order``) and moves it to ORDER_PLACED.
  When no message could go out, the phone opens a click-to-chat or ``tel:`` link (``OPEN_URL``), the order stays
  DRAFT, and AIRA asks whether the owner sent it.
- ``order.mark_sent`` places the order once the owner says he sent it himself (channel ``manual``).
Quantities are stored in each product's selling unit (``catalog.to_selling_units``). A retried tool call reuses
its draft (the doc id comes from ``ids.current_idem_key()``), and a placed order is never sent twice.
"""
import aira_live.messaging as messaging
from aira_live import confirm, ids
from aira_live.business import audit, catalog, hints, outbox, paths
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso
from aira_live.business.states import transition
from aira_live.business.store import get_store
from aira_live.contracts import StateMsg
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty, items_text
from aira_live.session import Session, get_session
from aira_live.tools import _repo

# Orders worth reporting when the owner asks "what about my order?" (a DRAFT was never sent; CLOSED is done).
ACTIVE_STATES = ["ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED", "DISCREPANCY",
                 "PAYMENT_PENDING", "PAYMENT_CONFIRMED"]
_MEASURES = {"L", "ml", "kg", "g"}
_UNIT_WORDS = {
    "l": "L", "ltr": "L", "ltrs": "L", "litre": "L", "litres": "L", "liter": "L", "liters": "L",
    "ml": "ml", "mls": "ml", "millilitre": "ml", "millilitres": "ml", "milliliter": "ml", "milliliters": "ml",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "pack": "pack", "packs": "pack", "packet": "pack", "packets": "pack", "pouch": "pack", "pouches": "pack",
    "jar": "jar", "jars": "jar", "box": "box", "boxes": "box",
}


def _canon_unit(unit) -> str:
    """A spoken unit word → L, ml, kg, g, pack, jar or box; any other word passes through unchanged."""
    u = str(unit or "").strip()
    return _UNIT_WORDS.get(u.lower().rstrip("."), u)


async def product_doc(shop_id: str, sku: str) -> dict:
    return {**(await get_store().get(paths.product(shop_id, sku)) or {}), "sku": sku}


def _packs(product: dict, qty: float, unit: str) -> float:
    size_l = product.get("packSizeL") if unit == "L" else None
    return qty / size_l if size_l else qty


def qty_phrase(product: dict, qty: float, unit: str, spoken_qty, spoken_unit: str, lang: str) -> str:
    """The read-back of one line in its selling unit, plus the pack form when the owner said something else.

    '30 L Sakthi Curd 1 L', '25 pack Aavin Paneer 200 g (25 × 200 g pack = 5 kg)',
    '2 L Aavin Milk 500 ml (4 × 500 ml pouch = 2 L)'. Every number comes from the converted quantity.
    """
    base = f"{fmt_qty(qty)} {unit} {product['name']}"
    spoken = _canon_unit(spoken_unit)
    if spoken == unit:
        return base
    if spoken in _MEASURES:                    # "5 kg" of paneer: say the packs and what the owner said
        other = f"{fmt_qty(spoken_qty)} {spoken}"
    elif unit in _MEASURES:                    # "4 packets" of milk: say the packs and the litres
        other = f"{fmt_qty(qty)} {unit}"
    else:                                      # packs of a product sold by the pack, jar or box
        return base
    both = t("k2.qty.both", lang, qty=fmt_qty(round(_packs(product, qty, unit), 6)),
             pack=product.get("pack") or unit, spoken=other)
    return f"{base} ({both})"


async def resolve_lines(shop_id: str, items: list[dict], lang: str,
                        supplier: dict | None = None) -> tuple[list[dict], list[str], str | None]:
    """Spoken items → order lines in selling units. Returns (lines, read-back phrases, error_say).

    Nothing is guessed: an unknown product, a product this supplier does not sell, or a quantity that is not a
    whole number of packs returns a sentence asking again instead of a line.
    """
    lines, phrases = [], []
    for it in items:
        it = it if isinstance(it, dict) else {"product": str(it)}
        spoken = str(it.get("product") or it.get("name") or "").strip()
        ref = await catalog.resolve(shop_id, spoken, lang)
        if ref is None:
            return [], [], hints.say("PRODUCT_UNKNOWN", lang, product=spoken)
        product = await product_doc(shop_id, ref.sku)
        name = product.get("name") or ref.sku
        sells = (supplier or {}).get("products") or []
        if sells and ref.sku not in sells:
            return [], [], t("k2.order.wrong_supplier", lang, supplier=supplier["name"], product=name)
        spoken_qty = it.get("qty")
        spoken_unit = _canon_unit(it.get("unit") or product.get("unit") or "pack")
        try:
            qty, unit = catalog.to_selling_units(product, spoken_qty, spoken_unit)
        except (TypeError, ValueError):
            return [], [], t("k2.order.qty_unclear", lang, product=name)
        if qty <= 0:
            return [], [], t("k2.order.qty_unclear", lang, product=name)
        lines.append({"sku": ref.sku, "name": name, "qty": float(qty), "unit": unit,
                      "agreedPrice": int(product["costPrice"])})
        phrases.append(qty_phrase(product, qty, unit, spoken_qty, spoken_unit, lang))
    return lines, phrases, None


async def order_create(supplier: str, items: list[dict]) -> dict:
    """Order stock from a supplier when the OWNER asks, e.g. "order 30 litres Sakthi curd from the Sakthi vendor".

    supplier: the vendor as spoken ("Sakthi vendor", "milkman", "ice cream distributor").
    items: [{"product": "sakthi curd", "qty": 30, "unit": "L"}]; unit is one of L, ml, kg, g, pack, jar, box
    (optional: the product's own unit). Nothing is sent yet: this reads the order back and waits for the owner's
    yes (confirm_action), then sends it to the vendor.
    """
    s = get_session()
    sup = await catalog.resolve_supplier(s.shop_id, supplier, s.lang)
    if sup is None:
        return {"ok": False, "say": hints.say("SUPPLIER_UNKNOWN", s.lang, supplier=supplier)}
    if not items:
        return {"ok": False, "say": t("k2.order.no_items", s.lang)}
    lines, phrases, err = await resolve_lines(s.shop_id, items, s.lang, sup)
    if err:
        return {"ok": False, "say": err}
    idem = ids.current_idem_key()
    oid = doc_id_for("o", idem)
    if await _repo.get_order(s.shop_id, oid) is None:          # a retried tool call reuses the same draft
        now = now_iso()
        await _repo.save_order(s.shop_id, {
            "id": oid, "supplierId": sup["id"], "supplierName": sup["name"], "channel": sup.get("channel", "whatsapp"),
            "items": lines, "state": "DRAFT", "lang": s.lang, "idemKey": idem, "createdAt": now, "updatedAt": now})
    prompt = t("k2.order.read_back", s.lang, items=", ".join(phrases), supplier=sup["name"])
    return await confirm.propose("order", prompt, {"order_id": oid, "idem": idem}, "order.send")


async def _supplier(shop_id: str, supplier_id: str) -> dict:
    return {**(await get_store().get(paths.supplier(shop_id, supplier_id)) or {}), "id": supplier_id}


async def _place(s: Session, order: dict, channel: str, message_id: str | None = None) -> dict:
    now = now_iso()
    order = {**order, "state": transition("order", order["state"], "ORDER_PLACED"), "channel": channel,
             "channelMsgId": message_id, "placedAt": now, "updatedAt": now}
    await _repo.save_order(s.shop_id, order)
    await outbox.order_state(s.shop_id, order["id"], "ORDER_PLACED")
    await audit.log(s.shop_id, "owner", "order_placed", "order", order["id"], channel)
    await s.send(StateMsg(entity="order", id=order["id"], state="ORDER_PLACED", summary=items_text(order["items"])))
    return {"ok": True, "orderId": order["id"], "state": "ORDER_PLACED",
            "say": t("k2.order.sent", s.lang, supplier=order["supplierName"])}


async def _commit_send(payload: dict, value: str | None) -> dict:
    """Committer "order.send": runs only after the owner's yes. Sends once; a retry says it was already sent."""
    s = get_session()
    order = await _repo.get_order(s.shop_id, payload["order_id"])
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    if order["state"] != "DRAFT":
        return {"ok": True, "orderId": order["id"], "say": t("k2.order.already", s.lang)}
    res = await messaging.send_order(s.shop_id, await _supplier(s.shop_id, order["supplierId"]), order)
    if res.get("messageId"):
        return await _place(s, order, res["channel"], res["messageId"])
    # No message went out: the phone opens the click-to-chat (or tel:) link and the order stays DRAFT until the
    # owner says he sent it. An order is not placed on a link alone.
    await s.send(StateMsg(entity="order", id=order["id"], state="DRAFT", summary=f"OPEN_URL {res['fallbackUrl']}"))
    first = t("k2.order.call" if res.get("channel") == "call" else "k2.order.fallback", s.lang,
              supplier=order["supplierName"], items=items_text(order["items"]))
    follow = await confirm.propose("generic", t("k2.order.mark_sent_q", s.lang, supplier=order["supplierName"]),
                                   {"order_id": order["id"], "idem": payload.get("idem") or order.get("idemKey")},
                                   "order.mark_sent")
    return {**follow, "ok": True, "orderId": order["id"], "fallbackUrl": res["fallbackUrl"],
            "say": f"{first} {follow['say']}"}


async def _commit_mark_sent(payload: dict, value: str | None) -> dict:
    """Committer "order.mark_sent": the owner confirmed the click-to-chat message (or the call) went out."""
    s = get_session()
    order = await _repo.get_order(s.shop_id, payload["order_id"])
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    if order["state"] != "DRAFT":
        return {"ok": True, "orderId": order["id"], "say": t("k2.order.already", s.lang)}
    return await _place(s, order, "manual")


def _state_words(state: str, lang: str) -> str:
    key = f"k2.order_state.{state}"
    words = t(key, lang)
    return state.replace("_", " ").lower() if words == key else words


async def order_status(order_id: str | None = None) -> dict:
    """Say where the latest open supplier order (or the given order_id) stands, with the vendor's reply if any."""
    s = get_session()
    order = (await _repo.get_order(s.shop_id, order_id) if order_id
             else await _repo.latest_order(s.shop_id, ACTIVE_STATES))
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    reply = (order.get("vendorReply") or {}).get("text")
    say = t("k2.order.status_reply" if reply else "k2.order.status", s.lang,
            supplier=order.get("supplierName") or order.get("supplierId", ""),
            state=_state_words(order["state"], s.lang), text=reply or "")
    return {"ok": True, "orderId": order["id"], "state": order["state"], "say": say}


confirm.register_committer("order.send", _commit_send)
confirm.register_committer("order.mark_sent", _commit_mark_sent)
