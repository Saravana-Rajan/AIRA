"""Spatial memory: where each product was last recorded (spec §5 shops/{shopId}/locations/{sku}).

A saved location is not proof the product is still there: a location the model observed, or one the owner
confirmed more than 3 days ago, is stale and is only ever spoken as "may have moved".
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from aira_live import db as dbmod
from aira_live.contracts import Lang
from aira_live.i18n import t
from aira_live.memory.zones import speak_zone

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(days=3)


class Location(BaseModel):
    sku: str = Field(min_length=1)
    zone: str = Field(min_length=1)
    confirmed_by: Literal["user", "model"]
    confirmed_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("confirmed_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _doc(shop_id: str, sku: str):
    return dbmod.db().collection("shops").document(shop_id).collection("locations").document(sku)


async def remember(shop_id: str, sku: str, zone: str, confirmed_by: str, confidence: float) -> Location:
    """Save where ``sku`` is. Validates before writing (ValueError on a bad zone, source or confidence)."""
    loc = Location(sku=sku, zone=zone, confirmed_by=confirmed_by, confirmed_at=_utcnow(), confidence=confidence)
    await _doc(shop_id, sku).set({"sku": loc.sku, "zone": loc.zone, "confirmedBy": loc.confirmed_by,
                                  "confirmedAt": loc.confirmed_at, "confidence": loc.confidence})
    return loc


async def where(shop_id: str, sku: str) -> Location | None:
    """The last recorded place of ``sku``, or None (also None for a malformed doc: never guess a place)."""
    snap = await _doc(shop_id, sku).get()
    if not snap.exists:
        return None
    d = snap.to_dict() or {}
    try:
        return Location(sku=d.get("sku", sku), zone=d["zone"], confirmed_by=d["confirmedBy"],
                        confirmed_at=d["confirmedAt"], confidence=d.get("confidence", 0.0))
    except (KeyError, ValidationError):
        log.warning("ignoring malformed location doc shops/%s/locations/%s", shop_id, sku)
        return None


def is_stale(loc: Location, now: datetime) -> bool:
    """Stale = not confirmed by the owner, or confirmed more than 3 days before ``now``."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return loc.confirmed_by != "user" or (now - loc.confirmed_at) > STALE_AFTER


def describe_location(name: str, loc: Location | None, lang: Lang, now: datetime) -> str:
    """One spoken sentence: fresh place, stale place ("may have moved"), or no saved place."""
    if loc is None:
        return t("loc.none", lang, name=name)
    key = "loc.stale" if is_stale(loc, now) else "loc.fresh"
    return t(key, lang, name=name, zone=speak_zone(loc.zone, lang))
