"""Subscribers to vision results: whoever analyses a frame (camera_ask, describe mode, product_find) hands the
objects it found to ``on_vision_objects``; registered callbacks (last-seen memory, describe mode) consume them.

A callback never breaks the tool that called it: an exception is logged and the other callbacks still run.
Objects are the plain dicts the point / describe prompts return (``label``, ``point`` [y, x] 0-1000,
``distance`` near|arm|far, optional ``box`` and ``confidence``). ``context`` says where they came from:
``purpose`` ("ask", "describe", "find", "identify", "passive"), the owner's ``question``, the model's
``answer``, the overall ``confidence`` and an optional ``event`` ("placed" when a hand put something down).
No image bytes travel through here: callbacks only ever see text.
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

Observer = Callable[[Any, list[dict], dict], Awaitable[None] | None]
_observers: list[Observer] = []


def register(cb: Observer) -> Observer:
    """Add ``cb`` (sync or async) to the subscribers; registering twice keeps one. Returns ``cb``."""
    if cb not in _observers:
        _observers.append(cb)
    return cb


def unregister(cb: Observer) -> None:
    if cb in _observers:
        _observers.remove(cb)


def observers() -> tuple[Observer, ...]:
    return tuple(_observers)


async def on_vision_objects(session: Any, objects: list[dict], context: dict | None = None) -> None:
    """Fan ``objects`` out to every subscriber. Never raises; nothing to do for an empty list."""
    if not objects:
        return
    ctx = dict(context or {})
    for cb in list(_observers):
        try:
            res = cb(session, objects, ctx)
            if inspect.isawaitable(res):
                await res
        except Exception:  # noqa: BLE001 - one bad observer must not break the tool that saw the frame
            log.exception("vision observer %s failed", getattr(cb, "__name__", cb))
