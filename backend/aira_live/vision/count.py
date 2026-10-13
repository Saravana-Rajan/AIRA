"""Count packs in one frame (crate or shelf snapshot) with Gemini Robotics-ER 2 pointing.

The official Robotics-ER counting method: ask for ONE POINT PER VISIBLE ITEM ("Point to each milk pouch ...
label each by its product name"), points [y, x] normalised 0-1000, and count the points left after merging
any two closer than ``DEDUPE_RADIUS``. The item is named generically ("curd packet") so a pack whose print is
only half readable is still pointed at; when the owner named a product ("Hatsun curd") the count keeps only
the points whose label names it, and unreadable labels make the count "not sure".

Never invent a count: when nothing valid is seen, the reply hits the item cap or the output cap (so more packs
may be in view), the result is ``count=None`` with ``needs_retake`` and an ``aim_hint`` code (see
``vision/hints.py``).

Two different problems get two different hints: a pack cut off by the frame edge asks Sam to move the
phone back (``vision.aim.move_back``); a pack hidden behind another one, or packs bunched together, asks him
to spread them out (``vision.aim.spread_out``). Moving back cannot fix an overlap.
"""
from __future__ import annotations

import math
import re
from typing import Any

from pydantic import BaseModel

from aira_live.frames import Frame
from aira_live.vision.gemini import TruncatedReply, generate_count_json, image_part
from aira_live.vision.point import parse_box

DEDUPE_RADIUS = 25     # two points closer than this (0-1000 space) are the same pack
OVERLAP_RADIUS = 35    # packs this close are probably stacked or overlapping
EDGE_MARGIN = 25       # a point this close to the frame edge is probably a cut-off pack
SPREAD_RATIO = 0.15    # overlapping pairs per pack above this ask Sam to spread the packs out
MAX_ITEMS = 80         # the prompt's reply cap; a reply this long may be truncated, so it is never a count
COUNT_TIMEOUT_S = 25.0  # per attempt (one ER retry, no Flash fallback)
COUNT_THINKING = "medium"
UNKNOWN_LABELS = {"", "unknown", "unreadable", "unclear", "n/a", "none", "?"}
_WORD = re.compile(r"[\w']+", re.U)


class CountResult(BaseModel):
    count: int | None
    confidence: float
    needs_retake: bool
    aim_hint: str          # hint code, e.g. "vision.aim.move_back"; "" when the count is usable as is
    # The deduped packs as the monitor draws them: {"label", "point": [y, x], "box": [ymin, xmin, ymax, xmax] | None}
    objects: list[dict] = []
    model: str | None = None   # the model that answered (diagnostics)
    # Evidence behind the number (additive): points whose label could not be read (with a named product they are
    # left out of ``count`` and cap the confidence; without one they are counted), counted packs flagged as partly
    # hidden behind another, and counted packs cut by / touching the frame edge.
    unknown: int = 0
    hidden: int = 0
    edge: int = 0


COUNT_PROMPT = (
    "Point to each {label} in the image, exactly one point per item, at the centre of each visible item, "
    "including items that are partly hidden or only partly in view. Do not point to anything else. "
    "Label each by its product name (brand and product). When a pack's print is not readable but it has the same "
    "colour, design and logo as a readable pack of that product in the image, give it that product name. Use "
    "\"unknown\" only when the pack matches no readable product. The answer follows the format: "
    '{{"items": [{{"point": [y, x], "label": "<product name>"}}]}}. '
    f"Points are [y, x] normalized to 0-1000. Return no more than {MAX_ITEMS} items."
)
COUNT_SCHEMA = '{"items": [{"point": [y, x], "label": "<product name>"}]}'


def _is_point(p: Any) -> bool:
    return (isinstance(p, (list, tuple)) and len(p) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1000 for v in p))


def _is_true(v: Any) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.casefold()) if len(w) > 1}


def _label_state(label: str, product_words: set[str]) -> str:
    """"match", "other" or "unknown" for one item label against the named product's words."""
    if label.strip().casefold() in UNKNOWN_LABELS:
        return "unknown"
    return "match" if product_words <= _words(label) else "other"


def assess(items: list[dict], product: str | None = None) -> CountResult:
    """Pure: raw model points -> deduped count, confidence and a retake hint key.

    With ``product``, only items whose label contains every word of it are counted; an unreadable label among
    them makes the result "not sure" (confidence below 0.5) since it may be one more of that product.
    """
    if isinstance(items, list) and len(items) >= MAX_ITEMS:
        # The model stopped at the cap, so packs past it were never pointed at. Ask for a closer view of
        # part of the shelf or crate rather than speak a truncated number.
        return CountResult(count=None, confidence=0.0, needs_retake=True, aim_hint="vision.aim.hold_closer")
    product_words = _words(product or "")
    pts: list[tuple[float, float]] = []
    edge_flags: list[bool] = []
    hidden_flags: list[bool] = []
    objects: list[dict] = []
    unknown = 0
    unread = 0
    for it in items if isinstance(items, list) else []:
        p = it.get("point") if isinstance(it, dict) else None
        if not _is_point(p):
            continue
        q = (float(p[0]), float(p[1]))
        label = str(it.get("label") or "").strip()
        if product_words:
            state = _label_state(label, product_words)
            if state != "match":
                unknown += state == "unknown"
                continue
        if any(_dist(q, r) < DEDUPE_RADIUS for r in pts):
            continue
        if not product_words and label.casefold() in UNKNOWN_LABELS:
            unread += 1  # counted (any pack counts), but reported: its product could not be read
        pts.append(q)
        box = parse_box(it.get("box_2d") if it.get("box_2d") is not None else it.get("box"))
        objects.append({"label": label, "point": [round(q[0]), round(q[1])], "box": list(box) if box else None})
        near_edge = min(q[0], q[1], 1000 - q[0], 1000 - q[1]) < EDGE_MARGIN
        # An older reply shape uses one "cut" flag for both causes: near the edge it is an edge cut,
        # anywhere else it can only be a pack hidden behind another one.
        legacy_cut = _is_true(it.get("cut"))
        edge = _is_true(it.get("edge")) or near_edge
        edge_flags.append(edge)
        hidden_flags.append(_is_true(it.get("hidden")) or (legacy_cut and not edge))
    n = len(pts)
    if n == 0:
        hint = "vision.aim.hold_closer" if unknown else "vision.aim.show_items"
        return CountResult(count=None, confidence=0.0, needs_retake=True, aim_hint=hint, unknown=unknown)
    overlaps = sum(1 for i in range(n) for j in range(i + 1, n) if _dist(pts[i], pts[j]) < OVERLAP_RADIUS)
    hidden = sum(hidden_flags)
    overlap_ratio = min(1.0, (overlaps + hidden) / n)
    edge_ratio = sum(edge_flags) / n
    confidence = max(0.0, min(1.0, 1.0 - 0.6 * overlap_ratio - 0.4 * edge_ratio))
    if unknown:
        # A pack whose name could not be read may be one more of the asked product: never a number.
        confidence = min(confidence, 0.4)
        hint = "vision.aim.hold_closer"
    elif edge_ratio > 0:
        hint = "vision.aim.move_back"
    elif hidden > 0 or overlap_ratio > SPREAD_RATIO:
        hint = "vision.aim.spread_out"
    else:
        hint = ""
    return CountResult(count=n, confidence=round(confidence, 2), needs_retake=bool(hint), aim_hint=hint,
                       objects=objects, unknown=unknown + unread, hidden=hidden, edge=sum(edge_flags))


def _items_from(data: dict) -> Any:
    """The point list from a model reply: a JSON list (wrapped as "items"), {"points": [...]}, or one object."""
    for key in ("items", "points"):
        if key in data:
            return data[key]
    if "point" in data:  # a single pack answered as one object instead of a list
        return [data]
    return []


async def count_in_frame(frame: Frame, item_label: str, product: str | None = None) -> CountResult:
    """Count ``item_label`` in one frame with the `count` role (Robotics-ER: one ER retry, no Flash fallback).

    ``product`` (optional) keeps only the points labelled with that product name. A reply cut off at the output
    cap is never a count (``count=None``, "hold closer"). Raises ``VisionError`` when both attempts fail;
    callers speak a retake hint instead of a number.
    """
    try:
        data = await generate_count_json(
            [image_part(frame.jpeg_b64), COUNT_PROMPT.format(label=item_label)],
            thinking=COUNT_THINKING,
            timeout_s=COUNT_TIMEOUT_S,
        )
    except TruncatedReply:
        return CountResult(count=None, confidence=0.0, needs_retake=True, aim_hint="vision.aim.hold_closer")
    out = assess(_items_from(data), product=product)
    out.model = data.get("_model")
    return out
