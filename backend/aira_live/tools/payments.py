"""Supplier payments and customer payments. Cash or UPI only. OWNER: Kanish (kanish/02).

The model proposes, the backend commits. Every payment goes through ``confirm.propose`` and changes money or
state only in a committer, after the owner's "yes":
- ``supplier_pay.cash``: the notes in the owner's hand were read twice by vision and equal the amount due
  (``reconcile.payable``: what was counted, at the agreed price). Evidence ``cash_count``.
- ``supplier_pay.upi``: the owner pays from his own UPI app and says so. Evidence ``user_confirm``.
- ``sale.complete``: a customer paid. Cash: the tendered notes and the change were both read. UPI: the shop's
  payment soundbox announced exactly the sale total (``soundbox.parse``), or, when it stayed silent, the owner
  confirms. A customer's phone screen is never evidence.
Every result and every spoken prompt is facts (amounts, change, shortfall as numbers) plus an English ``say_en``
hint; Live phrases them in the owner's language. Nothing here judges whether money is genuine.
"""
import asyncio
from typing import Literal

from aira_live import confirm, ids
from aira_live.business import audit, hints, ledger, money, outbox, paths, payments, reconcile, sales, soundbox
from aira_live.business.clock import now_iso
from aira_live.business.ledger import InsufficientStock
from aira_live.business.payments import PaymentMismatch
from aira_live.business.sales import SaleError
from aira_live.business.states import InvalidTransition, transition
from aira_live.business.store import get_store
from aira_live.contracts import CueMsg, StateMsg
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import Session, get_session
from aira_live.tools import _repo, _speak
from aira_live.tools import sales as sale_tools
from aira_live.tools.orders import ORDER_NONE, product_doc
from aira_live.vision import cash as vcash

CASH_PREP_S = 5.0        # time for the owner to take the notes out after hearing the plan
UPI_WAIT_S = 60.0        # how long to listen for the soundbox before asking the owner
FRAME_TIMEOUT_S = 6.0    # how long to wait for the phone's cash photos
PAYABLE_STATES = ["DELIVERY_RECONCILED", "DISCREPANCY", "PAYMENT_PENDING"]

METHOD_Q = {"ok": False, "error": "METHOD_UNKNOWN", "say_en": "Cash or UPI?"}
NOT_READY = {"ok": False, "error": "NOT_READY", "say_en": "Check the delivery and the bill before paying."}
SALE_NONE = {"ok": False, "error": "SALE_NONE", "say_en": "No open sale."}
# English meaning of the cash reader's hint keys (aira_live.vision.cash_merge.HINT_*).
CASH_HINTS_EN = {
    "cash.spread_notes": "Spread the notes apart so each one is visible.",
    "cash.unsupported_note": "One of these notes cannot be counted. Check it by hand.",
    "cash.show_again": "Not sure. Show the cash again.",
}
CASH_UNCLEAR_EN = "Cannot see the notes clearly. Show them again."


def _subscribe(session_id: str, callback):
    from aira_live import live_bridge
    return live_bridge.on_input_transcript(session_id, callback)


async def _notify(s: Session, text: str, cue: str = "ack") -> None:
    """Payment prompts belong to the session paying: never the shop-wide announcer, which every judge hears."""
    await _speak.speak_to(s, text, cue)


_COUNT_WORDS_EN = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
                   9: "nine", 10: "ten"}


def _notes_words(notes: list[dict], lang: str | None = None) -> str:
    """Spoken, not printed: "one 50, three 10s" (English; ``lang`` is accepted for compatibility and ignored)."""
    out = []
    for n in notes:
        count, d = int(n["count"]), int(n["denomination"])
        if count == 1:
            out.append(f"one {d}")
        else:
            out.append(f"{_COUNT_WORDS_EN.get(count, str(count))} {d}s")
    return ", ".join(out)


def _retake(hint_key: str | None = None) -> dict:
    return {"ok": False, "needs_retake": True, "error": "CASH_UNCLEAR", "hint": hint_key,
            "say_en": CASH_HINTS_EN.get(hint_key or "", CASH_UNCLEAR_EN)}


async def _read_cash(s: Session):
    """(CashRead, None) when both reads agree on a total, else (None, retake reply). Never guesses a total."""
    frames = await s.request_frames("cash", count=2, interval_ms=500, timeout_s=FRAME_TIMEOUT_S)
    if not frames:
        return None, _retake()
    read = await vcash.read_cash(frames)
    if read.hint_key:
        return None, _retake(read.hint_key)
    if not read.agreed or read.total is None:
        return None, _retake()
    return read, None


# ---------------------------------------------------------------------------------------------------- supplier

async def supplier_pay(order_id: str, method: Literal["cash", "upi"]) -> dict:
    """Pay the supplier for a checked delivery (after delivery_count and invoice_check).

    cash: AIRA says which notes to take out and checks them with the camera. upi: the owner pays in his own UPI
    app and confirms. Either way the payment is recorded only after the owner says yes (confirm_action).
    If AIRA asks to show the notes again, call this again.
    order_id: "" (empty) for the latest delivery waiting to be paid, which is almost always right. Only pass an
    orderId that a tool returned; never make one up from a supplier or product name.
    """
    s = get_session()
    if method not in ("cash", "upi"):
        return dict(METHOD_Q)
    order = (await _repo.get_order(s.shop_id, order_id) if order_id
             else await _repo.latest_order(s.shop_id, PAYABLE_STATES))
    if order is None:
        return dict(ORDER_NONE)
    if order["state"] not in PAYABLE_STATES:
        return dict(NOT_READY)
    d = await _repo.delivery_for_order(s.shop_id, order["id"])
    counted = {i["sku"]: float(i["countedQty"]) for i in (d or {}).get("items", []) if i.get("countedQty") is not None}
    amount = reconcile.payable(order["items"], counted)
    if amount <= 0:
        return dict(NOT_READY)
    # Nothing is written here: the payment doc and the PAYMENT_PENDING -> PAYMENT_CONFIRMED moves happen in the
    # committer, after the owner's yes. A "no" therefore leaves no pending payment behind.
    base = {"order_id": order["id"], "method": method, "amount": amount, "idem": ids.current_idem_key()}
    supplier = order.get("supplierName") or order.get("supplierId", "")
    facts = {"orderId": order["id"], "supplier": supplier, "amount": amount, "method": method}
    if method == "upi":
        return await confirm.propose("payment", f"Pay {amount} rupees to {supplier} from your UPI app, then say yes.",
                                     base, "supplier_pay.upi", facts=facts)
    await _notify(s, f"Pay {amount} rupees: {_notes_words(money.notes_for(amount))}. Show the notes to the camera.")
    await asyncio.sleep(CASH_PREP_S)
    read, retake = await _read_cash(s)
    if retake:
        return retake
    if read.total != amount:
        await s.send(CueMsg(cue="mismatch"))
        return {"ok": False, "needs_retake": True, "error": "CASH_WRONG", "seen": read.total, "amount": amount,
                "say_en": f"Seen {read.total} rupees, but it should be {amount}."}
    await s.send(CueMsg(cue="match"))
    return await confirm.propose("payment", f"{amount} rupees counted. Give it to {supplier} and confirm?",
                                 {**base, "total": read.total, "notes": read.notes}, "supplier_pay.cash",
                                 facts={**facts, "notes": read.notes})


async def _commit_supplier(payload: dict, evidence: dict) -> dict:
    """Committers "supplier_pay.cash" / "supplier_pay.upi": record the payment (idempotent on the proposal's
    idem key), confirm it with the evidence and move the order PAYMENT_PENDING -> PAYMENT_CONFIRMED. A retried
    confirm finds the order paid and changes nothing."""
    s = get_session()
    order = await _repo.get_order(s.shop_id, payload["order_id"])
    if order is None:
        return dict(ORDER_NONE)
    amount, method = payload["amount"], payload["method"]
    supplier = order.get("supplierName") or order.get("supplierId", "")
    done = {"ok": True, "orderId": order["id"], "supplier": supplier, "amount": amount, "method": method,
            "say_en": f"Payment of {amount} rupees to {supplier} recorded."}
    if order["state"] == "PAYMENT_CONFIRMED":
        return {**done, "paymentId": order.get("paymentId"), "already": True}  # retried
    if order["state"] not in PAYABLE_STATES:
        return hints.fact("PaymentMismatch")
    was_pending = order["state"] == "PAYMENT_PENDING"
    try:
        p = await payments.record(s.shop_id, "out", method, amount, "order", order["id"], f"{payload['idem']}:{method}")
        pending = order["state"] if was_pending else transition("order", order["state"], "PAYMENT_PENDING")
        await payments.confirm(s.shop_id, p["id"], evidence)
        state = transition("order", pending, "PAYMENT_CONFIRMED")
    except (PaymentMismatch, InvalidTransition, ValueError):
        return hints.fact("PaymentMismatch")
    order = {**order, "state": state, "paymentId": p["id"], "updatedAt": now_iso()}
    await _repo.save_order(s.shop_id, order)
    if not was_pending:  # analytics sees every state once, the pass through PAYMENT_PENDING included
        await outbox.order_state(s.shop_id, order["id"], "PAYMENT_PENDING")
    await outbox.order_state(s.shop_id, order["id"], "PAYMENT_CONFIRMED")
    await audit.log(s.shop_id, "owner", "supplier_paid", "order", order["id"], f"{evidence['type']}:{amount}")
    await s.send(StateMsg(entity="order", id=order["id"], state="PAYMENT_CONFIRMED", summary=str(amount)))
    await s.send(CueMsg(cue="done"))
    return {**done, "paymentId": p["id"]}


async def _commit_supplier_cash(payload: dict, value: str | None) -> dict:
    return await _commit_supplier(payload, {"type": "cash_count", "total": payload["total"], "notes": payload["notes"]})


async def _commit_supplier_upi(payload: dict, value: str | None) -> dict:
    return await _commit_supplier(payload, {"type": "user_confirm"})


# ---------------------------------------------------------------------------------------------------- customer

async def _sale(s: Session, sale_id: str | None) -> dict | None:
    sid = sale_id or sale_tools.active_sale.get(s.session_id)
    try:
        doc = await get_store().get(paths.sale(s.shop_id, sid)) if sid else None
    except ValueError:  # a model-supplied id that is not a document id: "no such sale", never a path error
        return None
    return {**doc, "id": sid} if doc else None


async def sale_pay(sale_id: str, method: Literal["cash", "upi"]) -> dict:
    """Take the customer's payment for the pending sale (from sale_start).

    cash: the owner shows the customer's notes, then the change he gives back. upi: AIRA listens for the shop's
    payment soundbox. Completing the sale always waits for the owner's yes (confirm_action).
    sale_id: "" (empty) for the sale that is open now, which is almost always right. Only pass a saleId that
    sale_start returned; never make one up.
    """
    s = get_session()
    sale = await _sale(s, sale_id)
    if sale is None or sale["state"] != "SALE_PENDING":
        return dict(SALE_NONE)
    if method not in ("cash", "upi"):
        return dict(METHOD_Q)
    total = int(sale["total"])
    idem = ids.current_idem_key()
    if method == "upi":
        return await _await_soundbox(s, sale, total, idem)
    read, retake = await _read_cash(s)
    if retake:
        return retake
    tendered = int(read.total)
    try:
        change = money.change_due(total, tendered)
    except money.InsufficientTender:
        await s.send(CueMsg(cue="mismatch"))
        return hints.fact("InsufficientTender", short=total - tendered, tendered=tendered, total=total)
    if change > 0:
        await _notify(s, f"Received {tendered}. Give back {change} rupees: {_notes_words(money.notes_for(change))}. "
                         "Show the change to the camera.")
        await asyncio.sleep(CASH_PREP_S)
        back, retake = await _read_cash(s)
        if retake:
            return retake
        if back.total != change:
            await s.send(CueMsg(cue="mismatch"))
            return {"ok": False, "needs_retake": True, "error": "CHANGE_WRONG", "seen": back.total, "change": change,
                    "say_en": f"That change is {back.total} rupees. It should be {change}."}
    await s.send(CueMsg(cue="match"))
    return await confirm.propose("sale", f"Received {tendered}, change {change}. Complete the sale?",
                                 {"sale_id": sale["id"], "method": "cash", "amount": total,
                                  "evidence": {"type": "cash_count", "total": tendered}, "idem": idem},
                                 "sale.complete",
                                 facts={"saleId": sale["id"], "total": total, "method": "cash", "tendered": tendered,
                                        "change": change})


async def _await_soundbox(s: Session, sale: dict, total: int, idem: str) -> dict:
    """Listen for the shop's soundbox and add up what it announces (a customer may pay in two goes).

    Short: tell the owner how much more to ask for and keep listening. Exact: propose completing. Over: tell
    him what to return and propose. Quiet: ask the owner; with a partial amount heard, the question names the
    shortfall, so only his explicit "yes" completes a sale that is not fully paid.
    """
    covered: asyncio.Future = asyncio.get_running_loop().create_future()
    heard: list[tuple[soundbox.SoundboxEvent, str]] = []
    facts = {"saleId": sale["id"], "total": total, "method": "upi"}

    def paid() -> int:
        return sum(ev.amount for ev, _ in heard)

    async def on_text(text: str) -> None:
        ev = soundbox.parse(text)
        if ev is None or covered.done():
            return
        heard.append((ev, text))
        if paid() < total:
            short = total - paid()
            await _notify(s, f"{short} rupees short, ask for {short} more.", "mismatch")
            return
        if paid() > total:
            extra = paid() - total
            await _notify(s, f"{extra} rupees extra received, return {extra}.", "mismatch")
        covered.set_result(None)

    unsubscribe = _subscribe(s.session_id, on_text)
    try:
        await _notify(s, "Waiting for the payment soundbox.")
        await asyncio.wait_for(covered, timeout=UPI_WAIT_S)
    except TimeoutError:
        amounts = [ev.amount for ev, _ in heard]
        if amounts:                                          # part paid: the question says what is missing
            short = total - paid()
            return await confirm.propose(
                "sale", f"Only {paid()} of {total} rupees received by UPI, {short} short. Complete the sale anyway?",
                {"sale_id": sale["id"], "method": "upi", "amount": total,
                 "evidence": {"type": "user_confirm", "heard": paid(), "payments": amounts}, "idem": idem},
                "sale.complete", facts={**facts, "paid": paid(), "short": short, "approx": True})
        return await confirm.propose("sale", f"The soundbox was not heard. Did you receive {total} rupees by UPI?",
                                     {"sale_id": sale["id"], "method": "upi", "amount": total,
                                      "evidence": {"type": "user_confirm"}, "idem": idem}, "sale.complete",
                                     facts={**facts, "paid": 0, "heard": False})
    finally:
        unsubscribe()
    received = paid()
    providers = {ev.provider for ev, _ in heard}
    evidence = {"type": "soundbox", "amount": total, "heard": received, "payments": [ev.amount for ev, _ in heard],
                "provider": providers.pop() if len(providers) == 1 else None,
                "transcript": " | ".join(text for _, text in heard)}
    if received > total:
        extra = received - total
        return await confirm.propose("sale", f"{received} rupees received by UPI, {extra} extra. Return {extra} and "
                                             "complete the sale?",
                                     {"sale_id": sale["id"], "method": "upi", "amount": total,
                                      "evidence": {**evidence, "extra": extra}, "idem": idem}, "sale.complete",
                                     facts={**facts, "paid": received, "extra": extra})
    await s.send(CueMsg(cue="match"))
    return await confirm.propose("sale", f"{received} rupees received by UPI. Complete the sale?",
                                 {"sale_id": sale["id"], "method": "upi", "amount": total, "evidence": evidence,
                                  "idem": idem}, "sale.complete", facts={**facts, "paid": received})


async def _commit_sale(payload: dict, value: str | None) -> dict:
    """Committer "sale.complete": record + confirm the incoming payment, then complete the sale (stock leaves
    once). A retried confirm finds the sale completed and changes nothing. The result carries what is left of
    each product sold (``stock``) and which of them are at or below their reorder threshold (``lowStock``)."""
    s = get_session()
    sale = await get_store().get(paths.sale(s.shop_id, payload["sale_id"]))
    if sale is None:
        return dict(SALE_NONE)
    if sale["state"] == "SALE_COMPLETED":
        return {"ok": True, "saleId": payload["sale_id"], "total": payload["amount"], "stock": [], "lowStock": [],
                "already": True, "say_en": "Sale complete."}
    try:
        p = await payments.record(s.shop_id, "in", payload["method"], payload["amount"], "sale", payload["sale_id"],
                                  payload["idem"])
        await payments.confirm(s.shop_id, p["id"], payload["evidence"])
        sale = await sales.complete(s.shop_id, payload["sale_id"], p["id"], payload["idem"])
    except (PaymentMismatch, SaleError, InvalidTransition):
        return hints.fact("PaymentMismatch")
    except InsufficientStock:
        return {"ok": False, "error": "STOCK_CHANGED", "say_en": "The stock changed. Check the items again."}
    sale_tools.active_sale.pop(s.session_id, None)
    await s.send(StateMsg(entity="sale", id=payload["sale_id"], state="SALE_COMPLETED", summary=str(payload["amount"])))
    await s.send(CueMsg(cue="done"))
    stock, low, lines = [], [], []
    for line in sale["items"]:
        prod = await product_doc(s.shop_id, line["sku"])
        left = await ledger.on_hand(s.shop_id, line["sku"])
        unit = prod.get("unit", "")
        left_text = f"{fmt_qty(left)} {unit}".strip()
        name = prod.get("name", line["sku"])
        row = {"sku": line["sku"], "product": name, "left": left, "unit": unit}
        stock.append(row)
        if left <= float(prod.get("reorderThreshold") or 0):
            low.append(row)
            lines.append(f"Only {left_text} left of {name}. Order more?")
        else:                                                   # the video: "Sale complete. Stock: 24 litres."
            lines.append(f"{name}: {left_text} left.")
    return {"ok": True, "saleId": payload["sale_id"], "paymentId": p["id"], "total": payload["amount"],
            "method": payload["method"], "stock": stock, "lowStock": low,
            "say_en": " ".join(["Sale complete.", *lines])}


confirm.register_committer("supplier_pay.cash", _commit_supplier_cash)
confirm.register_committer("supplier_pay.upi", _commit_supplier_upi)
confirm.register_committer("sale.complete", _commit_sale)
