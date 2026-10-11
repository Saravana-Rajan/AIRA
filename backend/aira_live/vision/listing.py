"""Fridge / shelf listing (spec §4): "what's in my fridge?" lists every visible item grouped by shelf or area.

``camera_ask`` hands a listing question here. The ``point`` model (ER 2, Flash fallback) returns areas with
items as [y, x] points (0-1000); the spoken line is built from i18n parts (area names, sides) in the session
language, with the item labels as the model named them. Low confidence says "not sure" plus an aim hint and
lists nothing; nothing visible says "show me the shelf". Never a guess.

After every result the optional ``aira_live.vision.observers.on_vision_objects`` hook (personal-item memory,
spec §5) is told what was seen; it is skipped when the module does not exist.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any

from aira_live.i18n import t
from aira_live.prompt import LANG_NAME
from aira_live.vision.gemini import generate_json, image_part
from aira_live.vision.hints import HINT_KEYS

log = logging.getLogger(__name__)

MIN_CONF = 0.5
MAX_AREAS = 4
MAX_ITEMS = 4
AREAS = ("top", "middle", "bottom", "door", "floor", "counter", "other")
DISTANCES = ("near", "arm", "far")

# A listing on their own: the owner wants every item named.
LIST_WORDS = (
    "what products", "which products", "what items", "which items", "list the", "list what", "list everything",
    "each product", "every product", "each item", "every item", "what all", "என்னென்ன", "क्या-क्या", "क्या क्या",
)
# A container plus an "in / on" question: "what's in my fridge?", "ஃப்ரிட்ஜில் என்ன இருக்கு", "फ्रिज में क्या है".
# "Which shelf has curd?" names a container but asks where one thing is: that stays a "look" question.
CONTAINER_WORDS = ("fridge", "freezer", "shelf", "shelves", "rack", "cupboard", "ஃப்ரிட்ஜ", "அலமாரி", "फ्रिज", "शेल्फ")
CONTENTS_WORDS = ("what's in", "what is in", "whats in", "what's on", "what is on", "whats on", "inside",
                  "what do you see", "என்ன இருக்கு", "என்ன இருக்கிறது", "என்ன உள்ளது", "क्या है", "क्या रखा", "क्या दिख")

LIST_PROMPT = (
    "You are the eyes of a blind shopkeeper wearing this chest camera. The owner asks: \"{question}\". "
    "List every product or item you can see, grouped by where it is: each shelf of a fridge or rack from top to "
    "bottom, the fridge door, the floor or the counter. area is one of {areas}; area_name is a short name for "
    "it in {lang}. Name each item in {lang} with its point [y, x] normalized 0-1000 (x: 0 is the owner's left, "
    "1000 the right) and distance: near (within reach), arm (an arm's length), far (a step or more). Only items "
    "you actually see; never guess brands you cannot read. Never describe or identify people. If the image is "
    "dark, blurred or shows no shelf, set confidence below 0.5 and aim_hint to one of {hints}."
)
LIST_SCHEMA = ('{"areas": [{"area": "top|middle|bottom|door|floor|counter|other", "area_name": "<short name>", '
               '"items": [{"label": "<name>", "point": [y, x], "distance": "near|arm|far"}]}], '
               '"confidence": 0.0, "aim_hint": "<optional key>"}')


def is_listing(question: str) -> bool:
    """True for "what's in my fridge?", "list the products on this shelf", and the same in Tamil or Hindi."""
    q = (question or "").lower()
    if any(w in q for w in LIST_WORDS):
        return True
    return any(w in q for w in CONTAINER_WORDS) and any(w in q for w in CONTENTS_WORDS)


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
        out.append({"label": label, "point": [round(p[0]), round(p[1])], "distance": d if d in DISTANCES else "arm"})
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


def _side(x: float, lang: str) -> str:
    from aira_live.tools.camera import side_key   # lazy: camera imports this module

    return t(side_key(x), lang)


def say_listing(areas: list[dict], lang: str) -> str:
    """"Top shelf: milk to your left, curd straight ahead. Fridge door: ..." in ``lang``."""
    if not areas:
        return t("cam.list.none", lang)
    parts: list[str] = []
    for a in areas[:MAX_AREAS]:
        title = a["area_name"] if a["area"] == "other" and a["area_name"] else t(f"cam.area.{a['area']}", lang)
        items = ", ".join(t("cam.list.item", lang, label=i["label"], side=_side(i["point"][1], lang))
                          for i in a["items"][:MAX_ITEMS])
        parts.append(t("cam.list.area", lang, area=title, items=items))
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


async def list_shelf(session, frame_b64: str, question: str) -> dict:
    """One frame of a fridge or shelf -> {say, kind: "list", areas, objects, confidence}. Raises VisionError."""
    lang = session.lang
    prompt = LIST_PROMPT.format(question=question, lang=LANG_NAME.get(lang, "English"),
                                areas="|".join(AREAS), hints=", ".join(HINT_KEYS))
    data = await generate_json("point", [image_part(frame_b64), prompt], LIST_SCHEMA, thinking="low", timeout_s=8.0)
    if not isinstance(data, dict):
        data = {}
    conf = _conf(data.get("confidence"))
    hint = data.get("aim_hint")
    if conf < MIN_CONF or hint:
        key = hint if isinstance(hint, str) and hint in HINT_KEYS else "vision.aim.show_items"
        return {"say": f"{t('cam.not_sure', lang)} {t(key, lang)}", "answer": "", "kind": "list",
                "areas": [], "objects": [], "confidence": conf, "aim_hint": key}
    areas = parse_areas(data.get("areas") if "areas" in data else data)
    objects = flat_objects(areas)
    await observe(session, objects, "camera_list")
    say = say_listing(areas, lang)
    return {"say": say, "answer": say, "kind": "list", "areas": areas, "objects": objects, "confidence": conf}
