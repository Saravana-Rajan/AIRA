"""Business events. Publish only AFTER the Firestore transaction commits.

Minimal setup (build overrides §1): no Pub/Sub and no BigQuery. Each event is appended to
Firestore ``shops/{shop_id}/events/{autoId}`` as ``{topic, payload, ts}`` in database "aira".
The payload is checked first: an unknown topic or a missing required field raises EventError
and nothing is written. Events are append-only; nothing here updates or deletes them.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from aira_live import config

Topic = Literal["sales", "inventory-movements", "orders", "discrepancies", "alerts"]

# topic -> table name. Kept for the interfaces contract; the minimal setup has no BigQuery.
TOPICS: dict[str, str] = {
    "sales": "sales",
    "inventory-movements": "inventory_movements",
    "orders": "orders",
    "discrepancies": "discrepancies",
    "alerts": "alerts",
}
REQUIRED: dict[str, set[str]] = {
    "sales": {"shop_id", "sale_id", "sku", "qty"},
    "inventory-movements": {"shop_id", "txn_id", "sku", "delta", "reason"},
    "orders": {"shop_id", "order_id", "state"},
    "discrepancies": {"shop_id", "delivery_id", "field"},
    "alerts": {"shop_id", "kind", "message"},
}
EVENTS_COLLECTION = "events"


class EventError(ValueError):
    """Unknown topic, a payload that is not a dict, a missing required field, or a bad shop_id."""


def _db():
    # Imported lazily so this module loads without a Firestore client (tests use the shared fake_db).
    from aira_live.db import db

    return db()


def _validate(topic: str, payload: dict) -> None:
    if topic not in TOPICS:
        raise EventError(f"unknown topic {topic!r}")
    if not isinstance(payload, dict):
        raise EventError(f"{topic} payload must be a dict")
    missing = {k for k in REQUIRED[topic] if payload.get(k) is None}
    if missing:
        raise EventError(f"{topic} payload missing {sorted(missing)}")
    shop_id = payload["shop_id"]
    if not isinstance(shop_id, str) or not shop_id or "/" in shop_id:
        raise EventError(f"{topic} payload has invalid shop_id {shop_id!r}")


def build_event(topic: str, payload: dict, now: datetime | None = None) -> dict:
    """A checked, JSON-safe copy of the payload, with ``ts`` (ISO UTC) added when absent."""
    _validate(topic, payload)
    body = dict(payload)
    body.setdefault("ts", (now or datetime.now(timezone.utc)).isoformat())
    return json.loads(json.dumps(body, ensure_ascii=False, default=str))


def build_message(topic: str, payload: dict) -> bytes:
    return json.dumps(build_event(topic, payload), ensure_ascii=False).encode()


async def publish(topic: Topic, payload: dict) -> str:
    """Append the event to ``shops/{shop_id}/events``. Returns the new doc id, or "mock".

    "mock" is returned (after the payload check) in mock mode or when GCP_PROJECT is unset.
    """
    now = datetime.now(timezone.utc)
    body = build_event(topic, payload, now)
    settings = config.load()
    if settings.mock or not settings.gcp_project:
        return "mock"
    ref = _db().collection("shops").document(body["shop_id"]).collection(EVENTS_COLLECTION).document()
    await ref.set({"topic": topic, "payload": body, "ts": now})
    return ref.id
