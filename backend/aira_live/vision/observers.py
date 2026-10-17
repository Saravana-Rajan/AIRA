"""Subscribers to vision results: ``on_vision_objects(session, objects, context)``.

Whoever analyses a frame (``camera_ask``, describe mode, ``product_find`` / the shelf listing) hands the parsed
objects (``[{"label", "point": [y, x], "distance": "near|arm|far", ...}]``, coordinates normalised 0-1000) to
this hook after each Robotics-ER 2 answer; registered callbacks (the "last seen" memory of personal items,
describe mode) consume them. ``context`` says where they came from: ``source`` ("camera_ask", "describe",
"product_find"), ``purpose`` ("ask", "describe", "find", "identify", "passive"), ``kind``/``question`` and the
model's ``answer`` when known, ``frame_id``/``frame_ts``, the overall ``confidence`` and an optional ``event``
("placed" when a hand put something down). A caller may pass just a string: it becomes ``source``/``purpose``.

Nothing is registered by default and the list is kept here so vision never imports memory. No image bytes
travel through here: callbacks only ever see text. An observer never breaks the tool that saw the frame:
each runs in turn (sync or async), an exception is logged and the next one still runs.
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

VisionObserver = Callable[[Any, list[dict], dict], Awaitable[None] | None]
Observer = VisionObserver

# Registered callbacks, in registration order. Initially empty.
OBSERVERS: list[VisionObserver] = []

_PURPOSE = {"camera_ask": "ask", "product_find": "find", "product_identify": "identify"}


def register(callback: VisionObserver) -> Callable[[], None]:
    """Subscribe ``callback(session, objects, context)`` (sync or async); returns ``unregister()``.
    Registering twice is once."""
    if callback not in OBSERVERS:
        OBSERVERS.append(callback)

    def off() -> None:
        unregister(callback)

    return off


def unregister(callback: VisionObserver) -> None:
    if callback in OBSERVERS:
        OBSERVERS.remove(callback)


def observers() -> tuple[VisionObserver, ...]:
    return tuple(OBSERVERS)


def normalize_context(context: Any) -> dict:
    """A dict copy with both ``source`` and ``purpose`` filled in; a bare string names the source."""
    if isinstance(context, str):
        ctx: dict = {"source": context}
    elif isinstance(context, dict):
        ctx = dict(context)
    else:
        ctx = {}
    source, purpose = ctx.get("source"), ctx.get("purpose")
    if source and not purpose:
        ctx["purpose"] = _PURPOSE.get(source, source)
    elif purpose and not source:
        ctx["source"] = next((k for k, v in _PURPOSE.items() if v == purpose), purpose)
    return ctx


async def on_vision_objects(session: Any, objects: list[dict], context: Any = None) -> int:
    """Hand one ER 2 result to every observer (each gets its own copies). Returns how many ran without
    raising. Never raises."""
    ctx = normalize_context(context)
    objs = [dict(o) if isinstance(o, dict) else o for o in (objects or [])]
    ok = 0
    for cb in list(OBSERVERS):
        try:
            res = cb(session, [dict(o) if isinstance(o, dict) else o for o in objs], dict(ctx))
            if inspect.isawaitable(res):
                await res
            ok += 1
        except Exception:  # noqa: BLE001 - one bad observer must not break the tool that saw the frame
            log.exception("vision observer %s failed", getattr(cb, "__name__", cb))
    return ok
