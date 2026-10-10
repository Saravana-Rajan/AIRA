"""Payment records. Cash/UPI only. A payment is CONFIRMED only with evidence:
a cash count, a soundbox parse (incoming UPI, amount equal ±₹0) or explicit owner confirmation."""
from typing import Literal

from aira_live.business import paths
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso
from aira_live.business.money import InsufficientTender, change_due
from aira_live.business.states import transition
from aira_live.business.store import get_store

EVIDENCE_TYPES = ("cash_count", "soundbox", "user_confirm")


class PaymentMismatch(ValueError):
    pass


def _whole_rupees(value) -> int | None:
    """Integer rupees, or None if the value is missing / not a whole number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else None


async def record(shop_id: str, direction: Literal["in", "out"], method: Literal["cash", "upi"], amount: int,
                 ref_type: str, ref_id: str, idem_key: str) -> dict:
    if direction not in ("in", "out"):
        raise ValueError("direction must be 'in' or 'out'")
    if method not in ("cash", "upi"):
        raise ValueError("method must be 'cash' or 'upi'")
    rupees = _whole_rupees(amount)
    if rupees is None or rupees <= 0:
        raise ValueError("amount must be a positive whole number of rupees")
    pid = doc_id_for("p", idem_key)
    path = paths.payment(shop_id, pid)

    async def fn(txn):
        existing = await txn.get(path)
        if existing is not None:
            return existing
        at = now_iso()
        doc = {"id": pid, "direction": direction, "method": method, "amount": rupees, "state": "PENDING",
               "evidence": None, "refType": ref_type, "refId": ref_id, "idemKey": idem_key,
               "createdAt": at, "updatedAt": at}
        txn.set(path, doc)
        return doc
    return await get_store().run(fn)


def _check_evidence(p: dict, evidence: dict) -> dict:
    """Validate evidence against the payment. Returns the stored evidence: the caller's fields, plus `change`
    for incoming cash and a short `detail` record (packages/contracts PaymentEvidence) unless one was given."""
    etype = evidence.get("type")
    if etype not in EVIDENCE_TYPES:
        raise ValueError(f"evidence type must be one of {EVIDENCE_TYPES}")
    ev = dict(evidence)
    if etype == "soundbox":
        if p["method"] != "upi" or p["direction"] != "in":
            raise PaymentMismatch("soundbox evidence only confirms incoming UPI")
        heard = _whole_rupees(evidence.get("amount"))
        if heard != p["amount"]:
            raise PaymentMismatch(f"soundbox said {evidence.get('amount')}, expected {p['amount']}")
        provider = evidence.get("provider")
        detail = f"soundbox ₹{heard}" + (f" ({provider})" if provider else "")
    elif etype == "cash_count":
        if p["method"] != "cash":
            raise PaymentMismatch("cash_count evidence only confirms cash")
        total = _whole_rupees(evidence.get("total"))
        if total is None:
            raise PaymentMismatch("cash count has no whole-rupee total")
        if p["direction"] == "out":
            if total != p["amount"]:
                raise PaymentMismatch(f"counted {total}, must pay exactly {p['amount']}")
            detail = f"cash counted ₹{total}"
        else:
            try:
                ev["change"] = change_due(p["amount"], total)
            except InsufficientTender as e:
                raise PaymentMismatch(str(e)) from e
            detail = f"cash counted ₹{total}, change ₹{ev['change']}"
    else:
        detail = "owner confirmed"
    if not isinstance(ev.get("detail"), str) or not ev["detail"]:
        ev["detail"] = detail
    return ev


async def confirm(shop_id: str, payment_id: str, evidence: dict) -> dict:
    path = paths.payment(shop_id, payment_id)

    async def fn(txn):
        p = await txn.get(path)
        if p is None:
            raise KeyError(f"payment {payment_id} not found")
        if p["state"] == "CONFIRMED":
            return p                                      # retry: unchanged, evidence not re-checked
        ev = _check_evidence(p, evidence)
        p = {**p, "state": transition("payment", p["state"], "CONFIRMED"), "evidence": ev, "updatedAt": now_iso()}
        txn.set(path, p)
        return p
    return await get_store().run(fn)


async def fail(shop_id: str, payment_id: str, reason: str) -> dict:
    path = paths.payment(shop_id, payment_id)

    async def fn(txn):
        p = await txn.get(path)
        if p is None:
            raise KeyError(f"payment {payment_id} not found")
        if p["state"] == "FAILED":
            return p
        p = {**p, "state": transition("payment", p["state"], "FAILED"), "failReason": reason, "updatedAt": now_iso()}
        txn.set(path, p)
        return p
    return await get_store().run(fn)
