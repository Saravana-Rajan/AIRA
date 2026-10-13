"""Fridge / shelf listing (spec §4): "what's in my fridge?" lists every visible item grouped by shelf or area.

``camera_ask`` hands a listing question here (the Live model calls it with kind="list"). The ``point`` model
(ER 2, Flash fallback) returns areas with items as [y, x] points (0-1000). The result is FACTS: areas (top shelf first) with their items left to right,
each with a coarse ``side`` (left / ahead / right) and distance, plus a short English ``say_en``; Gemini Live
phrases it in the owner's language. Low confidence returns ``unsure`` plus an aim hint and lists nothing;
nothing visible asks for the shelf. Never a guess.

After every result the optional ``aira_live.vision.observers.on_vision_objects`` hook (personal-item memory,
spec §5) is told what was seen; it is skipped when the module does not exist.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any

from aira_live.vision.gemini import generate_json, image_part
from aira_live.vision.hints import HINT_KEYS, hint_text
from aira_live.vision.point import parse_box

log = logging.getLogger(__name__)

MIN_CONF = 0.5
MAX_AREAS = 4
MAX_ITEMS = 4
AREAS = ("top", "middle", "bottom", "door", "floor", "counter", "other")
DISTANCES = ("near", "arm", "far")

# English names used in ``say_en`` (the meaning hint and caption fallback; Live says it in the owner's language).
AREA_EN = {"top": "Top shelf", "middle": "Middle shelf", "bottom": "Bottom shelf", "door": "Fridge door",
           "floor": "On the floor", "counter": "On the counter", "other": "Also"}
SIDE_EN = {"cam.side.far_left": "to your far left", "cam.side.left": "to your left",
           "cam.side.centre": "straight ahead", "cam.side.right": "to your right",
           "cam.side.far_right": "to your far right"}
DIST_EN = {"near": "within arm's reach", "arm": "at arm's length", "far": "a few steps away"}
NONE_EN = "I can't see any products. Show me the shelf."
NOT_SURE_EN = "Not sure."

LIST_PROMPT = (
    "You are the eyes of a blind shopkeeper wearing this chest camera. The owner asks: \"{question}\". "
    "List every product or item you can see, grouped by where it is: each shelf of a fridge or rack from top to "
    "bottom, the fridge door, the floor or the counter. area is one of {areas}; area_name is a short English "
    "name for it. Name each item in plain English (brand and size as printed on the pack when you can read "
    "them) with its point [y, x] normalized 0-1000 (x: 0 is the owner's left, 1000 the right) and distance: "
    "near (within reach), arm (an arm's length), far (a step or more). Only items "
    "you actually see; never guess brands you cannot read. Never describe or identify people. If the image is "
    "dark, blurred or shows no shelf, set confidence below 0.5 and aim_hint to one of {hints}."
)
LIST_SCHEMA = ('{"areas": [{"area": "top|middle|bottom|door|floor|counter|other", "area_name": "<short name>", '
               '"items": [{"label": "<name>", "point": [y, x], '
               '"distance": "near|arm|far"}]}], '
               '"confidence": 0.0, "aim_hint": "<optional key>"}')


def coarse_side(x: float) -> str:
    """"left", "ahead" or "right" from an x of 0-1000: the fact carried per item (coarser than ``side_en``)."""
    return "left" if x < 400 else "right" if x > 600 else "ahead"


def side_en(x: float) -> str:
    """English side words for an x of 0-1000: "to your left", "straight ahead", ..."""
    from aira_live.tools.camera import side_key   # lazy: camera imports this module

    return SIDE_EN.get(side_key(x), "straight ahead")


def dist_en(distance: Any) -> str:
    """English distance words: "within arm's reach", "at arm's length", "a few steps away"."""
    return DIST_EN.get(str(distance or "").lower(), DIST_EN["arm"])


def _point_ok(p: Any) -> bool:
    return (isinstance(p, (list, tuple)) and len(p) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1000 for v in p))


def _items(raw: Any) -> list[dict]:
    out: list[dict] = []
    for o in raw if isinstance(raw, list) else []:
        if not isinstance(o, dict):
            continue
        label = str(o.get("label") or "").strip()
        p = o.get("point")
        if not label or not _point_ok(p):
            continue
        d = str(o.get("distance") or "").lower()
        item = {"label": label, "point": [round(p[0]), round(p[1])], "distance": d if d in DISTANCES else "arm",
                "side": coarse_side(p[1])}
        box = parse_box(o.get("box_2d") if o.get("box_2d") is not None else o.get("box"))
        if box:
            item["box"] = list(box)
        out.append(item)
    out.sort(key=lambda i: i["point"][1])                      # left to right, as the owner would sweep a hand
    return out


def _area_rank(area: dict) -> float:
    """Top shelf first: areas are ordered by the mean y of their items (0 is the top of the picture)."""
    ys = [i["point"][0] for i in area["items"]]
    return sum(ys) / len(ys) if ys else 500.0


def parse_areas(raw: Any) -> list[dict]:
    """Only well-formed areas with at least one well-formed item, top to bottom; items left to right."""
    if isinstance(raw, dict):                                  # a flat ER 2 list reply: one "other" area
        raw = [{"area": "other", "items": raw.get("items")}]
    out: list[dict] = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        items = _items(a.get("items"))
        if not items:
            continue
        name = str(a.get("area") or "").strip().lower()
        area_name = str(a.get("area_name") or "").strip()
        if name not in AREAS:
            area_name, name = area_name or name, "other"
        out.append({"area": name, "area_name": area_name, "items": items})
    out.sort(key=_area_rank)
    return out


def flat_objects(areas: list[dict]) -> list[dict]:
    """Every item with its area, in spoken order: what the observers and the tool result get."""
    return [{**i, "area": a["area"]} for a in areas for i in a["items"]]


def say_listing(areas: list[dict], lang: str | None = None) -> str:
    """English meaning: "Top shelf: milk to your left, curd straight ahead. Fridge door: ..." (``lang`` is
    ignored; Live phrases it in the owner's language)."""
    if not areas:
        return NONE_EN
    parts: list[str] = []
    for a in areas[:MAX_AREAS]:
        title = a["area_name"] if a["area"] == "other" and a["area_name"] else AREA_EN[a["area"]]
        items = ", ".join(f"{i['label']} {side_en(i['point'][1])}" for i in a["items"][:MAX_ITEMS])
        parts.append(f"{title}: {items}.")
    return " ".join(parts)


def _conf(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:
        return 0.0
    if f > 1.0:
        f = f / 100.0 if f <= 100.0 else 0.0
    return min(max(f, 0.0), 1.0)


async def observe(session, objects: list[dict], context: str) -> None:
    """Tell ``aira_live.vision.observers.on_vision_objects`` what ER 2 saw, when that module exists.

    The hook is optional (spec §5, another lane) and must never break the answer: a missing module is skipped,
    a failing hook is logged.
    """
    try:
        from aira_live.vision.observers import on_vision_objects
    except ImportError:
        return
    try:
        res = on_vision_objects(session, objects, context)
        if inspect.isawaitable(res):
            await res
    except Exception:
        log.warning("vision observer failed for %s", context, exc_info=True)


async def publish_boxes(session, objects: list[dict], context: str, **kw) -> None:
    """Hand an ER 2 result (points and boxes) to ``aira_live.monitor_hooks.publish_er2``: the live monitor and
    the phone's optional "Show boxes" overlay. Optional like the observers; never breaks the answer."""
    try:
        from aira_live.monitor_hooks import publish_er2
    except ImportError:
        return
    try:
        await publish_er2(session, objects, context=context, **kw)
    except Exception:
        log.warning("monitor publish failed for %s", context, exc_info=True)


async def list_shelf(session, frame_b64: str, question: str) -> dict:
    """One frame of a fridge or shelf -> facts ``{kind: "list", areas, objects, confidence, say_en}``; when unsure
    ``{..., unsure: True, aim_hint}`` with nothing listed. Raises VisionError."""
    prompt = LIST_PROMPT.format(question=question, areas="|".join(AREAS), hints=", ".join(HINT_KEYS))
    data = await generate_json("point", [image_part(frame_b64), prompt], LIST_SCHEMA, thinking="low", timeout_s=8.0)
    if not isinstance(data, dict):
        data = {}
    conf = _conf(data.get("confidence"))
    hint = data.get("aim_hint")
    if conf < MIN_CONF or hint:
        key = hint if isinstance(hint, str) and hint in HINT_KEYS else "vision.aim.show_items"
        return {"kind": "list", "areas": [], "objects": [], "confidence": conf, "unsure": True, "aim_hint": key,
                "say_en": f"{NOT_SURE_EN} {hint_text(key)}"}
    areas = parse_areas(data.get("areas") if "areas" in data else data)
    objects = flat_objects(areas)
    await observe(session, objects, "camera_list")
    await publish_boxes(session, objects, "camera_list")
    out = {"kind": "list", "areas": areas, "objects": objects, "confidence": conf, "say_en": say_listing(areas)}
    return out
