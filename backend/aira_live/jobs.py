"""Shop jobs: expiry scan, day summary and forecast refresh. OWNER: Saravana (saravana/02, Task 5).

Build overrides §1: there is no Cloud Scheduler and no FCM. The routes are called by hand (curl), by an
external cron, or by the app at startup (the "morning check"). Every route needs header ``X-AIRA-Job-Token``
equal to env ``AIRA_JOB_TOKEN``, compared in constant time. When that env is unset or empty the routes are
disabled (503, fail closed). The check is a router dependency, so a rejected call does no work at all.

Alerts are English facts (exact quantities, units, product names, days left) handed to the owner's live
session via ``sessions.notify``; Gemini Live says them in whatever language the owner is speaking. The same
facts are returned in ``messages`` (with the English meaning in ``say_en``), so a caller with no live session
(the app's morning check) can show or speak them itself.
Jobs only read and speak. They never write stock, money or state; the one write is an append-only
``alerts`` analytics event, and a failure there never blocks the spoken alert.
"""
from __future__ import annotations

import hmac
import logging
from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from aira_live import config, sessions
from aira_live.analytics import events, forecast, summary
from aira_live.db import db

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
TOKEN_HEADER = "X-AIRA-Job-Token"
SHOP_ID = r"^[A-Za-z0-9_-]{1,64}$"  # a Firestore doc id with no "/" (no path injection)
JobToken = Annotated[str | None, Header(alias=TOKEN_HEADER)]
ShopIdParam = Annotated[str | None, Query(pattern=SHOP_ID)]


def require_job_token(token: JobToken = None) -> None:
    """503 when AIRA_JOB_TOKEN is unset, 401 without the header, 403 when it does not match."""
    expected = config.load().job_token
    if not expected:
        raise HTTPException(503, "jobs disabled: AIRA_JOB_TOKEN is not set")
    if not token:
        raise HTTPException(401, f"missing {TOKEN_HEADER} header")
    if not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(403, "bad job token")


router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_job_token)])


def _today() -> date:
    return datetime.now(IST).date()


async def _scan(shop_id: str, today: date) -> list[dict]:
    """Kanish's ``business.expiry.scan``, imported at call time so this router loads while that lane lands."""
    from aira_live.business import expiry

    return await expiry.scan(shop_id, today)


async def _shops() -> list[str]:
    """Every shop id. Mock mode has only the demo shop and reads no Firestore."""
    settings = config.load()
    if settings.mock:
        return [settings.demo_shop_id]
    return [snap.id async for snap in db().collection("shops").stream()]


async def _targets(shop_id: str | None) -> list[str]:
    return [shop_id] if shop_id else await _shops()


def _item(alert: dict) -> str:
    """'6 litres of Sakthi curd' (the qty is in the alert's selling unit), or just the name when the scan gave no
    quantity (never invent one)."""
    name = alert.get("name") or alert["sku"]
    qty = alert.get("qty")
    return name if qty is None or qty == "" else summary.item(qty, alert.get("unit"), name)


def _status(alert: dict) -> str:
    days = alert.get("daysLeft")
    return "expired" if isinstance(days, (int, float)) and days < 0 else "expiring"


def _alert_fact(alert: dict) -> dict:
    qty = alert.get("qty")
    return {"sku": alert.get("sku"), "name": alert.get("name") or alert.get("sku"),
            "qty": None if qty == "" else qty, "unit": alert.get("unit"), "days_left": alert.get("daysLeft")}


def _expiry_facts(alerts: list[dict]) -> dict:
    """Expired items first (take them off the shelf), then items expiring soon, with the English meaning."""
    expired = [a for a in alerts if _status(a) == "expired"]
    expiring = [a for a in alerts if _status(a) == "expiring"]
    parts = []
    if expired:
        parts.append(f"Expired: {', '.join(_item(a) for a in expired)}. Take them off the shelf.")
    if expiring:
        parts.append(f"Expiring soon: {', '.join(_item(a) for a in expiring)}. "
                     "Sell them first or return them to the vendor.")
    return {"expired": [_alert_fact(a) for a in expired], "expiring": [_alert_fact(a) for a in expiring],
            "say_en": " ".join(parts)}


async def _publish_alerts(shop_id: str, alerts: list[dict], text: str) -> None:
    """Analytics only: a failed append must not undo or block the spoken alert."""
    for a in alerts:
        try:
            await events.publish("alerts", {"shop_id": shop_id, "kind": "expiry", "sku": a.get("sku"),
                                            "qty": a.get("qty"), "days_left": a.get("daysLeft"),
                                            "status": _status(a), "message": text})
        except Exception:
            log.exception("could not record expiry alert event for shop %s", shop_id)


@router.post("/expiry")
async def job_expiry(shop_id: ShopIdParam = None) -> dict:
    """Scan every shop (or one) for expired or soon-expiring stock and speak one alert per shop."""
    today = _today()
    shops = total = notified = errors = 0
    messages: list[dict] = []
    for sid in await _targets(shop_id):
        shops += 1
        try:
            alerts = await _scan(sid, today)
            if not alerts:
                continue
            facts = _expiry_facts(alerts)
            notified += await sessions.notify(sid, facts["say_en"], cue="warn")
        except Exception:
            log.exception("expiry job failed for shop %s", sid)
            errors += 1
            continue
        total += len(alerts)
        messages.append({"shop_id": sid, **facts})
        await _publish_alerts(sid, alerts, facts["say_en"])
    return {"shops": shops, "alerts": total, "notified_sessions": notified, "errors": errors,
            "messages": messages}


@router.post("/day-summary")
async def job_day_summary(shop_id: ShopIdParam = None) -> dict:
    """Speak today's sales, revenue, top seller and tomorrow's suggested order (same facts as day_summary)."""
    shops = notified = errors = 0
    messages: list[dict] = []
    for sid in await _targets(shop_id):
        shops += 1
        try:
            facts = summary.facts(await summary.today(sid))
            notified += await sessions.notify(sid, facts["say_en"], cue="done")
        except Exception:  # no numbers when the records can't be read
            log.exception("day-summary job failed for shop %s", sid)
            errors += 1
            continue
        messages.append({"shop_id": sid, **facts})
    return {"shops": shops, "notified_sessions": notified, "errors": errors, "messages": messages}


@router.post("/forecast-refresh")
async def job_forecast_refresh(shop_id: ShopIdParam = None) -> dict:
    """Drop the cached 7-day windows and recompute (default: the demo shop)."""
    result = await forecast.refresh_model(shop_id)
    return {"ok": True, "skus": len(result), "with_forecast": sum(v is not None for v in result.values())}
