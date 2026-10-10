"""Day summary for one shop (IST business day). Pure aggregation in summarize(); Firestore reads in today().

Reads `shops/{shop}/{products,inventory,sales,orders}` in database "aira" (spec §5). It only reads: nothing
here writes stock, money or state.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, time
from zoneinfo import ZoneInfo

from aira_live.analytics import forecast
from aira_live.db import db
from aira_live.i18n import t

IST = ZoneInfo("Asia/Kolkata")
SAFETY = 1.15


def _num(x) -> str:
    return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)


def say(d: dict, lang: str) -> str:
    """The spoken day summary for a `today()` result. Shared by the day_summary tool and /jobs/day-summary."""
    names = d.get("names") or {}
    if not d.get("sales_count"):
        text = t("insights.no_sales", lang)
    else:
        top = d.get("top_sku")
        text = t("insights.day_summary", lang, count=d["sales_count"], revenue=_num(d.get("revenue", 0)),
                 top=names.get(top, top) or "")
    orders = d.get("suggested_orders") or []
    if orders:
        items = ", ".join(f"{_num(o['qty'])} {names.get(o['sku'], o['sku'])}" for o in orders)
        text = f"{text} {t('insights.tomorrow_order', lang, items=items)}"
    return text


def suggest_qty(forecast_value: float | None, on_hand: float, safety: float = SAFETY) -> int:
    """Units to order so one day of forecast demand plus a safety margin is covered. 0 without a forecast."""
    if forecast_value is None:
        return 0
    need = round(float(forecast_value) * safety - float(on_hand), 6)   # drop float noise before ceil
    return max(math.ceil(need), 0)


def summarize(sales: list[dict], products: dict[str, dict], inventory: dict[str, dict],
              forecasts: dict[str, float | None], open_discrepancies: int) -> dict:
    """Totals for completed sales, low-stock SKUs and forecast-based order suggestions."""
    completed = [s for s in sales if s.get("state") == "SALE_COMPLETED"]
    qty_by_sku: dict[str, float] = {}
    for s in completed:
        for item in s.get("items", []):
            qty_by_sku[item["sku"]] = qty_by_sku.get(item["sku"], 0.0) + float(item.get("qty", 0))

    def on_hand(sku: str) -> float:
        return float((inventory.get(sku) or {}).get("onHand", 0))

    low = sorted(sku for sku, p in products.items() if on_hand(sku) <= float(p.get("reorderThreshold", 0)))
    suggestions = []
    for sku in products:
        qty = suggest_qty(forecasts.get(sku), on_hand(sku))
        if qty > 0:
            suggestions.append({"sku": sku, "qty": qty})
    return {
        "sales_count": len(completed),
        "revenue": sum(int(s.get("total", 0)) for s in completed),
        "top_sku": max(qty_by_sku, key=qty_by_sku.__getitem__) if qty_by_sku else None,
        "qty_by_sku": qty_by_sku,
        "low_stock": low,
        "open_discrepancies": open_discrepancies,
        "suggested_orders": suggestions,
    }


def _where(query, field: str, op: str, value):
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter
    except ImportError:  # no client library installed: the positional form
        return query.where(field, op, value)
    return query.where(filter=FieldFilter(field, op, value))


async def _docs(query) -> dict[str, dict]:
    return {d.id: d.to_dict() or {} async for d in query.stream()}


async def today(shop_id: str, now: datetime | None = None) -> dict:
    """Today's (IST) completed sales, stock, open delivery discrepancies and suggested orders for one shop.

    A sale counts on the IST day it was completed (`completedAt`), the same rule the Shop Dashboard uses. Both
    filters are single-field, so Firestore's automatic indexes serve them. `now` is for tests and jobs.
    """
    now = (now or datetime.now(IST)).astimezone(IST)
    start = datetime.combine(now.date(), time.min, tzinfo=IST)
    shop = db().collection("shops").document(shop_id)
    products, inventory, sales, disc = await asyncio.gather(
        _docs(shop.collection("products")),
        _docs(shop.collection("inventory")),
        _docs(_where(shop.collection("sales"), "completedAt", ">=", start)),
        _docs(_where(shop.collection("orders"), "state", "==", "DISCREPANCY")),
    )
    values = await asyncio.gather(*(forecast.daily_demand(shop_id, sku) for sku in products))
    out = summarize(list(sales.values()), products, inventory, dict(zip(products, values)), len(disc))
    out["names"] = {sku: p.get("name", sku) for sku, p in products.items()}
    return out
