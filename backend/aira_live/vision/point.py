"""Point to one product so the app can guide Sam's hand. [y, x] and boxes stay normalized 0–1000.

``point_to`` returns None when the target is not visible or the reply is unusable; it never invents a target.
The caller (``product_find``, plan 03) then speaks ``t("vision.aim.not_visible", lang)``. A ``TargetResult``
always fits the ``target`` ServerMsg: integer coordinates in 0..1000 and a finite confidence in 0..1.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from aira_live.frames import Frame
from aira_live.vision.gemini import generate_json, image_part

MIN_CONF = 0.3
DEFAULT_CONF = 0.5  # ER 2's list replies usually carry no confidence

POINT_PROMPT = (
    "Point to the {label} in this image. If it is not visible, return {{\"point\": null}}. "
    "Points are [y, x], normalized to 0-1000."
)
POINT_SCHEMA = '{"point": [y, x] or null, "label": "<label>", "confidence": 0.0}'


class TargetResult(BaseModel):
    label: str
    point: tuple[int, int]
    box: tuple[int, int, int, int] | None
    confidence: float


def _nums_in_range(v: Any, n: int) -> bool:
    return (isinstance(v, (list, tuple)) and len(v) == n
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x <= 1000 for x in v))


def parse_box(raw: Any) -> tuple[int, int, int, int] | None:
    """A ``box_2d`` / ``box`` [ymin, xmin, ymax, xmax] in 0-1000 -> an integer tuple; None when malformed,
    out of range, or empty (ymax <= ymin or xmax <= xmin). Shared by every ER 2 parser."""
    if not _nums_in_range(raw, 4):
        return None
    ymin, xmin, ymax, xmax = (round(v) for v in raw)
    if ymax <= ymin or xmax <= xmin:
        return None
    return (ymin, xmin, ymax, xmax)


def _confidence(v: Any) -> float:
    """Missing or unparseable -> DEFAULT_CONF; a percentage (1 < v <= 100) is scaled; above 100 -> 0 (rejected)."""
    if v is None or isinstance(v, bool):
        return DEFAULT_CONF
    try:
        f = float(v)
    except (TypeError, ValueError):
        return DEFAULT_CONF
    if not math.isfinite(f):
        return DEFAULT_CONF
    if f > 1.0:
        f = f / 100.0 if f <= 100.0 else 0.0
    return max(f, 0.0)


def parse_target(data: Any, label: str) -> TargetResult | None:
    """Turn one raw model reply into a target, or None when it is missing, malformed or too unsure."""
    if not isinstance(data, dict):
        return None
    p = data.get("point")
    if not _nums_in_range(p, 2):
        return None
    conf = _confidence(data.get("confidence"))
    if conf < MIN_CONF:
        return None
    y, x = round(p[0]), round(p[1])
    box = parse_box(data.get("box_2d") if data.get("box_2d") is not None else data.get("box"))
    if box is not None and not (box[0] <= y <= box[2] and box[1] <= x <= box[3]):
        box = None  # a box that does not contain its own point is not that object's box
    raw_label = data.get("label")
    name = raw_label.strip() if isinstance(raw_label, str) else ""
    return TargetResult(label=name or label, point=(y, x), box=box, confidence=conf)


async def point_to(frame: Frame, target_label: str) -> TargetResult | None:
    """Ask the `point` model (ER 2, Flash fallback) where the target is. Raises VisionError if every attempt fails."""
    data = await generate_json(
        "point",
        [image_part(frame.jpeg_b64), POINT_PROMPT.format(label=target_label)],
        POINT_SCHEMA,
        thinking="low",
        timeout_s=5.0,
    )
    if "point" not in data and "items" in data:  # ER 2 often answers with its list format
        items = data["items"] if isinstance(data["items"], list) else []
        for item in items:
            target = parse_target(item, target_label)
            if target is not None:
                return target
        return None
    return parse_target(data, target_label)
