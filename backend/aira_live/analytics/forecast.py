"""Per-SKU daily demand: the 7-day moving average of SALE rows in the Firestore ledger.

Source of truth is `shops/{shop}/ledger/{txnId}` in database "aira" (spec §5). A sale writes a row with
reason "SALE" and a negative `delta` in selling units (litres for milk and curd, else packs/jars/boxes).
A business day is the Asia/Kolkata date of the row's `at`. Today is still a partial day, so the window is
the 7 full days before today.

This build has no BigQuery or ARIMA (build overrides §1). With fewer than MIN_DAYS days of shop sales in
the window, `daily_demand` returns None and callers say they don't have enough data instead of guessing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from zoneinfo import ZoneInfo

from aira_live import config

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
WINDOW_DAYS = 7
MIN_DAYS = 3
CACHE_TTL_S = 60.0
SALE = "SALE"
# shared/scripts/backfill_demo_sales.py writes invented SALE rows labelled ``demo: true`` (refType "demo_backfill"), so
# the demo shop has a forecast on demo day. They feed the number like any sale, but the window remembers that
# they did (``demo_history``), and every suggestion built on them is spoken and shown as "demo history"
# (repo rule: never invent counts or amounts; nothing fabricated is presented as real).
DEMO_REF_TYPE = "demo_backfill"


def is_demo_row(row: Mapping) -> bool:
    return row.get("demo") is True or row.get("refType") == DEMO_REF_TYPE


Daily = dict[date, float]
# ({sku: {day: units sold}}, days on which the shop sold anything, whether any demo row contributed)
Window = tuple[dict[str, Daily], set[date], bool]


def ist_day(at: object) -> date | None:
    """IST business day of a ledger `at`: an ISO-8601 string or a datetime. Naive values are UTC."""
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at.strip())
        except ValueError:
            return None
    if not isinstance(at, datetime):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(IST).date()


def fold_sales(rows: Iterable[Mapping]) -> Window:
    """Ledger rows → ({sku: {day: units sold}}, days on which the shop sold anything, demo rows seen).
    Non-SALE rows are ignored."""
    by_sku: dict[str, Daily] = defaultdict(dict)
    active: set[date] = set()
    demo = False
    for row in rows:
        if row.get("reason") != SALE:
            continue
        sku, day = row.get("sku"), ist_day(row.get("at"))
        try:
            units = -float(row.get("delta", 0.0))
        except (TypeError, ValueError):
            continue
        if not sku or day is None:
            continue
        active.add(day)
        demo = demo or is_demo_row(row)
        by_sku[sku][day] = by_sku[sku].get(day, 0.0) + units
    return dict(by_sku), active, demo


def moving_average_7(daily: Mapping[date, float], today: date, active_days: Iterable[date] = ()) -> float | None:
    """Average units/day over the 7 full days before `today`.

    A day counts as data when this SKU sold that day or the shop sold anything that day (`active_days`), so a
    slow mover's zero days still count as zero. Fewer than MIN_DAYS data days → None. A shop with a shorter
    history is averaged over the days since its first data day in the window, not over 7.
    """
    window = [today - timedelta(days=i) for i in range(1, WINDOW_DAYS + 1)]
    seen = set(daily) | set(active_days)
    data_days = [d for d in window if d in seen]
    if len(data_days) < MIN_DAYS:
        return None
    span = (today - min(data_days)).days
    total = sum(max(float(daily.get(d, 0.0)), 0.0) for d in window)
    return round(total / span, 2)


# ---------- Firestore ----------

def _db():
    from aira_live.db import db
    return db()


def _today() -> date:
    return datetime.now(IST).date()


def _enabled(ops: bool = False) -> bool:
    """The service reads the ledger only outside mock mode with a project. ``ops=True`` (the CLI) reads whenever
    Firestore is configured (an emulator or a project): an operator's shell with ADC has no Gemini key."""
    settings = config.load()
    project = bool(getattr(settings, "gcp_project", None))
    if ops:
        return project or bool(os.getenv("FIRESTORE_EMULATOR_HOST"))
    return not getattr(settings, "mock", False) and project


def _at_since(col, bound):
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter
    except ImportError:  # no SDK installed (offline unit tests with a fake client)
        return col.where("at", ">=", bound)
    return col.where(filter=FieldFilter("at", ">=", bound))


async def _rows(query) -> list[dict]:
    return [snap.to_dict() or {} async for snap in query.stream()]


async def _product_skus(shop_id: str) -> set[str]:
    """SKUs of the shop's products: the `sku` field, else the doc id (products/{sku}, spec §5)."""
    col = _db().collection("shops").document(shop_id).collection("products")
    return {doc.get("sku") or snap.id async for snap in col.stream()
            if (doc := snap.to_dict() or {}).get("active") is not False}      # disabled by voice: no forecast


async def _fetch_window(shop_id: str, today: date) -> Window:
    """Every ledger row since the window start, in one pass. business.ledger writes `at` as an ISO-8601 UTC
    string; a Firestore Timestamp is read too. Firestore compares values only within one type, so each
    form gets its own single-field range query (automatic index, no composite index needed)."""
    start = datetime.combine(today - timedelta(days=WINDOW_DAYS), dtime(0), IST).astimezone(timezone.utc)
    col = _db().collection("shops").document(shop_id).collection("ledger")
    parts = await asyncio.gather(_rows(_at_since(col, start.isoformat())), _rows(_at_since(col, start)))
    return fold_sales(row for part in parts for row in part)


_cache: dict[tuple[str, date], tuple[float, asyncio.Future]] = {}


def clear_cache() -> None:
    _cache.clear()


async def _window(shop_id: str, today: date) -> Window:
    """The shop's sales window, read once per CACHE_TTL_S and shared by concurrent callers."""
    key, now = (shop_id, today), time.monotonic()
    hit = _cache.get(key)
    if hit is not None and hit[0] > now:
        fut = hit[1]
    else:
        for stale in [k for k, (expires, _) in _cache.items() if expires <= now]:
            del _cache[stale]
        fut = asyncio.ensure_future(_fetch_window(shop_id, today))
        _cache[key] = (now + CACHE_TTL_S, fut)
    try:
        return await asyncio.shield(fut)
    except Exception:
        if _cache.get(key, (0.0, None))[1] is fut:
            del _cache[key]
        raise


async def daily_demand(shop_id: str, sku: str) -> float | None:
    """Units of `sku` sold per day (7-day moving average), or None without enough data, in mock mode or when
    Firestore can't be read."""
    if not _enabled():
        return None
    today = _today()
    try:
        by_sku, active, _ = await _window(shop_id, today)
    except Exception as exc:  # never break a voice tool over analytics
        log.warning("forecast: ledger read failed for %s: %s", shop_id, exc)
        return None
    return moving_average_7(by_sku.get(sku, {}), today, active)


async def demo_history(shop_id: str) -> bool:
    """True when a backfilled demo row (``is_demo_row``) is in the shop's forecast window, so whatever is said or
    shown from the forecast must be marked "demo history". False in mock mode or when Firestore can't be read."""
    if not _enabled():
        return False
    try:
        return (await _window(shop_id, _today()))[2]
    except Exception as exc:
        log.warning("forecast: ledger read failed for %s: %s", shop_id, exc)
        return False


async def daily_demand_all(shop_id: str, *, ops: bool = False) -> dict[str, float | None]:
    """Demand for every product of the shop, plus any SKU that sold in the window. ``ops``: see ``_enabled``."""
    if not _enabled(ops):
        return {}
    today = _today()
    try:
        skus = await _product_skus(shop_id)
        by_sku, active, _ = await _window(shop_id, today)
    except Exception as exc:
        log.warning("forecast: Firestore read failed for %s: %s", shop_id, exc)
        return {}
    return {sku: moving_average_7(by_sku.get(sku, {}), today, active) for sku in sorted(skus | set(by_sku))}


async def refresh_model(shop_id: str | None = None, *, ops: bool = False) -> dict[str, float | None]:
    """Drop cached windows and recompute every SKU (default: the demo shop). Used by /jobs/forecast-refresh;
    the name is kept from the plan, but there is no model to train in this build."""
    clear_cache()
    return await daily_demand_all(shop_id or config.load().demo_shop_id, ops=ops)


def _main(argv: list[str]) -> int:
    """``python -m aira_live.analytics.forecast [shop_id]``: print every SKU's forecast (reads in mock mode too)."""
    import json

    shop_arg = argv[0] if argv else None
    print(json.dumps(asyncio.run(refresh_model(shop_arg, ops=True)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_main(sys.argv[1:]))
