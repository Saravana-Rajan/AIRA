"""Hook for every ER 2 object result: ``on_vision_objects(session, objects, context)``.

``camera_ask`` and describe mode call it after each Robotics-ER 2 answer with the parsed objects
(``[{"label", "point": [y, x], "distance": "near|arm|far", ...}]``, coordinates normalised 0-1000) and a
context dict (``source``: "camera_ask" | "describe", ``frame_id``, ``confidence``, ``kind``/``question`` when
known). The "last seen" visual memory subscribes with ``register`` and records sightings of personal items;
nothing is registered by default, and the list is kept here so vision never imports memory.

Observers never break the caller: each runs in turn, an exception is logged and the next one still runs.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

VisionObserver = Callable[[Any, list[dict], dict], Awaitable[None]]

# Registered callbacks, in registration order. Initially empty.
OBSERVERS: list[VisionObserver] = []


def register(callback: VisionObserver) -> Callable[[], None]:
    """Subscribe ``callback(session, objects, context)``; returns ``unregister()``. Registering twice is once."""
    if callback not in OBSERVERS:
        OBSERVERS.append(callback)

    def unregister() -> None:
        if callback in OBSERVERS:
            OBSERVERS.remove(callback)

    return unregister


async def on_vision_objects(session: Any, objects: list[dict], context: dict | None = None) -> int:
    """Hand one ER 2 result to every observer. Returns how many ran without raising. Never raises."""
    ctx = dict(context or {})
    ok = 0
    for cb in list(OBSERVERS):
        try:
            await cb(session, [dict(o) for o in objects], dict(ctx))
            ok += 1
        except Exception:
            log.exception("vision observer %s failed", getattr(cb, "__name__", cb))
    return ok
