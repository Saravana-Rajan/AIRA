"""Day summary for one shop (IST business day). Pure aggregation in summarize(); Firestore reads in today().

Reads `shops/{shop}/{products,inventory,sales,orders}` in database "aira" (spec §5). It only reads: nothing
here writes stock, money or state. Spoken text is English facts only: Live phrases them in the owner's language.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from datetime import datetime, time
from zoneinfo import ZoneInfo

from aira_live.analytics import forecast
from aira_live.db import db

IST = ZoneInfo("Asia/Kolkata")
SAFETY = 1.15
UNIT_WORDS = {"L": ("litre", "litres"), "pack": ("pack", "packs"), "jar": ("jar", "jars"), "box": ("box", "boxes")}
UNITS = tuple(UNIT_WORDS)         # catalog selling units with an English word
DEFAULT_UNIT = "pack"             # what business.ledger and business.expiry use for a product with no unit


def _num(x) -> str:
    return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)


def amount(qty, unit: str | None, fmt: Callable[[object], str] = _num) -> str:
    """A quantity in the product's selling unit, in English: "23 litres", "1 pack", "3 jars".

    Stock, ledger, expiry and forecast numbers are in selling units (litres for milk and curd), so a bare
    "23 Aavin Milk 500 ml" would be heard as 23 pouches when it is 46. An unknown unit is written as stored.
    """
    n = fmt(qty)
    unit = unit or DEFAULT_UNIT
    words = UNIT_WORDS.get(unit)
    if words is None:
        return f"{n} {unit}"
    return f"{n} {words[0] if n == '1' else words[1]}"


def item(qty, unit: str | None, name: str, fmt: Callable[[object], str] = _num) -> str:
    """A quantity with its unit and the product name: "23 litres of Aavin Milk 500 ml"."""
    return f"{amount(qty, unit, fmt)} of {name}"


def facts(d: dict) -> dict:
    """The day's facts for Live from a `today()` result: exact sales count and revenue (rupees), the top
    seller's name, the suggested orders (each with its product name) and the English meaning in ``say_en``."""
    names = d.get("names") or {}
    top = d.get("top_sku")
    orders = [{**o, "name": names.get(o["sku"], o["sku"])} for o in d.get("suggested_orders") or []]
    out = {"sales_count": int(d.get("sales_count") or 0), "revenue": d.get("revenue", 0),
           "top_seller": names.get(top, top) if top else None, "suggested_orders": orders,
           "demo_history": bool(d.get("demo_history"))}
    out["say_en"] = say_en(out)
    return out


def say_en(f: dict) -> str:
    """The English meaning of `facts()`. Shared by the day_summary tool and /jobs/day-summary."""
    if not f.get("sales_count"):
        text = "No sales recorded today yet."
    else:
        text = f"Today: {f['sales_count']} sales, {_num(f.get('revenue', 0))} rupees."
        if f.get("top_seller"):
            text += f" Top seller: {f['top_seller']}."
    orders = f.get("suggested_orders") or []
    if orders:
        items = ", ".join(item(o["qty"], o.get("unit"), o.get("name") or o["sku"]) for o in orders)
        if f.get("demo_history"):  # the forecast includes backfilled demo rows: never present it as real data
            text = f"{text} Based on demo sales history."
        text = f"{text} Suggested order for tomorrow: {items}."
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
    for sku, p in products.items():
        qty = suggest_qty(forecasts.get(sku), on_hand(sku))
        if qty > 0:  # qty is in the product's selling unit, so the unit travels with it
            suggestions.append({"sku": sku, "qty": qty, "unit": p.get("unit") or DEFAULT_UNIT})
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


async def _active(query) -> dict[str, dict]:
    """Products disabled by voice (``active: false``) keep their history but leave the summary."""
    return {k: v for k, v in (await _docs(query)).items() if v.get("active") is not False}


async def today(shop_id: str, now: datetime | None = None) -> dict:
    """Today's (IST) completed sales, stock, open delivery discrepancies and suggested orders for one shop.

    A sale counts on the IST day it was completed (`completedAt`), the same rule the Shop Dashboard uses. Both
    filters are single-field, so Firestore's automatic indexes serve them. `now` is for tests and jobs.
    """
    now = (now or datetime.now(IST)).astimezone(IST)
    start = datetime.combine(now.date(), time.min, tzinfo=IST)
    shop = db().collection("shops").document(shop_id)
    products, inventory, sales, disc = await asyncio.gather(
        _active(shop.collection("products")),
        _docs(shop.collection("inventory")),
        _docs(_where(shop.collection("sales"), "completedAt", ">=", start)),
        _docs(_where(shop.collection("orders"), "state", "==", "DISCREPANCY")),
    )
    values = await asyncio.gather(*(forecast.daily_demand(shop_id, sku) for sku in products))
    out = summarize(list(sales.values()), products, inventory, dict(zip(products, values)), len(disc))
    out["names"] = {sku: p.get("name", sku) for sku, p in products.items()}
    out["demo_history"] = await forecast.demo_history(shop_id)  # backfilled demo rows feed the suggestions
    return out
