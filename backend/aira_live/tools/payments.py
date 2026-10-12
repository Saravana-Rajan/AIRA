"""Supplier payments and customer payments. Cash or UPI only. OWNER: Kanish (kanish/02).

The model proposes, the backend commits. Every payment goes through ``confirm.propose`` and changes money or
state only in a committer, after the owner's "yes":
- ``supplier_pay.cash``: the notes in the owner's hand were read twice by vision and equal the amount due
  (``reconcile.payable``: what was counted, at the agreed price). Evidence ``cash_count``.
- ``supplier_pay.upi``: the owner pays from his own UPI app and says so. Evidence ``user_confirm``.
- ``sale.complete``: a customer paid. Cash: the tendered notes and the change were both read. UPI: the shop's
  payment soundbox announced exactly the sale total (``soundbox.parse``), or, when it stayed silent, the owner
  confirms. A customer's phone screen is never evidence.
Nothing here judges whether money is genuine.
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
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import Session, get_session
from aira_live.tools import _repo, _speak
from aira_live.tools import sales as sale_tools
from aira_live.tools.orders import product_doc
from aira_live.vision import cash as vcash

CASH_PREP_S = 5.0        # time for the owner to take the notes out after hearing the plan
UPI_WAIT_S = 60.0        # how long to listen for the soundbox before asking the owner
FRAME_TIMEOUT_S = 6.0    # how long to wait for the phone's cash photos
PAYABLE_STATES = ["DELIVERY_RECONCILED", "DISCREPANCY", "PAYMENT_PENDING"]


def _subscribe(session_id: str, callback):
    from aira_live import live_bridge
    return live_bridge.on_input_transcript(session_id, callback)


async def _notify(s: Session, text: str, cue: str = "ack") -> None:
    """Payment prompts belong to the session paying: never the shop-wide announcer, which every judge hears."""
    await _speak.speak_to(s, text, cue)


_COUNT_WORDS_EN = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
                   9: "nine", 10: "ten"}


def _notes_words(notes: list[dict], lang: str) -> str:
    """Spoken, not printed: "one 50, three 10s" (en), with the count as a word where the language has one."""
    out = []
    for n in notes:
        count, d = int(n["count"]), int(n["denomination"])
        if count == 1:
            out.append(t("k2.notes.one", lang, d=d))
        else:
            spoken = _COUNT_WORDS_EN.get(count, str(count)) if lang == "en" else str(count)
            out.append(t("k2.notes.many", lang, count=spoken, d=d))
    return ", ".join(out)


async def _read_cash(s: Session):
    """(CashRead, None) when both reads agree on a total, else (None, retake reply). Never guesses a total."""
    frames = await s.request_frames("cash", count=2, interval_ms=500, timeout_s=FRAME_TIMEOUT_S)
    if not frames:
        return None, {"ok": False, "needs_retake": True, "say": t("k2.cash.unclear", s.lang)}
    read = await vcash.read_cash(frames)
    if read.hint_key:
        return None, {"ok": False, "needs_retake": True, "say": t(read.hint_key, s.lang)}
    if not read.agreed or read.total is None:
        return None, {"ok": False, "needs_retake": True, "say": t("k2.cash.unclear", s.lang)}
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
        return {"ok": False, "say": t("k2.pay.method_q", s.lang)}
    order = (await _repo.get_order(s.shop_id, order_id) if order_id
             else await _repo.latest_order(s.shop_id, PAYABLE_STATES))
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    if order["state"] not in PAYABLE_STATES:
        return {"ok": False, "say": t("k2.pay.not_ready", s.lang)}
    d = await _repo.delivery_for_order(s.shop_id, order["id"])
    counted = {i["sku"]: float(i["countedQty"]) for i in (d or {}).get("items", []) if i.get("countedQty") is not None}
    amount = reconcile.payable(order["items"], counted)
    if amount <= 0:
        return {"ok": False, "say": t("k2.pay.not_ready", s.lang)}
    # Nothing is written here: the payment doc and the PAYMENT_PENDING -> PAYMENT_CONFIRMED moves happen in the
    # committer, after the owner's yes. A "no" therefore leaves no pending payment behind.
    base = {"order_id": order["id"], "method": method, "amount": amount, "idem": ids.current_idem_key()}
    supplier = order.get("supplierName") or order.get("supplierId", "")
    if method == "upi":
        return await confirm.propose("payment", t("k2.pay.upi_q", s.lang, amount=amount, supplier=supplier),
                                     base, "supplier_pay.upi")
    await _notify(s, t("k2.pay.plan", s.lang, amount=amount, notes=_notes_words(money.notes_for(amount), s.lang)))
    await asyncio.sleep(CASH_PREP_S)
    read, retake = await _read_cash(s)
    if retake:
        return retake
    if read.total != amount:
        await s.send(CueMsg(cue="mismatch"))
        return {"ok": False, "needs_retake": True,
                "say": t("k2.pay.cash_wrong", s.lang, seen=read.total, amount=amount)}
    await s.send(CueMsg(cue="match"))
    return await confirm.propose("payment", t("k2.pay.cash_q", s.lang, amount=amount, supplier=supplier),
                                 {**base, "total": read.total, "notes": read.notes}, "supplier_pay.cash")


async def _commit_supplier(payload: dict, evidence: dict) -> dict:
    """Committers "supplier_pay.cash" / "supplier_pay.upi": record the payment (idempotent on the proposal's
    idem key), confirm it with the evidence and move the order PAYMENT_PENDING -> PAYMENT_CONFIRMED. A retried
    confirm finds the order paid and changes nothing."""
    s = get_session()
    order = await _repo.get_order(s.shop_id, payload["order_id"])
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    amount, method = payload["amount"], payload["method"]
    done = t("k2.pay.done", s.lang, amount=amount, supplier=order.get("supplierName") or order.get("supplierId", ""))
    if order["state"] == "PAYMENT_CONFIRMED":
        return {"ok": True, "orderId": order["id"], "paymentId": order.get("paymentId"), "say": done}  # retried
    if order["state"] not in PAYABLE_STATES:
        return {"ok": False, "say": hints.say("PaymentMismatch", s.lang)}
    was_pending = order["state"] == "PAYMENT_PENDING"
    try:
        p = await payments.record(s.shop_id, "out", method, amount, "order", order["id"], f"{payload['idem']}:{method}")
        pending = order["state"] if was_pending else transition("order", order["state"], "PAYMENT_PENDING")
        await payments.confirm(s.shop_id, p["id"], evidence)
        state = transition("order", pending, "PAYMENT_CONFIRMED")
    except (PaymentMismatch, InvalidTransition, ValueError):
        return {"ok": False, "say": hints.say("PaymentMismatch", s.lang)}
    order = {**order, "state": state, "paymentId": p["id"], "updatedAt": now_iso()}
    await _repo.save_order(s.shop_id, order)
    if not was_pending:  # analytics sees every state once, the pass through PAYMENT_PENDING included
        await outbox.order_state(s.shop_id, order["id"], "PAYMENT_PENDING")
    await outbox.order_state(s.shop_id, order["id"], "PAYMENT_CONFIRMED")
    await audit.log(s.shop_id, "owner", "supplier_paid", "order", order["id"], f"{evidence['type']}:{amount}")
    await s.send(StateMsg(entity="order", id=order["id"], state="PAYMENT_CONFIRMED", summary=str(amount)))
    await s.send(CueMsg(cue="done"))
    return {"ok": True, "orderId": order["id"], "paymentId": p["id"], "say": done}


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
        return {"ok": False, "say": t("k2.sale.none", s.lang)}
    if method not in ("cash", "upi"):
        return {"ok": False, "say": t("k2.pay.method_q", s.lang)}
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
        return {"ok": False, "say": hints.say("InsufficientTender", s.lang, short=total - tendered)}
    if change > 0:
        await _notify(s, t("k2.sale.change", s.lang, tendered=tendered, change=change,
                           notes=_notes_words(money.notes_for(change), s.lang)))
        await asyncio.sleep(CASH_PREP_S)
        back, retake = await _read_cash(s)
        if retake:
            return retake
        if back.total != change:
            await s.send(CueMsg(cue="mismatch"))
            return {"ok": False, "needs_retake": True,
                    "say": t("k2.sale.change_wrong", s.lang, seen=back.total, change=change)}
    await s.send(CueMsg(cue="match"))
    return await confirm.propose("sale", t("k2.sale.cash_q", s.lang, tendered=tendered, change=change),
                                 {"sale_id": sale["id"], "method": "cash", "amount": total,
                                  "evidence": {"type": "cash_count", "total": tendered}, "idem": idem},
                                 "sale.complete")


async def _await_soundbox(s: Session, sale: dict, total: int, idem: str) -> dict:
    """Listen for the shop's soundbox and add up what it announces (a customer may pay in two goes).

    Short: tell the owner how much more to ask for and keep listening. Exact: propose completing. Over: tell
    him what to return and propose. Quiet: ask the owner; with a partial amount heard, the question names the
    shortfall, so only his explicit "yes" completes a sale that is not fully paid.
    """
    covered: asyncio.Future = asyncio.get_running_loop().create_future()
    heard: list[tuple[soundbox.SoundboxEvent, str]] = []

    def paid() -> int:
        return sum(ev.amount for ev, _ in heard)

    async def on_text(text: str) -> None:
        ev = soundbox.parse(text)
        if ev is None or covered.done():
            return
        heard.append((ev, text))
        if paid() < total:
            await _notify(s, t("k2.sale.upi_short", s.lang, short=total - paid()), "mismatch")
            return
        if paid() > total:
            await _notify(s, t("k2.sale.upi_over", s.lang, extra=paid() - total), "mismatch")
        covered.set_result(None)

    unsubscribe = _subscribe(s.session_id, on_text)
    try:
        await _notify(s, t("k2.sale.upi_wait", s.lang))
        await asyncio.wait_for(covered, timeout=UPI_WAIT_S)
    except TimeoutError:
        amounts = [ev.amount for ev, _ in heard]
        if amounts:                                          # part paid: the question says what is missing
            return await confirm.propose(
                "sale", t("k2.sale.upi_short_q", s.lang, paid=paid(), total=total, short=total - paid()),
                {"sale_id": sale["id"], "method": "upi", "amount": total,
                 "evidence": {"type": "user_confirm", "heard": paid(), "payments": amounts}, "idem": idem},
                "sale.complete")
        return await confirm.propose("sale", t("k2.sale.upi_timeout_q", s.lang, total=total),
                                     {"sale_id": sale["id"], "method": "upi", "amount": total,
                                      "evidence": {"type": "user_confirm"}, "idem": idem}, "sale.complete")
    finally:
        unsubscribe()
    received = paid()
    providers = {ev.provider for ev, _ in heard}
    evidence = {"type": "soundbox", "amount": total, "heard": received, "payments": [ev.amount for ev, _ in heard],
                "provider": providers.pop() if len(providers) == 1 else None,
                "transcript": " | ".join(text for _, text in heard)}
    if received > total:
        extra = received - total
        return await confirm.propose("sale", t("k2.sale.upi_over_q", s.lang, paid=received, extra=extra),
                                     {"sale_id": sale["id"], "method": "upi", "amount": total,
                                      "evidence": {**evidence, "extra": extra}, "idem": idem}, "sale.complete")
    await s.send(CueMsg(cue="match"))
    return await confirm.propose("sale", t("k2.sale.upi_heard_q", s.lang, amount=received),
                                 {"sale_id": sale["id"], "method": "upi", "amount": total, "evidence": evidence,
                                  "idem": idem}, "sale.complete")


async def _commit_sale(payload: dict, value: str | None) -> dict:
    """Committer "sale.complete": record + confirm the incoming payment, then complete the sale (stock leaves
    once). A retried confirm finds the sale completed and changes nothing."""
    s = get_session()
    sale = await get_store().get(paths.sale(s.shop_id, payload["sale_id"]))
    if sale is None:
        return {"ok": False, "say": t("k2.sale.none", s.lang)}
    if sale["state"] == "SALE_COMPLETED":
        return {"ok": True, "saleId": payload["sale_id"], "lowStock": [], "say": t("k2.sale.done", s.lang)}
    try:
        p = await payments.record(s.shop_id, "in", payload["method"], payload["amount"], "sale", payload["sale_id"],
                                  payload["idem"])
        await payments.confirm(s.shop_id, p["id"], payload["evidence"])
        sale = await sales.complete(s.shop_id, payload["sale_id"], p["id"], payload["idem"])
    except (PaymentMismatch, SaleError, InvalidTransition):
        return {"ok": False, "say": hints.say("PaymentMismatch", s.lang)}
    except InsufficientStock:
        return {"ok": False, "say": t("k2.sale.stock_changed", s.lang)}
    sale_tools.active_sale.pop(s.session_id, None)
    await s.send(StateMsg(entity="sale", id=payload["sale_id"], state="SALE_COMPLETED", summary=str(payload["amount"])))
    await s.send(CueMsg(cue="done"))
    low, left_lines = [], []
    for line in sale["items"]:
        prod = await product_doc(s.shop_id, line["sku"])
        left = await ledger.on_hand(s.shop_id, line["sku"])
        left_text = f"{fmt_qty(left)} {prod.get('unit', '')}".strip()
        name = prod.get("name", line["sku"])
        if left <= float(prod.get("reorderThreshold") or 0):
            low.append(t("k2.sale.low_stock", s.lang, left=left_text, product=name))
        else:                                                   # the video: "Sale complete. Stock: 24 litres."
            left_lines.append(t("k2.sale.left", s.lang, left=left_text, product=name))
    return {"ok": True, "saleId": payload["sale_id"], "lowStock": low,
            "say": " ".join([t("k2.sale.done", s.lang), *left_lines, *low])}


confirm.register_committer("supplier_pay.cash", _commit_supplier_cash)
confirm.register_committer("supplier_pay.upi", _commit_supplier_upi)
confirm.register_committer("sale.complete", _commit_sale)
