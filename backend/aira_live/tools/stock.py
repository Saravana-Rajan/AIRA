"""Stock and expiry, spoken. OWNER: Kanish (kanish/02).

Read-only: these tools say the RECORDED stock (the ledger). A saved number is not proof the packs are still on
the shelf, so the words are "in stock", never "on the shelf".
"""
from aira_live.business import catalog, clock, expiry, hints, ledger, paths
from aira_live.business.store import get_store
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import get_session
from aira_live.tools.orders import product_doc

ALL_LIMIT = 6        # products spoken in the summary (lowest first); the rest are in "items"
EXPIRY_LIMIT = 5     # expiry alerts spoken (soonest first); the rest are in "alerts"
LOW_LIMIT = 3        # low products named after the stock summary
_WHEN = {"expired": "k2.when.expired", "expires_today": "k2.when.today", "expires_tomorrow": "k2.when.tomorrow"}


async def stock_query(product: str | None = None) -> dict:
    """How much of a product is in stock, e.g. "how much curd do I have?". Without a product: a short summary of
    everything, products at or below their reorder level first."""
    s = get_session()
    if product:
        ref = await catalog.resolve(s.shop_id, product, s.lang)
        if ref is None:
            return {"ok": False, "say": hints.say("PRODUCT_UNKNOWN", s.lang, product=product)}
        p = await product_doc(s.shop_id, ref.sku)
        qty = await ledger.on_hand(s.shop_id, ref.sku)
        return {"ok": True, "sku": ref.sku, "qty": qty,
                "say": t("k2.stock.one", s.lang, product=p.get("name", ref.sku),
                         qty=f"{fmt_qty(qty)} {p.get('unit', '')}".strip())}
    rows = []
    for sku, p in await catalog.active_products(s.shop_id):        # disabled products keep their history, unspoken
        qty = await ledger.on_hand(s.shop_id, sku)
        above = qty > float(p.get("reorderThreshold") or 0)
        rows.append((above, qty, p.get("name", sku), p.get("unit", ""), sku))
    rows.sort()
    listing = ", ".join(f"{name} {fmt_qty(q)} {unit}".strip() for _, q, name, unit, _ in rows[:ALL_LIMIT])
    # The video: "Aavin milk 500 ml is low: 8 left." Name what is at or below its reorder level, after the list.
    low = [t("k2.stock.low", s.lang, product=name, left=f"{fmt_qty(q)} {unit}".strip()) if q > 0
           else t("k2.stock.out", s.lang, product=name)
           for above, q, name, unit, _ in rows if not above][:LOW_LIMIT]
    return {"ok": True, "items": [{"sku": sku, "qty": q} for _, q, _, _, sku in rows],
            "say": " ".join([t("k2.stock.all", s.lang, list=listing), *low])}


async def _qty_words(shop_id: str, alert: dict, lang: str) -> str:
    """Pouches of milk and curd are counted in packs ("6 packs"): that is what the owner picks off the shelf. The
    ledger keeps litres; anything that is not a whole number of packs stays in litres."""
    product = await product_doc(shop_id, alert["sku"])
    size = product.get("packSizeL")
    if alert.get("unit") == "L" and size:
        packs = float(alert["qty"]) / float(size)
        if abs(packs - round(packs)) < 1e-6:
            return t("k2.qty.packs", lang, n=int(round(packs)))
    return f"{fmt_qty(alert['qty'])} {alert['unit']}"


async def expiry_check() -> dict:
    """Stock that has expired or expires today or tomorrow, soonest first."""
    s = get_session()
    alerts = await expiry.scan(s.shop_id, clock.today_ist())
    if not alerts:
        return {"ok": True, "alerts": [], "say": t("k2.expiry.none", s.lang)}
    items = []
    for a in alerts[:EXPIRY_LIMIT]:
        qty = await _qty_words(s.shop_id, a, s.lang)
        items.append(t("k2.expiry.item", s.lang, product=a["name"], qty=qty, when=t(_WHEN[a["status"]], s.lang)))
    listing = ", ".join(items)
    return {"ok": True, "alerts": alerts, "say": t("k2.expiry.list", s.lang, list=listing)}
