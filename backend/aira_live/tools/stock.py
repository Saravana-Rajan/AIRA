"""Stock and expiry, spoken. OWNER: Kanish (kanish/02).

Read-only: these tools return the RECORDED stock (the ledger) as facts; Gemini Live phrases them in the owner's
language. A saved number is not proof the packs are still on the shelf, so the meaning is "in stock", never
"on the shelf".
"""
from aira_live.business import catalog, clock, expiry, hints, ledger
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import get_session
from aira_live.tools.orders import product_doc

ALL_LIMIT = 6        # products in the summary hint (lowest first); every product is in "items"
EXPIRY_LIMIT = 5     # expiry alerts in the hint (soonest first); every alert is in "alerts"
LOW_LIMIT = 3        # low products named after the stock summary
_WHEN = {"expired": "already expired", "expires_today": "expires today", "expires_tomorrow": "expires tomorrow"}


def _qty(q: float, unit: str) -> str:
    return f"{fmt_qty(q)} {unit}".strip()


async def stock_query(product: str | None = None) -> dict:
    """How much of a product is in stock, e.g. "how much curd do I have?". Without a product: a short summary of
    everything, products at or below their reorder level first."""
    s = get_session()
    if product:
        ref = await catalog.resolve(s.shop_id, product, s.lang)
        if ref is None:
            return hints.fact("PRODUCT_UNKNOWN", product=product)
        p = await product_doc(s.shop_id, ref.sku)
        qty = await ledger.on_hand(s.shop_id, ref.sku)
        name, unit = p.get("name", ref.sku), p.get("unit", "")
        low = qty <= float(p.get("reorderThreshold") or 0)
        return {"ok": True, "sku": ref.sku, "product": name, "qty": qty, "unit": unit, "low": low,
                "say_en": f"{name}: {_qty(qty, unit)} in stock."}
    rows = []
    for sku, p in await catalog.active_products(s.shop_id):        # disabled products keep their history, unspoken
        qty = await ledger.on_hand(s.shop_id, sku)
        above = qty > float(p.get("reorderThreshold") or 0)
        rows.append((above, qty, p.get("name", sku), p.get("unit", ""), sku))
    rows.sort()
    items = [{"sku": sku, "product": name, "qty": q, "unit": unit, "low": not above}
             for above, q, name, unit, sku in rows]
    low = [it for it in items if it["low"]]
    listing = ", ".join(f"{it['product']} {_qty(it['qty'], it['unit'])}" for it in items[:ALL_LIMIT])
    # The video: "Aavin milk 500 ml is low: 8 left." Name what is at or below its reorder level, after the list.
    low_lines = [f"{it['product']} is low: {_qty(it['qty'], it['unit'])} left." if it["qty"] > 0
                 else f"{it['product']} is out of stock." for it in low[:LOW_LIMIT]]
    return {"ok": True, "items": items, "low": low, "say_en": " ".join([f"Stock: {listing}.", *low_lines])}


async def _qty_words(shop_id: str, alert: dict) -> str:
    """Pouches of milk and curd are counted in packs ("6 packs"): that is what the owner picks off the shelf. The
    ledger keeps litres; anything that is not a whole number of packs stays in litres."""
    product = await product_doc(shop_id, alert["sku"])
    size = product.get("packSizeL")
    if alert.get("unit") == "L" and size:
        packs = float(alert["qty"]) / float(size)
        if abs(packs - round(packs)) < 1e-6:
            return f"{int(round(packs))} packs"
    return f"{fmt_qty(alert['qty'])} {alert['unit']}"


async def expiry_check() -> dict:
    """Stock that has expired or expires today or tomorrow, soonest first."""
    s = get_session()
    alerts = await expiry.scan(s.shop_id, clock.today_ist())
    if not alerts:
        return {"ok": True, "alerts": [], "say_en": "Nothing expires today or tomorrow."}
    items = []
    for a in alerts[:EXPIRY_LIMIT]:
        items.append(f"{a['name']} {await _qty_words(s.shop_id, a)} {_WHEN[a['status']]}")
    return {"ok": True, "alerts": alerts, "say_en": f"{', '.join(items)}. Sell or return these first."}
