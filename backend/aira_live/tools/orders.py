"""Supplier ordering. OWNER: Kanish (kanish/02).

The model proposes, the backend commits: ``order_create`` only saves a DRAFT and reads the order back through
``confirm.propose``. The committers ``order.send`` and ``order.mark_sent`` run after the owner's spoken "yes":
- ``order.send`` sends the order (Telegram / WhatsApp via ``messaging.send_order``) and moves it to ORDER_PLACED.
  When no message could go out, the phone opens a click-to-chat or ``tel:`` link (``OPEN_URL``), the order stays
  DRAFT, and AIRA asks whether the owner sent it.
- ``order.mark_sent`` places the order once the owner says he sent it himself (channel ``manual``).
- A judge's order (a session from the public judge page) goes to a demo vendor instead: it is placed on channel
  ``demo``, no real vendor is messaged, and a few seconds later the demo vendor's reply is spoken to that judge
  only. The demo reply is never stored as a vendor reply, because it is not evidence of what a vendor said.
Quantities are stored in each product's selling unit (``catalog.to_selling_units``). A retried tool call reuses
its draft (the doc id comes from ``ids.current_idem_key()``), and a placed order is never sent twice.
Tools return facts (names, quantities, amounts, error codes) plus a short English ``say_en`` meaning hint;
Gemini Live phrases them in whatever language the owner is speaking.
"""
import asyncio
import logging

import aira_live.messaging as messaging
from aira_live import confirm, ids
from aira_live.business import audit, catalog, delivery_time, hints, outbox, paths
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso
from aira_live.business.states import transition
from aira_live.business.store import get_store
from aira_live.contracts import StateMsg
from aira_live.messaging.clicktochat import fmt_qty, items_text
from aira_live.session import Session, get_session
from aira_live.tools import _repo, _speak
from aira_live.tools._speak import is_judge

log = logging.getLogger(__name__)

# Judges (tools/_speak.is_judge) order from a demo vendor; the shop's own phone keeps ordering from real vendors.
DEMO_REPLY_DELAY_S = 4.0
_DEMO_REPLIES: set[asyncio.Task] = set()   # holds each pending demo reply until it finishes

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
# English meaning of each order state (the ``state`` code itself is the fact).
_STATE_WORDS = {
    "DRAFT": "not sent yet", "ORDER_PLACED": "order placed", "DELIVERY_PENDING": "delivery pending",
    "DELIVERY_RECONCILED": "delivery checked", "DISCREPANCY": "delivery does not match",
    "PAYMENT_PENDING": "payment pending", "PAYMENT_CONFIRMED": "paid", "CLOSED": "closed",
}
ORDER_NONE = {"ok": False, "error": "ORDER_NONE", "say_en": "No open order found."}


def _canon_unit(unit) -> str:
    """A spoken unit word → L, ml, kg, g, pack, jar or box; any other word passes through unchanged."""
    u = str(unit or "").strip()
    return _UNIT_WORDS.get(u.lower().rstrip("."), u)


async def product_doc(shop_id: str, sku: str) -> dict:
    return {**(await get_store().get(paths.product(shop_id, sku)) or {}), "sku": sku}


def _packs(product: dict, qty: float, unit: str) -> float:
    size_l = product.get("packSizeL") if unit == "L" else None
    return qty / size_l if size_l else qty


def qty_phrase(product: dict, qty: float, unit: str, spoken_qty, spoken_unit: str, lang: str | None = None) -> str:
    """The English read-back of one line in its selling unit, plus the pack form when the owner said something else.

    '30 L Sakthi Curd 1 L', '25 pack Aavin Paneer 200 g (25 × 200 g pack = 5 kg)',
    '2 L Aavin Milk 500 ml (4 × 500 ml pouch = 2 L)'. Every number comes from the converted quantity.
    ``lang`` is accepted for compatibility and ignored.
    """
    base = f"{fmt_qty(qty)} {unit} {product['name']}"
    spoken = _canon_unit(spoken_unit)
    if spoken == unit:
        return base
    if spoken not in _MEASURES and abs(_packs(product, qty, unit) - qty) < 1e-9:
        return base                            # 12 packs of a 1 L product are 12 L: nothing more to say
    if spoken in _MEASURES:                    # "5 kg" of paneer: say the packs and what the owner said
        other = f"{fmt_qty(spoken_qty)} {spoken}"
    elif unit in _MEASURES:                    # "4 packets" of milk: say the packs and the litres
        other = f"{fmt_qty(qty)} {unit}"
    else:                                      # packs of a product sold by the pack, jar or box
        return base
    packs = fmt_qty(round(_packs(product, qty, unit), 6))
    return f"{base} ({packs} × {product.get('pack') or unit} = {other})"


def line_facts(lines: list[dict]) -> list[dict]:
    """The items of an order or sale as facts: [{"product", "qty", "unit"}, ...] in selling units."""
    return [{"product": ln["name"], "qty": ln["qty"], "unit": ln["unit"]} for ln in lines]


async def resolve_lines(shop_id: str, items: list[dict], lang: str | None = None,
                        supplier: dict | None = None) -> tuple[list[dict], list[str], dict | None]:
    """Spoken items → order lines in selling units. Returns (lines, English read-back phrases, error facts).

    Nothing is guessed: an unknown product (PRODUCT_UNKNOWN), a product this supplier does not sell
    (WRONG_SUPPLIER), or a quantity that is not a whole number of packs (QTY_UNCLEAR) returns a facts dict
    (``ok`` False, ``error``, ``say_en``) instead of a line. Callers do ``if err: return err``.
    """
    lines, phrases = [], []
    for it in items:
        it = it if isinstance(it, dict) else {"product": str(it)}
        spoken = str(it.get("product") or it.get("name") or "").strip()
        ref = await catalog.resolve(shop_id, spoken, lang)
        if ref is None:
            return [], [], hints.fact("PRODUCT_UNKNOWN", product=spoken)
        product = await product_doc(shop_id, ref.sku)
        name = product.get("name") or ref.sku
        sells = (supplier or {}).get("products") or []
        if sells and ref.sku not in sells:
            return [], [], {"ok": False, "error": "WRONG_SUPPLIER", "supplier": supplier["name"], "product": name,
                            "say_en": f"{supplier['name']} does not sell {name}."}
        spoken_qty = it.get("qty")
        # A bare count is packs: "2 milk" is 2 pouches (1 L), never 2 litres. The read-back says both forms.
        spoken_unit = _canon_unit(it.get("unit") or "")
        unclear = {"ok": False, "error": "QTY_UNCLEAR", "product": name, "qty": spoken_qty, "unit": spoken_unit,
                   "say_en": f"Cannot order that amount of {name}. Say how many packs."}
        try:
            qty, unit = catalog.to_selling_units(product, spoken_qty, spoken_unit)
        except (TypeError, ValueError):
            return [], [], unclear
        if qty <= 0:
            return [], [], unclear
        lines.append({"sku": ref.sku, "name": name, "qty": float(qty), "unit": unit,
                      "agreedPrice": int(product["costPrice"])})
        phrases.append(qty_phrase(product, qty, unit, spoken_qty, spoken_unit))
    return lines, phrases, None


def read_back_prompt(phrases: list[str], supplier_name: str, delivery_by: dict | None, note: str | None,
                     lang: str | None = None) -> str:
    """'Order 30 L Sakthi Curd 1 L from Sakthi vendor?' or, with extras, '…, deliver by 6 am tomorrow, note: …. Send it?'"""
    items = ", ".join(phrases)
    extras = []
    if delivery_by:
        extras.append(f"deliver by {delivery_by['text']}")
    if note:
        extras.append(f"note: {note}")
    if not extras:
        return f"Order {items} from {supplier_name}?"
    return f"Order {items} from {supplier_name}, {', '.join(extras)}. Send it?"


async def order_create(supplier: str, items: list[dict], delivery_by: str | None = None,
                       note: str | None = None) -> dict:
    """Order stock from a supplier when the OWNER asks, e.g. "order 30 litres Sakthi curd from the Sakthi vendor,
    deliver by 6 am tomorrow, send fresh stock".

    supplier: the vendor as spoken ("Sakthi vendor", "milkman", "ice cream distributor").
    items: [{"product": "sakthi curd", "qty": 30, "unit": "L"}]; unit is one of L, ml, kg, g, pack, jar, box.
    Leave unit out when the owner only gave a count ("20 milk"): a bare count means packs.
    delivery_by: when the owner wants it, in his words ("6 am tomorrow", "today evening", "நாளை காலை").
    note: any short instruction for the vendor, in his words ("send fresh stock", "pack the ice cream in dry ice").
    Capture any delivery time or note the owner says. If he gave no delivery time, ask ONCE "Any delivery time?"
    before calling; if he says no, call without delivery_by. Never invent a time or a note.
    Nothing is sent yet: this reads the order back and waits for the owner's yes (confirm_action), then sends it
    to the vendor with the time and the note.
    """
    s = get_session()
    sup = await catalog.resolve_supplier(s.shop_id, supplier, s.lang)
    if sup is None:
        return hints.fact("SUPPLIER_UNKNOWN", supplier=supplier)
    if not items:
        return {"ok": False, "error": "NO_ITEMS", "say_en": "What should I order?"}
    lines, phrases, err = await resolve_lines(s.shop_id, items, s.lang, sup)
    if err:
        return err
    by = delivery_time.parse_delivery_by(delivery_by)
    note_text = delivery_time.clean_note(note)
    idem = ids.current_idem_key()
    oid = doc_id_for("o", idem)
    if await _repo.get_order(s.shop_id, oid) is None:          # a retried tool call reuses the same draft
        now = now_iso()
        await _repo.save_order(s.shop_id, {
            "id": oid, "supplierId": sup["id"], "supplierName": sup["name"], "channel": sup.get("channel", "whatsapp"),
            "items": lines, "deliveryBy": by, "note": note_text, "state": "DRAFT", "lang": s.lang, "idemKey": idem,
            "createdAt": now, "updatedAt": now})
    prompt = read_back_prompt(phrases, sup["name"], by, note_text)
    facts = {"orderId": oid, "supplier": sup["name"], "items": line_facts(lines),
             "deliveryBy": by["text"] if by else None, "note": note_text}
    return await confirm.propose("order", prompt, {"order_id": oid, "idem": idem}, "order.send", facts=facts)


async def _supplier(shop_id: str, supplier_id: str) -> dict:
    return {**(await get_store().get(paths.supplier(shop_id, supplier_id)) or {}), "id": supplier_id}


def _already_sent(order: dict) -> dict:
    return {"ok": True, "orderId": order["id"], "state": order["state"], "supplier": order.get("supplierName"),
            "already": True, "say_en": "That order was already sent."}


async def _place(s: Session, order: dict, channel: str, message_id: str | None = None) -> dict:
    now = now_iso()
    order = {**order, "state": transition("order", order["state"], "ORDER_PLACED"), "channel": channel,
             "channelMsgId": message_id, "placedAt": now, "updatedAt": now}
    await _repo.save_order(s.shop_id, order)
    await outbox.order_state(s.shop_id, order["id"], "ORDER_PLACED")
    await audit.log(s.shop_id, "owner", "order_placed", "order", order["id"], channel)
    await s.send(StateMsg(entity="order", id=order["id"], state="ORDER_PLACED", summary=items_text(order["items"])))
    return {"ok": True, "orderId": order["id"], "state": "ORDER_PLACED", "supplier": order["supplierName"],
            "channel": channel, "say_en": f"Order sent to {order['supplierName']}."}


async def _commit_send(payload: dict, value: str | None) -> dict:
    """Committer "order.send": runs only after the owner's yes. Sends once; a retry says it was already sent."""
    s = get_session()
    order = await _repo.get_order(s.shop_id, payload["order_id"])
    if order is None:
        return dict(ORDER_NONE)
    if order["state"] != "DRAFT":
        return _already_sent(order)
    if is_judge(s):                            # a judge's order never reaches a real vendor
        return await _place_with_demo_vendor(s, order)
    res = await messaging.send_order(s.shop_id, await _supplier(s.shop_id, order["supplierId"]), order)
    if res.get("messageId"):
        return await _place(s, order, res["channel"], res["messageId"])
    # No message went out: the phone opens the click-to-chat (or tel:) link and the order stays DRAFT until the
    # owner says he sent it. An order is not placed on a link alone.
    await s.send(StateMsg(entity="order", id=order["id"], state="DRAFT", summary=f"OPEN_URL {res['fallbackUrl']}"))
    supplier, items = order["supplierName"], items_text(order["items"])
    if res.get("channel") == "call":
        first = f"Calling {supplier}. Tell them: {items}."
    else:
        first = "The order message is ready. Double-tap Send, then tell me when it is sent."
    reason = res.get("reason")
    if reason == "telegram_not_linked":            # never silent: say why the Telegram bot did not send it
        first = f"{supplier} is not on the AIRA Telegram bot yet. {first}"
    elif reason and reason.startswith("telegram"):  # down or not set up: not the vendor's doing
        first = f"The Telegram bot could not send it right now. {first}"
    follow = await confirm.propose("generic", f"Did you send the order to {supplier}?",
                                   {"order_id": order["id"], "idem": payload.get("idem") or order.get("idemKey")},
                                   "order.mark_sent",
                                   facts={"orderId": order["id"], "supplier": supplier, "items": line_facts(order["items"])})
    out = {**follow, "ok": True, "orderId": order["id"], "state": "DRAFT", "channel": res.get("channel"),
           "fallbackUrl": res["fallbackUrl"], "say_en": f"{first} {follow['say_en']}"}
    if reason:
        out["reason"] = reason
    return out


async def _place_with_demo_vendor(s: Session, order: dict) -> dict:
    out = await _place(s, order, "demo", f"demo-{order['id']}")
    out["demo"] = True
    out["say_en"] = f"Order sent to the demo {order['supplierName']}. The reply comes in a few seconds."
    task = asyncio.create_task(_demo_vendor_reply(s, order["id"]))
    _DEMO_REPLIES.add(task)
    task.add_done_callback(_DEMO_REPLIES.discard)
    return out


async def _demo_vendor_reply(s: Session, order_id: str) -> None:
    """After a short pause, speak the demo vendor's reply to this judge's session only (``_speak.speak_to``, never
    the shop-wide announcer). The line is English facts; Live says it in the judge's language. A judge who
    already left is skipped quietly."""
    try:
        await asyncio.sleep(DEMO_REPLY_DELAY_S)
        order = await _repo.get_order(s.shop_id, order_id)
        if order is None or order["state"] != "ORDER_PLACED":
            return
        by = (order.get("deliveryBy") or {}).get("text")
        reply = f"OK, I will deliver by {by}." if by else "OK, delivery today at 6 pm."
        await _speak.speak_to(s, f"Demo reply from {order['supplierName']}: {reply}")
    except Exception:
        log.warning("demo vendor reply for order %s: session %s unreachable", order_id, s.session_id, exc_info=True)


async def _commit_mark_sent(payload: dict, value: str | None) -> dict:
    """Committer "order.mark_sent": the owner confirmed the click-to-chat message (or the call) went out."""
    s = get_session()
    order = await _repo.get_order(s.shop_id, payload["order_id"])
    if order is None:
        return dict(ORDER_NONE)
    if order["state"] != "DRAFT":
        return _already_sent(order)
    return await _place(s, order, "manual")


def _state_words(state: str, lang: str | None = None) -> str:
    """English meaning of an order state code ("DELIVERY_PENDING" → "delivery pending")."""
    return _STATE_WORDS.get(state) or state.replace("_", " ").lower()


async def order_status(order_id: str | None = None) -> dict:
    """Say where the latest open supplier order (or the given order_id) stands, with the vendor's reply if any."""
    s = get_session()
    order = (await _repo.get_order(s.shop_id, order_id) if order_id
             else await _repo.latest_order(s.shop_id, ACTIVE_STATES))
    if order is None:
        return dict(ORDER_NONE)
    reply = (order.get("vendorReply") or {}).get("text")
    supplier = order.get("supplierName") or order.get("supplierId", "")
    say = f"Order to {supplier}: {_state_words(order['state'])}."
    if reply:
        say = f"{say} They said: {reply}"
    return {"ok": True, "orderId": order["id"], "state": order["state"], "supplier": supplier,
            "items": line_facts(order.get("items") or []), "vendorReply": reply or None, "say_en": say}


confirm.register_committer("order.send", _commit_send)
confirm.register_committer("order.mark_sent", _commit_mark_sent)
