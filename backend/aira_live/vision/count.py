"""Count packs in one frame (crate or shelf snapshot) with Gemini Robotics-ER 2 pointing.

Points are [y, x] normalised 0-1000. Never invent a count: when nothing valid is seen, or the reply hits
the item cap (so more packs may be in view), the result is ``count=None`` with ``needs_retake`` and an
``aim_hint`` i18n key (see ``vision/hints.py``).

Two different problems get two different hints: a pack cut off by the frame edge asks Sam to move the
phone back (``vision.aim.move_back``); a pack hidden behind another one, or packs bunched together, asks him
to spread them out (``vision.aim.spread_out``). Moving back cannot fix an overlap.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from aira_live.frames import Frame
from aira_live.vision.gemini import generate_json, image_part

DEDUPE_RADIUS = 12     # two points closer than this (0-1000 space) are the same pack
OVERLAP_RADIUS = 35    # packs this close are probably stacked or overlapping
EDGE_MARGIN = 25       # a point this close to the frame edge is probably a cut-off pack
SPREAD_RATIO = 0.15    # overlapping pairs per pack above this ask Sam to spread the packs out
MAX_ITEMS = 80         # the prompt's reply cap; a reply this long may be truncated, so it is never a count


class CountResult(BaseModel):
    count: int | None
    confidence: float
    needs_retake: bool
    aim_hint: str          # i18n key, e.g. "vision.aim.move_back"; "" when the count is usable as is


COUNT_PROMPT = (
    "You are counting stock for a blind shopkeeper. Point to EVERY individual {label} visible in this image, "
    "exactly one point per item, at the centre of each item. Do not point to anything else. "
    'For each item set "edge": true if part of it is cut off by the image edge, and '
    '"hidden": true if part of it is hidden behind another item. '
    f"Points are [y, x] normalized to 0-1000. Return no more than {MAX_ITEMS} items."
)
COUNT_SCHEMA = '[{"point": [y, x], "label": "<label>", "edge": false, "hidden": false}]'


def _is_point(p: Any) -> bool:
    return (isinstance(p, (list, tuple)) and len(p) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1000 for v in p))


def _is_true(v: Any) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def assess(items: list[dict]) -> CountResult:
    """Pure: raw model points -> deduped count, confidence and a retake hint key."""
    if isinstance(items, list) and len(items) >= MAX_ITEMS:
        # The model stopped at the cap, so packs past it were never pointed at. Ask for a closer view of
        # part of the shelf or crate rather than speak a truncated number.
        return CountResult(count=None, confidence=0.0, needs_retake=True, aim_hint="vision.aim.hold_closer")
    pts: list[tuple[float, float]] = []
    edge_flags: list[bool] = []
    hidden_flags: list[bool] = []
    for it in items if isinstance(items, list) else []:
        p = it.get("point") if isinstance(it, dict) else None
        if not _is_point(p):
            continue
        q = (float(p[0]), float(p[1]))
        if any(_dist(q, r) < DEDUPE_RADIUS for r in pts):
            continue
        pts.append(q)
        near_edge = min(q[0], q[1], 1000 - q[0], 1000 - q[1]) < EDGE_MARGIN
        # An older reply shape uses one "cut" flag for both causes: near the edge it is an edge cut,
        # anywhere else it can only be a pack hidden behind another one.
        legacy_cut = _is_true(it.get("cut"))
        edge = _is_true(it.get("edge")) or near_edge
        edge_flags.append(edge)
        hidden_flags.append(_is_true(it.get("hidden")) or (legacy_cut and not edge))
    n = len(pts)
    if n == 0:
        return CountResult(count=None, confidence=0.0, needs_retake=True, aim_hint="vision.aim.show_items")
    overlaps = sum(1 for i in range(n) for j in range(i + 1, n) if _dist(pts[i], pts[j]) < OVERLAP_RADIUS)
    hidden = sum(hidden_flags)
    overlap_ratio = min(1.0, (overlaps + hidden) / n)
    edge_ratio = sum(edge_flags) / n
    confidence = max(0.0, min(1.0, 1.0 - 0.6 * overlap_ratio - 0.4 * edge_ratio))
    if edge_ratio > 0:
        hint = "vision.aim.move_back"
    elif hidden > 0 or overlap_ratio > SPREAD_RATIO:
        hint = "vision.aim.spread_out"
    else:
        hint = ""
    return CountResult(count=n, confidence=round(confidence, 2), needs_retake=bool(hint), aim_hint=hint)


def _items_from(data: dict) -> Any:
    """The point list from a model reply: a JSON list (wrapped as "items"), {"points": [...]}, or one object."""
    for key in ("items", "points"):
        if key in data:
            return data[key]
    if "point" in data:  # a single pack answered as one object instead of a list
        return [data]
    return []


async def count_in_frame(frame: Frame, item_label: str) -> CountResult:
    """Count ``item_label`` in one frame with the `count` role (ER 2, Flash fallback).

    Raises ``VisionError`` when every model attempt fails; callers speak a retake hint instead of a number.
    """
    data = await generate_json(
        "count",
        [image_part(frame.jpeg_b64), COUNT_PROMPT.format(label=item_label)],
        COUNT_SCHEMA,
        thinking="high",
        timeout_s=12.0,
    )
    return assess(_items_from(data))
