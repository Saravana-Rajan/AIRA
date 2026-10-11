"""Episodic memory: what happened in the shop (spec §5 shops/{shopId}/memoryEvents)."""
from datetime import datetime, timezone

from aira_live import db as dbmod

MAX_RECENT = 100


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coll(shop_id: str):
    return dbmod.db().collection("shops").document(shop_id).collection("memoryEvents")


async def log(shop_id: str, type: str, text: str, ref_type: str | None = None, ref_id: str | None = None) -> str:
    """Append one shop event; returns the new event id."""
    if not text or not text.strip():
        raise ValueError("memory event text is empty")
    ref = _coll(shop_id).document()
    await ref.set({"type": type, "text": text, "refType": ref_type, "refId": ref_id, "at": _utcnow()})
    return ref.id


async def recent(shop_id: str, limit: int = 20) -> list[dict]:
    """Newest events first; each dict has id, type, text, refType, refId, at."""
    if not 1 <= limit <= MAX_RECENT:
        raise ValueError(f"limit must be between 1 and {MAX_RECENT}")
    query = _coll(shop_id).order_by("at", direction="DESCENDING").limit(limit)
    return [{"id": snap.id, **snap.to_dict()} async for snap in query.stream()]
