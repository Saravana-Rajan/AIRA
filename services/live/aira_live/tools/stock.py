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
    for sku, p in await get_store().list(paths.products(s.shop_id)):
        qty = await ledger.on_hand(s.shop_id, sku)
        above = qty > float(p.get("reorderThreshold") or 0)
        rows.append((above, qty, p.get("name", sku), p.get("unit", ""), sku))
    rows.sort()
    listing = ", ".join(f"{name} {fmt_qty(q)} {unit}".strip() for _, q, name, unit, _ in rows[:ALL_LIMIT])
    return {"ok": True, "items": [{"sku": sku, "qty": q} for _, q, _, _, sku in rows],
            "say": t("k2.stock.all", s.lang, list=listing)}


async def expiry_check() -> dict:
    """Stock that has expired or expires today or tomorrow, soonest first."""
    s = get_session()
    alerts = await expiry.scan(s.shop_id, clock.today_ist())
    if not alerts:
        return {"ok": True, "alerts": [], "say": t("k2.expiry.none", s.lang)}
    listing = ", ".join(t("k2.expiry.item", s.lang, product=a["name"], qty=f"{fmt_qty(a['qty'])} {a['unit']}",
                          when=t(_WHEN[a["status"]], s.lang)) for a in alerts[:EXPIRY_LIMIT])
    return {"ok": True, "alerts": alerts, "say": t("k2.expiry.list", s.lang, list=listing)}
