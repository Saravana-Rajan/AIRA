"""Read-only HTTP API for the Shop Dashboard (mounted by main.py through OPTIONAL_ROUTERS).

GET /api/forecast?shop=<id> -> {"items": [{sku, name, dailyDemand, onHand, suggestedOrder}, ...],
                                "demoHistory": bool}
  The dashboard's Forecast card (frontend/dashboard/src/lib/forecast.ts). Only the demo shop is served; any other
  shop id is refused (403). A SKU with no forecast (not enough sales history, mock mode) is left out, so the
  card never shows an invented number. Numbers are in the product's selling unit (litres for milk and curd).
  ``demoHistory`` is true when backfilled demo sales (shared/scripts/backfill_demo_sales.py, rows labelled demo:true)
  feed the numbers; the card then says "demo history" instead of presenting them as real shop sales.

Everything here only reads: the forecast window (analytics.forecast), the product names and the stock on hand
(business.ledger). Browser calls are allowed from AIRA_ALLOWED_ORIGINS only (CORS in main.py).
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from aira_live import config
from aira_live.analytics import forecast, summary
from aira_live.business import ledger, paths
from aira_live.business.store import get_store

log = logging.getLogger(__name__)
router = APIRouter()

SHOP_ID = r"^[A-Za-z0-9_-]{1,64}$"  # a Firestore doc id with no "/" (no path injection)


@router.get("/api/forecast")
async def forecast_items(shop: Annotated[str, Query(pattern=SHOP_ID)]) -> dict:
    if shop != config.load().demo_shop_id:
        raise HTTPException(status_code=403, detail="only the demo shop is served")
    demand = await forecast.daily_demand_all(shop)  # {} in mock mode or when Firestore can't be read
    demo = await forecast.demo_history(shop)
    try:
        rows = await get_store().list(paths.products(shop))
        names = {sku: (doc or {}).get("name") or sku for sku, doc in rows}
        items = []
        for sku in sorted(demand):
            daily = demand[sku]
            if daily is None:
                continue
            on_hand = float(await ledger.on_hand(shop, sku))
            items.append({"sku": sku, "name": names.get(sku, sku), "dailyDemand": daily, "onHand": on_hand,
                          "suggestedOrder": summary.suggest_qty(daily, on_hand)})
    except Exception as exc:  # the dashboard shows "Forecast unavailable"; never a half-made list
        log.warning("forecast api: read failed for %s: %s", shop, exc)
        raise HTTPException(status_code=503, detail="forecast unavailable") from None
    return {"items": items, "demoHistory": demo}
