"""Shop jobs: expiry scan, day summary and forecast refresh. OWNER: Saravana (saravana/02, Task 5).

Build overrides §1: there is no Cloud Scheduler and no FCM. The routes are called by hand (curl), by an
external cron, or by the app at startup (the "morning check"). Every route needs header ``X-AIRA-Job-Token``
equal to env ``AIRA_JOB_TOKEN``, compared in constant time. When that env is unset or empty the routes are
disabled (503, fail closed). The check is a router dependency, so a rejected call does no work at all.

Alerts are spoken in the owner's live session via ``sessions.notify``. The spoken text is also returned in
``messages``, so a caller with no live session (the app's morning check) can speak it itself.
Jobs only read and speak. They never write stock, money or state; the one write is an append-only
``alerts`` analytics event, and a failure there never blocks the spoken alert.
"""
from __future__ import annotations

import hmac
import logging
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from aira_live import config, sessions
from aira_live.analytics import events, forecast, summary
from aira_live.db import db
from aira_live.i18n import t

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
TOKEN_HEADER = "X-AIRA-Job-Token"
LANGS = ("ta", "hi", "en")
DEFAULT_LANG = "en"
Lang = Literal["ta", "hi", "en"]
SHOP_ID = r"^[A-Za-z0-9_-]{1,64}$"  # a Firestore doc id with no "/" (no path injection)


def require_job_token(token: str | None = Header(default=None, alias=TOKEN_HEADER)) -> None:
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


def _known_lang(value) -> str | None:
    return value if value in LANGS else None


async def _shop_lang(client, data: dict) -> str:
    """The owner's language (``users/{ownerUid}.lang``), then the shop doc's ``lang``, then English."""
    owner = data.get("ownerUid")
    if owner:
        user = await client.collection("users").document(owner).get()
        if user.exists:
            lang = _known_lang((user.to_dict() or {}).get("lang"))
            if lang:
                return lang
    return _known_lang(data.get("lang")) or DEFAULT_LANG


async def _shops() -> list[tuple[str, str]]:
    """(shop_id, language) for every shop. Mock mode has only the demo shop and reads no Firestore."""
    settings = config.load()
    if settings.mock:
        return [(settings.demo_shop_id, DEFAULT_LANG)]
    client = db()
    return [(snap.id, await _shop_lang(client, snap.to_dict() or {}))
            async for snap in client.collection("shops").stream()]


async def _lang_of(shop_id: str) -> str:
    if config.load().mock:
        return DEFAULT_LANG
    client = db()
    snap = await client.collection("shops").document(shop_id).get()
    return await _shop_lang(client, snap.to_dict() or {}) if snap.exists else DEFAULT_LANG


async def _targets(shop_id: str | None, lang: str | None) -> list[tuple[str, str]]:
    if shop_id:
        return [(shop_id, lang or await _lang_of(shop_id))]
    return [(sid, lang or shop_lang) for sid, shop_lang in await _shops()]


def _num(x) -> str:
    return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)


def _item(alert: dict) -> str:
    """'6 Sakthi curd', or just the name when the scan gave no quantity (never invent one)."""
    name = alert.get("name") or alert["sku"]
    qty = alert.get("qty")
    return name if qty is None or qty == "" else f"{_num(qty)} {name}"


def _status(alert: dict) -> str:
    days = alert.get("daysLeft")
    return "expired" if isinstance(days, (int, float)) and days < 0 else "expiring"


def _expiry_text(alerts: list[dict], lang: str) -> str:
    """Expired items first (take them off the shelf), then items expiring soon."""
    parts = []
    for status, key in (("expired", "jobs.expired_alert"), ("expiring", "jobs.expiry_alert")):
        items = [_item(a) for a in alerts if _status(a) == status]
        if items:
            parts.append(t(key, lang, items=", ".join(items)))
    return " ".join(parts)


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
async def job_expiry(shop_id: str | None = Query(default=None, pattern=SHOP_ID),
                     lang: Lang | None = Query(default=None)) -> dict:
    """Scan every shop (or one) for expired or soon-expiring stock and speak one alert per shop."""
    today = _today()
    shops = total = notified = errors = 0
    messages: list[dict] = []
    for sid, lng in await _targets(shop_id, lang):
        shops += 1
        try:
            alerts = await _scan(sid, today)
            if not alerts:
                continue
            text = _expiry_text(alerts, lng)
            notified += await sessions.notify(sid, text, cue="warn")
        except Exception:
            log.exception("expiry job failed for shop %s", sid)
            errors += 1
            continue
        total += len(alerts)
        messages.append({"shop_id": sid, "say": text})
        await _publish_alerts(sid, alerts, text)
    return {"shops": shops, "alerts": total, "notified_sessions": notified, "errors": errors,
            "messages": messages}


@router.post("/day-summary")
async def job_day_summary(shop_id: str | None = Query(default=None, pattern=SHOP_ID),
                          lang: Lang | None = Query(default=None)) -> dict:
    """Speak today's sales, revenue, top seller and tomorrow's suggested order (same words as day_summary)."""
    shops = notified = errors = 0
    messages: list[dict] = []
    for sid, lng in await _targets(shop_id, lang):
        shops += 1
        try:
            text = summary.say(await summary.today(sid), lng)
            notified += await sessions.notify(sid, text, cue="done")
        except Exception:  # no numbers when the records can't be read
            log.exception("day-summary job failed for shop %s", sid)
            errors += 1
            continue
        messages.append({"shop_id": sid, "say": text})
    return {"shops": shops, "notified_sessions": notified, "errors": errors, "messages": messages}


@router.post("/forecast-refresh")
async def job_forecast_refresh(shop_id: str | None = Query(default=None, pattern=SHOP_ID)) -> dict:
    """Drop the cached 7-day windows and recompute (default: the demo shop)."""
    result = await forecast.refresh_model(shop_id)
    return {"ok": True, "skus": len(result), "with_forecast": sum(v is not None for v in result.values())}
