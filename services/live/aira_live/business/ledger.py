"""Immutable stock ledger + inventory balance with FIFO batches. Idempotent; reads before writes.

Quantities are always in the product's selling unit (`products/{sku}.unit`: "L", "pack", "jar", "box");
callers convert spoken amounts with `catalog.to_selling_units` first.
"""
from datetime import date, timedelta
from typing import Literal

from aira_live.business import outbox, paths
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso, today_ist
from aira_live.business.store import Txn, get_store

Reason = Literal["DELIVERY", "SALE", "RETURN", "ADJUST", "EXPIRED"]
_REASONS = {"DELIVERY", "SALE", "RETURN", "ADJUST", "EXPIRED"}
_NEGATIVE_ONLY = {"SALE", "RETURN", "EXPIRED"}
_EPS = 1e-9


class InsufficientStock(ValueError):
    pass


def _validate(delta: float, reason: str) -> None:
    if reason not in _REASONS:
        raise ValueError(f"unknown reason {reason!r}")
    if delta == 0:
        raise ValueError("delta must be non-zero")
    if reason in _NEGATIVE_ONLY and delta > 0:
        raise ValueError(f"{reason} must be negative")
    if reason == "DELIVERY" and delta < 0:
        raise ValueError("DELIVERY must be positive")


def _as_date(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _q(x: float) -> float:
    return round(float(x), 3)


async def prepare_movement(txn: Txn, shop_id: str, sku: str, delta: float, reason: Reason, ref_type: str,
                           ref_id: str, idem_key: str, *, expiry_date: date | str | None = None,
                           today: date | None = None, actor: str = "aira") -> dict:
    """READS only. Returns a plan for commit_movement (or the cached result if already applied)."""
    _validate(delta, reason)
    seen = await txn.get(paths.idem(idem_key))
    if seen is not None:
        return {"done": True, "result": seen["result"]}
    product = await txn.get(paths.product(shop_id, sku))
    if product is None:
        raise KeyError(f"unknown sku {sku}")
    inv = await txn.get(paths.inventory(shop_id, sku)) or {"onHand": 0.0, "batches": []}
    today = today or today_ist()
    batches = [dict(b) for b in inv.get("batches") or []]
    on_hand = float(inv.get("onHand") or 0.0)
    # Stock written outside the ledger (onHand without batches) is treated as the oldest, undated lot.
    untracked = max(0.0, _q(on_hand - sum(float(b["qty"]) for b in batches)))
    if delta > 0:
        exp = _as_date(expiry_date) or (today + timedelta(days=int(product.get("shelfLifeDays") or 2)))
        batches.append({"batchId": doc_id_for("b", idem_key), "qty": _q(delta), "expiryDate": exp.isoformat()})
    else:
        need = -float(delta)
        if need > on_hand + _EPS:
            raise InsufficientStock(f"{sku}: have {on_hand}, need {need}")
        take = min(untracked, need)
        untracked, need = _q(untracked - take), _q(need - take)
        remaining = []
        for b in sorted(batches, key=lambda x: x.get("expiryDate") or ""):
            if need <= _EPS:
                remaining.append(b)
                continue
            take = min(float(b["qty"]), need)
            need = _q(need - take)
            left = _q(float(b["qty"]) - take)
            if left > _EPS:
                remaining.append({**b, "qty": left})
        batches = remaining
    at = now_iso()
    new_inv = {"sku": sku, "onHand": _q(untracked + sum(float(b["qty"]) for b in batches)), "batches": batches,
               "updatedAt": at}
    txn_id = doc_id_for("t", idem_key)
    return {"done": False, "shop_id": shop_id, "sku": sku, "txn_id": txn_id, "new_inv": new_inv,
            "ledger": {"id": txn_id, "sku": sku, "delta": float(delta), "unit": product.get("unit", "pack"),
                       "reason": reason, "refType": ref_type, "refId": ref_id, "idemKey": idem_key, "at": at,
                       "actor": actor},
            "idem_key": idem_key}


def commit_movement(txn: Txn, prep: dict) -> dict:
    """WRITES only."""
    if prep["done"]:
        return prep["result"]
    txn.set(paths.inventory(prep["shop_id"], prep["sku"]), prep["new_inv"])
    txn.set(paths.ledger(prep["shop_id"], prep["txn_id"]), prep["ledger"])
    txn.set(paths.idem(prep["idem_key"]),
            {"key": prep["idem_key"], "result": prep["new_inv"], "at": prep["ledger"]["at"]})
    return prep["new_inv"]


async def publish_movement(prep: dict) -> None:
    """Post-commit analytics for one movement; no-op for replays. Never call inside a transaction."""
    if prep["done"]:
        return
    led = prep["ledger"]
    await outbox.inventory_movement(prep["shop_id"], prep["txn_id"], led["sku"], led["delta"], led["reason"])


async def apply(shop_id: str, sku: str, delta: float, reason: Reason, ref_type: str, ref_id: str, idem_key: str,
                *, expiry_date: date | str | None = None, today: date | None = None, actor: str = "aira") -> dict:
    """One stock movement in one transaction. Returns the inventory document. Idempotent on idem_key."""
    async def fn(txn: Txn) -> tuple[dict, dict]:
        prep = await prepare_movement(txn, shop_id, sku, delta, reason, ref_type, ref_id, idem_key,
                                      expiry_date=expiry_date, today=today, actor=actor)
        return prep, commit_movement(txn, prep)

    prep, result = await get_store().run(fn)
    await publish_movement(prep)                                 # after commit, outside the transaction
    return result


async def on_hand(shop_id: str, sku: str) -> float:
    inv = await get_store().get(paths.inventory(shop_id, sku))
    return float(inv.get("onHand") or 0.0) if inv else 0.0
