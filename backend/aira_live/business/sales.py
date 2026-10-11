"""Sales: priced from the catalog, stock deducted exactly once on completion, analytics published after commit."""
from decimal import ROUND_HALF_UP, Decimal

from aira_live.business import audit, ledger, outbox, paths
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso
from aira_live.business.ledger import InsufficientStock
from aira_live.business.states import transition
from aira_live.business.store import get_store


class SaleError(ValueError):
    pass


def _aggregate(items: list[dict]) -> dict[str, float]:
    agg: dict[str, float] = {}
    for it in items:
        qty = float(it["qty"])
        if qty <= 0:
            raise SaleError("quantity must be positive")
        agg[it["sku"]] = round(agg.get(it["sku"], 0.0) + qty, 3)
    if not agg:
        raise SaleError("empty sale")
    return agg


def _rupees(unit_price: int, qty: float) -> int:
    """Line total in whole rupees, half rounded up (₹2.50 -> ₹3), never banker's rounding."""
    return int((Decimal(unit_price) * Decimal(str(qty))).quantize(Decimal(1), rounding=ROUND_HALF_UP))


async def create(shop_id: str, items: list[dict], idem_key: str) -> dict:
    """items: [{sku, qty}] in selling units. Duplicate SKUs are summed. Nothing is written if any line fails."""
    agg = _aggregate(items)
    sid = doc_id_for("s", idem_key)
    path = paths.sale(shop_id, sid)
    store = get_store()

    async def fn(txn):
        existing = await txn.get(path)
        if existing is not None:
            return existing
        lines = []
        for sku, qty in agg.items():
            prod = await txn.get(paths.product(shop_id, sku))
            if prod is None:
                raise KeyError(f"unknown sku {sku}")
            inv = await txn.get(paths.inventory(shop_id, sku)) or {"onHand": 0.0}
            if float(inv.get("onHand", 0.0)) + 1e-9 < qty:
                raise InsufficientStock(f"{sku}: have {inv.get('onHand', 0.0)}, need {qty}")
            unit = int(prod["price"])
            lines.append({"sku": sku, "qty": qty, "unitPrice": unit, "lineTotal": _rupees(unit, qty)})
        doc = {"id": sid, "items": lines, "total": sum(ln["lineTotal"] for ln in lines), "state": "SALE_PENDING",
               "paymentId": None, "idemKey": idem_key, "createdAt": store.server_ts(), "updatedAt": now_iso()}
        txn.set(path, doc)
        return doc
    return await store.run(fn)


async def complete(shop_id: str, sale_id: str, payment_id: str, idem_key: str) -> dict:
    """SALE_PENDING -> SALE_COMPLETED with a CONFIRMED incoming payment for this sale and its exact total.
    Deducts stock once: a retry (same or new idem_key) of a completed sale returns it unchanged."""
    spath, ppath = paths.sale(shop_id, sale_id), paths.payment(shop_id, payment_id)
    store = get_store()

    async def fn(txn):
        sale = await txn.get(spath)                                  # ---- reads ----
        if sale is None:
            raise KeyError(f"sale {sale_id} not found")
        if sale["state"] == "SALE_COMPLETED":
            return sale, []                                          # already done: no deduction, no events
        new_state = transition("sale", sale["state"], "SALE_COMPLETED")   # a voided sale stops here
        pay = await txn.get(ppath)
        if pay is None or pay["state"] != "CONFIRMED" or pay["direction"] != "in":
            raise SaleError("payment not confirmed")
        if pay.get("refType") != "sale" or pay.get("refId") != sale_id:
            raise SaleError(f"payment {payment_id} is not for sale {sale_id}")
        if pay["amount"] != sale["total"]:
            raise SaleError(f"payment {pay['amount']} != sale total {sale['total']}")
        preps = [await ledger.prepare_movement(txn, shop_id, line["sku"], -float(line["qty"]), "SALE", "sale",
                                               sale_id, f"{idem_key}:{line['sku']}")
                 for line in sale["items"]]
        for prep in preps:                                           # ---- writes ----
            ledger.commit_movement(txn, prep)
        sale = {**sale, "state": new_state, "paymentId": payment_id,
                "completedAt": store.server_ts(), "updatedAt": now_iso()}
        txn.set(spath, sale)
        audit.log_in_txn(txn, shop_id, "aira", "sale_completed", "sale", sale_id, "ok")
        return sale, preps

    sale, preps = await store.run(fn)
    if preps:                                                        # ---- after commit, outside the txn ----
        await outbox.sale_lines(shop_id, sale_id, sale["items"])
        for prep in preps:
            await ledger.publish_movement(prep)
    return sale


async def void(shop_id: str, sale_id: str) -> dict:
    """SALE_PENDING -> SALE_VOID. A completed sale cannot be voided (InvalidTransition). Idempotent."""
    spath = paths.sale(shop_id, sale_id)

    async def fn(txn):
        sale = await txn.get(spath)
        if sale is None:
            raise KeyError(f"sale {sale_id} not found")
        if sale["state"] == "SALE_VOID":
            return sale
        sale = {**sale, "state": transition("sale", sale["state"], "SALE_VOID"), "updatedAt": now_iso()}
        txn.set(spath, sale)
        return sale
    return await get_store().run(fn)
