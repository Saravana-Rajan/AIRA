"""Publish hook for the live monitor (the dashboard's Live page, another lane).

Producers call ``monitor_publish(session, msg)`` with one monitor message; the monitor hub (``/monitor``, built
separately) registers a callback with ``register`` and fans the message out to its WebSocket viewers. Nothing
is registered by default, so every call is a cheap no-op until the hub exists: ``has_hooks()`` lets a producer
skip expensive work (downscaling a frame) when nobody is watching.

Monitor contract (v1): ``{"t": "mon", "kind": ..., "session": <session id>, "ts": <epoch ms>, ...}``.
- kind ``vision``: ``source`` ("mediapipe" | "er2" | "bump"), ``objects`` ``[{label, box, point, score?,
  severity?}]`` with ``box`` ``[ymin, xmin, ymax, xmax]`` or null and ``point`` ``[y, x]`` or null, both
  0-1000; optional ``target`` (the label being looked for) and ``found``.
- kind ``frame``: a downscaled JPEG (``jpeg`` base64, ``w``, ``h``) from a shop camera (uid ``camera:<name>``).

Hooks never break the caller: each runs in turn, an exception is logged and the next one still runs.
"""
from __future__ import annotations

import base64
import inspect
import io
import logging
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

MonitorHook = Callable[[Any, dict], Any]  # sync or async; the result is awaited when awaitable

# Registered callbacks, in registration order. Initially empty: the hub registers itself when it starts.
HOOKS: list[MonitorHook] = []

FRAME_MAX_SIDE = 480     # monitor frames are small previews, never the full picture
FRAME_QUALITY = 60
NORM_MAX = 1000


def register(callback: MonitorHook) -> Callable[[], None]:
    """Subscribe ``callback(session, msg)``; returns ``unregister()``. Registering twice is once."""
    if callback not in HOOKS:
        HOOKS.append(callback)

    def unregister() -> None:
        if callback in HOOKS:
            HOOKS.remove(callback)

    return unregister


def has_hooks() -> bool:
    return bool(HOOKS)


def _session_fields(session: Any) -> dict:
    return {"session": getattr(session, "session_id", None), "uid": getattr(session, "uid", None),
            "shop": getattr(session, "shop_id", None)}


def _norm(v: Any) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if f != f or f < 0 or f > NORM_MAX:
        return None
    return round(f)


def _coords(raw: Any, n: int) -> list[int] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != n:
        return None
    out = [_norm(v) for v in raw]
    if any(v is None for v in out):
        return None
    if n == 4 and not (out[2] > out[0] and out[3] > out[1]):
        return None
    return out  # type: ignore[return-value]


def normalise_object(obj: Any) -> dict | None:
    """One producer object (ER 2 result, MediaPipe detection, pydantic dump) -> the monitor's object shape.

    ``box`` and ``point`` are each ``None`` when missing or malformed (a bad box never drops a good point);
    ``box_2d`` is accepted as a box. An object without a label, or with neither a box nor a point, is dropped.
    """
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    if not isinstance(obj, dict):
        return None
    label = str(obj.get("label") or "").strip()
    box = _coords(obj.get("box") if obj.get("box") is not None else obj.get("box_2d"), 4)
    point = _coords(obj.get("point"), 2)
    if not label or (box is None and point is None):
        return None
    out: dict[str, Any] = {"label": label, "box": box, "point": point}
    score = obj.get("score", obj.get("confidence"))
    if isinstance(score, (int, float)) and not isinstance(score, bool) and score == score:
        out["score"] = max(0.0, min(1.0, float(score)))
    if obj.get("severity") == "danger":
        out["severity"] = "danger"
    if obj.get("distance") in ("near", "arm", "far"):
        out["distance"] = obj["distance"]
    return out


def vision_message(session: Any, source: str, objects: list, *, target: str | None = None,
                   found: bool | None = None, frame_id: str | None = None, context: str | None = None) -> dict:
    """Build one kind-``vision`` monitor message. Malformed objects are dropped, never invented."""
    msg: dict[str, Any] = {"t": "mon", "kind": "vision", **_session_fields(session), "ts": round(time.time() * 1000),
                           "source": source,
                           "objects": [o for o in (normalise_object(x) for x in objects or []) if o is not None]}
    if target is not None:
        msg["target"] = target
    if found is not None:
        msg["found"] = bool(found)
    if frame_id is not None:
        msg["frameId"] = frame_id
    if context is not None:
        msg["context"] = context
    return msg


def downscale_jpeg(jpeg_b64: str, max_side: int = FRAME_MAX_SIDE, quality: int = FRAME_QUALITY) -> tuple[str, int, int]:
    """A JPEG (base64) with its longest side at most ``max_side`` -> ``(base64, w, h)``. Never upscales."""
    from PIL import Image  # lazy: only when a monitor is watching

    img = Image.open(io.BytesIO(base64.b64decode(jpeg_b64)))
    img.load()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    scale = min(1.0, max_side / max(img.width, img.height))
    if scale < 1.0:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode(), img.width, img.height


def frame_message(session: Any, jpeg_b64: str, *, frame_id: str, frame_ts: float, purpose: str,
                  downscale: bool = True) -> dict:
    """Build one kind-``frame`` monitor message (a shop camera's picture, downscaled for the preview)."""
    w = h = 0
    if downscale:
        try:
            jpeg_b64, w, h = downscale_jpeg(jpeg_b64)
        except Exception:
            log.warning("monitor frame: downscale failed; sending as is", exc_info=True)
    return {"t": "mon", "kind": "frame", **_session_fields(session), "ts": round(time.time() * 1000),
            "frameId": frame_id, "frameTs": frame_ts, "purpose": purpose, "jpeg": jpeg_b64, "w": w, "h": h}


async def monitor_publish(session: Any, msg: dict) -> int:
    """Hand one monitor message to every hook. Returns how many ran without raising. Never raises."""
    if not HOOKS:
        return 0
    ok = 0
    for cb in list(HOOKS):
        try:
            res = cb(session, dict(msg))
            if inspect.isawaitable(res):
                await res
            ok += 1
        except Exception:
            log.exception("monitor hook %s failed", getattr(cb, "__name__", cb))
    return ok


async def publish_er2(session: Any, objects: list, *, target: str | None = None, found: bool | None = None,
                      frame_id: str | None = None, context: str | None = None) -> int:
    """An ER 2 result: to the monitor (source ``er2``) and, as a ``boxes`` server message, to the phone's
    optional "Show boxes" overlay. Objects without a box are not sent to the phone. Never raises."""
    msg = vision_message(session, "er2", objects, target=target, found=found, frame_id=frame_id, context=context)
    n = await monitor_publish(session, msg)
    try:
        from aira_live.contracts import MAX_BOXES, BoxesOutMsg, BoxObject

        boxed = [o for o in msg["objects"] if o["box"] is not None][:MAX_BOXES]
        send = getattr(session, "send", None)
        if (boxed or target is not None) and callable(send):
            await send(BoxesOutMsg(
                source="er2", frame_id=frame_id, target=target, found=found,
                objects=[BoxObject(label=o["label"], box=tuple(o["box"]), score=o.get("score")) for o in boxed]))
    except Exception:
        log.warning("er2 boxes: could not send to session %s", getattr(session, "session_id", None), exc_info=True)
    return n
