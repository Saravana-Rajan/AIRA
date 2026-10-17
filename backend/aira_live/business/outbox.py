"""Post-commit analytics events (saravana/02 contracts). Never called inside a transaction.
A publish failure is logged and never undoes a committed business action."""
import logging
from typing import Awaitable, Callable

from aira_live.business.clock import now_iso

log = logging.getLogger(__name__)
Publisher = Callable[[str, dict], Awaitable[str]]
_publisher: Publisher | None = None


def set_publisher(fn: Publisher | None) -> None:
    """Tests inject a fake; None restores aira_live.analytics.events.publish."""
    global _publisher
    _publisher = fn


async def _publish(topic: str, payload: dict) -> None:
    try:
        pub = _publisher
        if pub is None:
            from aira_live.analytics.events import publish as pub
        await pub(topic, payload)
    except Exception:
        log.exception("analytics publish failed topic=%s payload=%s", topic, payload)


async def inventory_movement(shop_id: str, txn_id: str, sku: str, delta: float, reason: str) -> None:
    await _publish("inventory-movements",
                   {"shop_id": shop_id, "txn_id": txn_id, "sku": sku, "delta": float(delta), "reason": reason})


async def sale_lines(shop_id: str, sale_id: str, items: list[dict]) -> None:
    ts = now_iso()
    for it in items:
        await _publish("sales", {"shop_id": shop_id, "sale_id": sale_id, "sku": it["sku"], "qty": float(it["qty"]),
                                 "unit_price": int(it["unitPrice"]), "ts": ts})


async def order_state(shop_id: str, order_id: str, state: str) -> None:
    await _publish("orders", {"shop_id": shop_id, "order_id": order_id, "state": state})


async def discrepancies(shop_id: str, delivery_id: str, result) -> None:
    """result: reconcile.ReconcileResult (duck-typed: .discrepancies list of dicts)."""
    for d in result.discrepancies:
        field = d["kind"] if "sku" not in d else f"{d['kind']}:{d['sku']}"
        await _publish("discrepancies", {"shop_id": shop_id, "delivery_id": delivery_id, "field": field,
                                         "expected": d.get("expected"), "actual": d.get("actual")})
