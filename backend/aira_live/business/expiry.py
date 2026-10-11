"""Batch expiry scan (spec §5 inventory batches). Returns codes and numbers only; tools speak them."""
from datetime import date

from aira_live.business import paths
from aira_live.business.clock import today_ist
from aira_live.business.store import get_store


def _status(days_left: int) -> str | None:
    if days_left < 0:
        return "expired"
    if days_left == 0:
        return "expires_today"
    if days_left == 1:
        return "expires_tomorrow"
    return None


async def scan(shop_id: str, today: date | None = None) -> list[dict]:
    """Batches that are expired or expire today/tomorrow, soonest first.
    Items: {sku, name, qty, unit, daysLeft, batchId, expiryDate, status}."""
    today = today or today_ist()
    store = get_store()
    products = dict(await store.list(paths.products(shop_id)))
    alerts = []
    for sku, inv in await store.list(paths.inventories(shop_id)):
        product = products.get(sku, {})
        for b in inv.get("batches") or []:
            if not b.get("expiryDate") or float(b.get("qty") or 0) <= 0:
                continue
            days = (date.fromisoformat(b["expiryDate"]) - today).days
            status = _status(days)
            if status:
                alerts.append({"sku": sku, "name": product.get("name", sku), "qty": float(b["qty"]),
                               "unit": product.get("unit", "pack"), "daysLeft": days, "batchId": b.get("batchId"),
                               "expiryDate": b["expiryDate"], "status": status})
    return sorted(alerts, key=lambda a: (a["daysLeft"], a["sku"], a["expiryDate"]))
