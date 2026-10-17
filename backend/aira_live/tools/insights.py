"""Live tools day_summary and reorder_suggest. OWNER: Saravana (saravana/02).

Both only read and suggest. Ordering goes through Kanish's order_create, which runs its own read-back
confirmation, so there is no double confirm here. No forecast means no number: AIRA says it lacks history.
Results are facts (exact counts, rupees, product and supplier names) plus a short English ``say_en``; Gemini
Live phrases them in whatever language the owner is speaking.
"""
from __future__ import annotations

import asyncio
import logging

from aira_live.analytics import forecast, summary
from aira_live.db import db
from aira_live.session import get_session

log = logging.getLogger(__name__)

UNAVAILABLE = "I can't read the shop records right now. Please try again."


# business.catalog / business.ledger (Kanish) are imported at call time so the tool registry still loads
# while that lane is landing.
async def _resolve(shop_id: str, spoken: str, lang: str):
    from aira_live.business import catalog
    return await catalog.resolve(shop_id, spoken, lang)


async def _on_hand(shop_id: str, sku: str) -> float:
    from aira_live.business import ledger
    return float(await ledger.on_hand(shop_id, sku))


async def _products(shop_id: str) -> dict[str, dict]:
    col = db().collection("shops").document(shop_id).collection("products")
    return {d.id: doc async for d in col.stream() if (doc := d.to_dict() or {}).get("active") is not False}


async def _suppliers_by_sku(shop_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    async for d in db().collection("shops").document(shop_id).collection("suppliers").stream():
        sup = d.to_dict() or {}
        for sku in sup.get("products", []):
            out.setdefault(sku, sup.get("name", d.id))
    return out


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:.1f}"


async def day_summary() -> dict:
    """Today's sales count, revenue in rupees, top seller and suggested orders, as facts."""
    s = get_session()
    try:
        d = await summary.today(s.shop_id)
    except Exception as exc:  # never invent numbers when the records can't be read
        log.warning("day_summary: read failed for %s: %s", s.shop_id, exc)
        return {"ok": False, "error": "unavailable", "say_en": UNAVAILABLE}
    return {"ok": True, **d, **summary.facts(d)}


def _none(say_en: str, **facts) -> dict:
    return {"ok": True, **facts, "suggestions": [], "next_tool": None, "say_en": say_en}


def _suggestion_en(x: dict) -> str:
    on_hand = summary.amount(x["on_hand"], x["unit"], _fmt)
    forecast_amt = summary.amount(x["forecast"], x["unit"], _fmt)
    qty = summary.amount(x["qty"], x["unit"], _fmt)
    line = f"{x['name']}: {on_hand} left, you sell about {forecast_amt} a day. Order {qty}"
    return f"{line} from {x['supplier']}?" if x["supplier"] else f"{line}?"


async def reorder_suggest(product: str | None = None) -> dict:
    """Suggest a reorder quantity from recent sales, for one product or for all (only suggests, never orders)."""
    s = get_session()
    try:
        products, suppliers = await asyncio.gather(_products(s.shop_id), _suppliers_by_sku(s.shop_id))
        if product:
            ref = await _resolve(s.shop_id, product, s.lang)
            if ref is None:
                return {**_none(f"{product} is not in the shop's catalog.", product=product), "ok": False,
                        "error": "unknown_product"}
            skus = [ref.sku]
            products.setdefault(ref.sku, {"name": getattr(ref, "name", ref.sku)})
        else:
            skus = list(products)
        forecasts, stocks = await asyncio.gather(
            asyncio.gather(*(forecast.daily_demand(s.shop_id, sku) for sku in skus)),
            asyncio.gather(*(_on_hand(s.shop_id, sku) for sku in skus)),
        )
    except Exception as exc:  # never invent numbers when the records can't be read
        log.warning("reorder_suggest: read failed for %s: %s", s.shop_id, exc)
        return {**_none(UNAVAILABLE), "ok": False, "error": "unavailable"}

    suggestions, any_history = [], False
    for sku, fc, on in zip(skus, forecasts, stocks):
        if fc is None:  # no sales history for this product: skip it rather than guess
            continue
        any_history = True
        qty = summary.suggest_qty(fc, on)
        if qty > 0:
            product = products.get(sku, {})
            # Stock, forecast and qty are all in the product's selling unit (litres for milk and curd).
            suggestions.append({"sku": sku, "name": product.get("name", sku), "qty": qty,
                                "unit": product.get("unit") or summary.DEFAULT_UNIT,
                                "supplier": suppliers.get(sku), "forecast": fc, "on_hand": on})
    if not any_history:
        return _none("Not enough sales history yet to suggest an order.", reason="not_enough_data")
    if not suggestions:
        return _none("Stock looks fine. Nothing to order now.", reason="nothing_to_order")

    lines = [_suggestion_en(x) for x in suggestions]
    demo = await forecast.demo_history(s.shop_id)  # backfilled demo rows feed the number: say so, never as real
    if demo:
        lines.insert(0, "Based on demo sales history.")
    return {"ok": True, "suggestions": suggestions, "demo_history": demo, "next_tool": "order_create",
            "say_en": " ".join(lines)}
