"""Live tool camera_ask: open questions about what the camera sees, answered as facts for a blind owner.

The Live model hears Sam in any language and decides what he wants: it calls ``camera_ask`` with a ``kind``
("look", "read", "count", "path", "list") and, for a count, the ``item`` (generic English noun) and ``product``
(brand, if named). The code routes on ``kind`` only and never parses the owner's words.

The tool takes a fresh frame from the chest camera and asks the ``point`` model (Robotics-ER 2, Flash fallback
through ``generate_json``) for objects with [y, x] points (0-1000) and distances. The result is structured facts
in plain English (``facts``, ``objects`` with side and distance, ``count`` / ``approx``, ``hint``) plus a short
English ``say_en`` meaning hint; Live says it to the owner in the language he is speaking. No fixed sentence in
any language is built here.

Rules: low confidence or a bad frame is ``sure: false`` plus an aim hint, never a guess. "Read this" is read
twice (``read`` and ``verify`` roles) and returned verbatim only when both reads agree. A path answer names the
obstacle and its direction and never says "safe" or "clear". Every ER 2 result also goes to
``vision.observers.on_vision_objects`` so the visual memory can record what was seen.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Literal, NamedTuple

from aira_live.contracts import RequestFrameMsg
from aira_live.diag import diag, summarize, trunc
from aira_live.session import REUSE_MIN_SIDE, Session, get_session
from aira_live.vision import listing, observers, precount
from aira_live.vision.count import CountResult, count_in_frame
from aira_live.vision.gemini import VisionError, generate_json, image_part
from aira_live.vision.hints import HINT_KEYS
from aira_live.vision.point import parse_box

log = logging.getLogger(__name__)

MIN_CONF = 0.5
MAX_OBJECTS = 3           # "what's in front of me": up to three objects, nearest first
FRAME_TIMEOUT_S = 6.0     # the phone may be busy with ambient frames; an owner question waits a little longer
COUNT_FRAME_TIMEOUT_S = 15.0  # count only: a slow phone may need several seconds to encode the count photo
COUNT_FRAMES = 1          # a count is ONE frame read twice in parallel, spoken only when the two reads agree
LOOK_TIMEOUT_S = 4.0      # per-attempt model timeout for a look / path answer (one retry)
READ_MIN_SIDE = 1280      # a reused frame for reading text must be a full-size owner frame
DIST_ORDER = ("near", "arm", "far")
KINDS = ("look", "read", "count", "path", "list")
# Labels that must never be spoken: people are never described, and a label can't smuggle in "safe".
PERSON = re.compile(r"\b(person|people|man|woman|boy|girl|child|kid|customer|face|human|lady|guy)\b", re.I)
UNSAFE_WORDS = re.compile(r"\bsafe\b|\bclear\b", re.I)

# Plain-English meaning of each fact (Live says it in the owner's language).
SIDE_EN = {"far_left": "to your far left", "left": "to your left", "centre": "straight ahead",
           "right": "to your right", "far_right": "to your far right"}
DIST_EN = {"near": "within arm's reach", "arm": "at arm's length", "far": "a few steps away"}
HINT_EN = {
    "move_back": "Move the phone back a little.",
    "spread_out": "Spread the items apart.",
    "show_items": "Show the items in front of the camera.",
    "show_label": "Turn the pack so the label faces the camera.",
    "hold_closer": "Hold it closer to the camera.",
    "not_visible": "It is not in view.",
    "hold_still": "Hold the camera still, or step back a little.",
}
AREA_EN = {"top": "Top shelf", "middle": "Middle shelf", "bottom": "Bottom shelf", "door": "Fridge door",
           "floor": "On the floor", "counter": "On the counter", "other": "Also"}

ASK_PROMPT = (
    "You are the eyes of a blind shopkeeper wearing this chest camera in a small dairy shop. "
    "Question: \"{question}\". Answer in plain English. Describe what is there so a blind person can act on it: "
    "list the main objects (at most 3, nearest and most relevant first), each with a short plain label, "
    "its point [y, x] normalized 0-1000 (x: 0 is the owner's left, 1000 the right), "
    "and its distance: near (within reach), arm (an arm's "
    "length), far (a step or more away). Walls, floor and the sky are not objects. guidance lists the next one "
    "or two actions. "
    "Never describe or identify people. If the image is dark, blurred or the thing asked about is not "
    "visible, set confidence below 0.5 and aim_hint to one of {hints}."
)
ASK_SCHEMA = ('{"answer": "<one short sentence>", "guidance": ["<step>"], '
              '"objects": [{"label": "<name>", "point": [y, x], '
              '"distance": "near|arm|far"}], '
              '"confidence": 0.0, "aim_hint": "<optional key>"}')
READ_PROMPT = (
    "Read every printed text in this image exactly as written, in reading order, as one line. "
    "Return the text verbatim: no translation, no summary, no guess. If nothing is readable, return an empty text."
)
READ_SCHEMA = '{"text": "<verbatim text>", "confidence": 0.0}'
_WS = re.compile(r"\s+")


class CountTarget(NamedTuple):
    generic: str            # what ER points at: "curd packet" (generic, so half-readable packs are pointed too)
    product: str | None     # the named product the labels must carry ("Hatsun Curd"), or None for every item
    phrase: str             # the English label of the answer ("Hatsun Curd packets"); "" when none


def _plural(noun: str) -> str:
    noun = noun.strip()
    if not noun or noun.endswith("s"):
        return noun
    return noun + ("es" if noun.endswith(("x", "ch", "sh")) else "s")


def count_target(item: str = "", product: str = "") -> CountTarget:
    """What to count, straight from the model's tool arguments (never parsed from the owner's words): ``item`` is
    the generic noun in English ("curd packet"), ``product`` the brand or product when one was named."""
    item = _WS.sub(" ", item or "").strip()
    product = _WS.sub(" ", product or "").strip()
    generic = item or (f"{product} packet" if product else "item the owner is showing")
    if product:
        phrase = f"{product} {_plural(item.split()[-1])}" if item else product
    else:
        phrase = _plural(item) if item else ""
    return CountTarget(generic, product or None, phrase)


def normalize_kind(kind: Any) -> str:
    """The model's ``kind`` argument; anything missing or unknown is a plain "look"."""
    k = str(kind or "").strip().lower()
    return k if k in KINDS else "look"


def short_hint(key: Any) -> str:
    """"vision.aim.move_back" -> "move_back" ("" for none)."""
    return str(key or "").rsplit(".", 1)[-1]


def hint_en(hint: str) -> str:
    return HINT_EN.get(short_hint(hint), "")


def side_key(x: float) -> str:
    """Where an x of 0-1000 is for the owner: his left, ahead (centre) or his right."""
    if x < 200:
        return "cam.side.far_left"
    if x < 400:
        return "cam.side.left"
    if x <= 600:
        return "cam.side.centre"
    if x <= 800:
        return "cam.side.right"
    return "cam.side.far_right"


def side_name(x: float) -> str:
    """"far_left", "left", "centre", "right" or "far_right"."""
    return side_key(x).rsplit(".", 1)[-1]


def dist_key(distance: Any) -> str:
    d = str(distance or "").lower()
    return f"cam.dist.{d}" if d in ("near", "arm", "far") else "cam.dist.arm"


def _box(raw: Any) -> list[int] | None:
    box = parse_box(raw)
    return list(box) if box else None


def distance_from_box(box: list[int] | None) -> str | None:
    """Rough distance from how much of the frame a box fills: half the height is "near", a quarter "arm"."""
    if not box:
        return None
    size = max(box[2] - box[0], box[3] - box[1]) / 1000.0
    return "near" if size >= 0.5 else "arm" if size >= 0.25 else "far"


def parse_objects(raw: Any) -> list[dict]:
    """Only well-formed objects: a label, a [y, x] point inside 0-1000 and a distance word (from the model, or
    from the box size when the model gave none). People and labels with "safe"/"clear" are dropped."""
    out: list[dict] = []
    for o in raw if isinstance(raw, list) else []:
        if not isinstance(o, dict):
            continue
        p = o.get("point")
        label = _WS.sub(" ", str(o.get("label") or "")).strip()
        if not label or PERSON.search(label) or UNSAFE_WORDS.search(label):
            continue
        if not (isinstance(p, (list, tuple)) and len(p) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1000 for v in p)):
            continue
        box = _box(o.get("box_2d") if o.get("box_2d") is not None else o.get("box"))
        d = str(o.get("distance") or "").lower()
        if d not in DIST_ORDER:
            d = distance_from_box(box) or "arm"
        obj = {"label": label, "point": [round(p[0]), round(p[1])], "distance": d}
        if box:
            obj["box"] = box
        out.append(obj)
    return out


_objects = parse_objects


def nearest_first(objects: list[dict]) -> list[dict]:
    """Nearest first; the model's own order (most relevant first) breaks ties."""
    return sorted(objects, key=lambda o: DIST_ORDER.index(o["distance"]))


def _conf(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f > 1.0:
        f = f / 100.0 if f <= 100.0 else 0.0
    return min(max(f, 0.0), 1.0)


def _hint(key: Any) -> str:
    return key if isinstance(key, str) and key in HINT_KEYS else "vision.aim.show_items"


def _steps(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in raw if str(x).strip()] if isinstance(raw, list) else []


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?।" else text + "."


def where_en(obj: dict) -> str:
    """"The table is straight ahead, at arm's length." (English facts for Live to say in his language.)"""
    return (f"The {obj['label']} is {SIDE_EN[side_name(obj['point'][1])]}, "
            f"{DIST_EN.get(obj['distance'], DIST_EN['arm'])}.")


def fact_objects(objects: list[dict]) -> list[dict]:
    """The objects as facts: label, side and distance in words, nearest first (at most ``MAX_OBJECTS``)."""
    return [{"label": o["label"], "side": side_name(o["point"][1]), "distance": o["distance"]}
            for o in nearest_first(objects)[:MAX_OBJECTS]]


def _facts_look(answer: str, steps: list[str], objects: list[dict]) -> str:
    """Up to three objects with side and distance, nearest first; the model's answer only when it sees fewer
    than two objects; then the next action. At most four short sentences."""
    parts: list[str] = [where_en(obj) for obj in nearest_first(objects)[:MAX_OBJECTS]]
    if answer and len(objects) < 2:
        parts.append(_sentence(answer))
    if steps and len(parts) < 4:
        parts.append(_sentence(steps[0]))
    return " ".join(parts) if parts else "Not sure what is there."


def _facts_path(steps: list[str], objects: list[dict]) -> tuple[str, dict | None]:
    """The obstacle and its direction. Never the model's own words alone: those could say "safe"."""
    if not objects:
        return "Nothing seen in the way up to a few steps. Walk slowly and check again.", None
    nearest = nearest_first(objects)[0]
    line = (f"There is a {nearest['label']} {SIDE_EN[side_name(nearest['point'][1])]}, "
            f"{DIST_EN.get(nearest['distance'], DIST_EN['arm'])}. Go around it slowly.")
    step = next((s for s in steps if not UNSAFE_WORDS.search(s)), "")
    obstacle = {"label": nearest["label"], "side": side_name(nearest["point"][1]), "distance": nearest["distance"]}
    return (f"{line} {_sentence(step)}".strip() if step else line), obstacle


def _not_sure(kind: str, reason: str, hint: str = "", **extra: Any) -> dict:
    """Never a guess: what went wrong and how to aim, as facts."""
    h = short_hint(hint)
    say_en = " ".join(x for x in ("Not sure.", HINT_EN.get(h, "")) if x)
    return {"kind": kind, "sure": False, "reason": reason, "hint": h, "aim_hint": hint, "say_en": say_en,
            "facts": say_en, "answer": "", "objects": [], "confidence": 0.0, **extra}


def _no_camera(kind: str) -> dict:
    return {"kind": kind, "sure": False, "reason": "no_camera_frame", "hint": "", "answer": "", "objects": [],
            "confidence": 0.0, "say_en": "I can't see a camera picture. Please check the camera.",
            "facts": "No camera picture arrived."}


def _unavailable(kind: str) -> dict:
    return {"kind": kind, "sure": False, "reason": "vision_unavailable", "hint": "", "answer": "", "objects": [],
            "confidence": 0.0, "say_en": "I can't reach the vision model right now. Please try again.",
            "facts": "The vision model did not answer."}


async def _read_twice(frame_b64: str) -> dict:
    """Two independent reads (read + verify models) must agree, else "not sure" and the label hint."""
    # The two reads are independent: run them at the same time (one model wait instead of two).
    datas = await asyncio.gather(*(generate_json(role, [image_part(frame_b64), READ_PROMPT], READ_SCHEMA,
                                                 timeout_s=6.0) for role in ("read", "verify")))
    reads = [(str(data.get("text") or "").strip(), _conf(data.get("confidence"))) for data in datas]
    texts = [_WS.sub(" ", r[0]).casefold() for r in reads]
    conf = min(r[1] for r in reads)
    if not texts[0] or texts[0] != texts[1] or conf < MIN_CONF:
        reason = "nothing_readable" if not texts[0] else "reads_disagree" if texts[0] != texts[1] else "low_confidence"
        return _not_sure("read", reason, "vision.aim.show_label", confidence=conf,
                         agreed=bool(texts[0]) and texts[0] == texts[1])
    text = reads[0][0]
    return {"kind": "read", "sure": True, "text": text, "verbatim": True, "answer": text,
            "facts": f'The printed text reads, verbatim: "{text}"', "say_en": text, "confidence": conf,
            "agreed": True}


def listing_facts(areas: list[dict]) -> str:
    """"Top shelf: milk to your left, curd straight ahead. Fridge door: ..." in English."""
    if not areas:
        return "No products in view. Show me the shelf."
    parts: list[str] = []
    for a in areas[:listing.MAX_AREAS]:
        title = a["area_name"] if a["area"] == "other" and a["area_name"] else AREA_EN.get(a["area"], "Also")
        items = ", ".join(f"{i['label']} {SIDE_EN[side_name(i['point'][1])]}" for i in a["items"][:listing.MAX_ITEMS])
        parts.append(f"{title}: {items}.")
    return " ".join(parts)


def _as_list_facts(out: dict) -> dict:
    """A shelf listing (``vision.listing``) as facts: the areas and their items, in English."""
    if out.get("aim_hint"):
        return _not_sure("list", "low_confidence", out["aim_hint"], confidence=out.get("confidence", 0.0))
    areas = out.get("areas") or []
    facts = listing_facts(areas)
    return {"kind": "list", "sure": bool(areas), "facts": facts, "say_en": facts, "answer": facts,
            "areas": [{"area": a["area"], "area_name": a["area_name"],
                       "items": [{"label": i["label"], "side": side_name(i["point"][1])} for i in a["items"]]}
                      for a in areas],
            "objects": out.get("objects", []), "confidence": out.get("confidence", 0.0)}


@asynccontextmanager
async def owner_first(s: Session):
    """The owner's camera question goes first: while it runs the ambient and describe loops make no ER 2 call
    (``Session.owner_busy``), and the phone's describe stream pauses so the question's frames are not queued
    behind scene frames. The stream resumes when the last question ends."""
    from aira_live.tools import describe  # describe imports this module

    loop = None
    with s.owner_asking():
        if s.owner_asks == 1:
            loop = describe.active(s)
            if loop is not None and loop.active:
                await _send_quiet(s, RequestFrameMsg(purpose=describe.PURPOSE, stop=True))
            else:
                loop = None
        try:
            yield
        finally:
            if loop is not None and s.owner_asks == 1 and describe.active(s) is loop and loop.active:
                await _send_quiet(s, RequestFrameMsg(purpose=describe.PURPOSE, continuous=True))


async def _send_quiet(s: Session, msg: RequestFrameMsg) -> None:
    try:
        await s.send(msg)
    except Exception as exc:  # noqa: BLE001 - a closed socket: nothing to pause or resume
        log.debug("camera_ask: describe pause/resume not sent to %s: %s", s.session_id, exc)


async def _count_one(frame, tgt: CountTarget) -> CountResult | None:
    try:
        return await count_in_frame(frame, tgt.generic, product=tgt.product)
    except VisionError as exc:
        log.warning("camera_ask: count failed: %s", type(exc).__name__)
        return None


def _usable(r: CountResult | None) -> bool:
    return r is not None and r.count is not None and r.confidence >= MIN_CONF


# A full shelf (more than SMALL_COUNT packs) is never counted exactly twice in a row: packs overlap and the two
# reads differ by a few. Two reads within ABOUT_TOLERANCE of each other give "about N" (``approx`` true, so it is
# never passed off as exact); reads further apart, or with confidence below ABOUT_MIN_CONF, stay "not sure".
SMALL_COUNT = 10
ABOUT_MIN_CONF = 0.3
ABOUT_TOLERANCE = 0.08
# Hints that only say the packs are crowded or cut at the edge. Any other hint ("hold closer": an unreadable label
# that may be one more of the product, or a reply cut at the item / output cap; "show items") blocks "about N":
# ``count.assess`` caps those reads at 0.4 confidence precisely so that no number is said.
ABOUT_HINTS = frozenset({"", "vision.aim.spread_out", "vision.aim.move_back"})


def _about(a: CountResult | None, b: CountResult | None) -> int | None:
    for r in (a, b):
        if r is None or r.count is None or r.aim_hint not in ABOUT_HINTS:
            return None
        if r.confidence < ABOUT_MIN_CONF:
            return None
    hi = max(a.count, b.count)
    if hi <= SMALL_COUNT or abs(a.count - b.count) > max(2, round(ABOUT_TOLERANCE * hi)):
        return None
    return round((a.count + b.count) / 2)


def _count_say_en(n: int, phrase: str, approx: bool) -> str:
    what = f" {phrase}" if phrase else ""
    return f"About {n}{what}." if approx else f"I can see {n}{what}."


async def _count(s: Session, item: str = "", product: str = "") -> dict:
    """One frame, counted twice in parallel; a number only when the two reads agree. Off by one: a third read
    breaks the tie. Anything else is "not sure" plus how to hold the camera. Never an invented count.

    A full shelf whose two reads are close gives ``approx`` true ("about N"); ``exact`` is false then, so an
    estimate can never pass as an exact stock or delivery number."""
    tgt = count_target(item, product)
    frames = await s.request_frames("count", count=COUNT_FRAMES, timeout_s=COUNT_FRAME_TIMEOUT_S)
    diag("camera_ask", stage="frames", session=s.session_id, kind="count", lang=s.lang, frames=len(frames),
         bytes=[len(f.jpeg_b64) for f in frames], sizes=[[f.w, f.h] for f in frames])
    if not frames:
        diag("camera_ask", severity="WARNING", stage="no_frames", session=s.session_id, kind="count",
             timeout_s=COUNT_FRAME_TIMEOUT_S)
        return {**_no_camera("count"), "count": None, "approx": False, "exact": False, "agreed": False}
    pair = [frames[-1], frames[-1]]  # the model reads the same photo twice: two independent counts to agree
    started = time.perf_counter()
    reads: list[CountResult | None] = list(await asyncio.gather(*(_count_one(f, tgt) for f in pair)))
    n: int | None = None
    a, b = reads
    approx = _about(a, b)
    if approx is not None:
        n = approx
    elif _usable(a) and _usable(b):
        if a.count == b.count:
            n = a.count
        elif abs(a.count - b.count) == 1:
            third = await _count_one(pair[-1], tgt)
            reads.append(third)
            if _usable(third) and third.count in (a.count, b.count):
                n = third.count
    ms = round((time.perf_counter() - started) * 1000)
    good = [r for r in reads if r is not None]
    conf = min((r.confidence for r in good), default=0.0) if len(good) == len(reads) else 0.0
    diag("camera_ask", stage="count", session=s.session_id, kind="count", frames=len(frames),
         counts=[r.count if r else None for r in reads], confidences=[r.confidence if r else None for r in reads],
         hints=[r.aim_hint if r else None for r in reads], models=[r.model if r else None for r in reads],
         agreed=n is not None, approx=approx is not None, third=len(reads) > 2,
         product_filter=tgt.product is not None, ms=ms)
    shown = next((r for r in reversed(reads) if r is not None and r.count is not None), None)
    if shown is not None:
        await listing.publish_boxes(s, shown.objects, "camera_ask:count", frame_id=pair[-1].id)
    hints = {r.aim_hint for r in good if r.aim_hint}
    hint = hints.pop() if len(hints) == 1 and len(good) == len(reads) else ""
    what = {"label": tgt.generic if item else "", "product": tgt.product or ""}
    if n is None:
        if len(good) < len(reads):
            reason = "vision_failed"
        elif any(r.count is None for r in good):
            reason = "nothing_counted"
        elif any(r.confidence < MIN_CONF for r in good):
            reason = "low_confidence"
        else:
            reason = "reads_disagree"
        h = short_hint(hint) or "hold_still"
        say_en = f"Not sure of the count. {HINT_EN[h] if h in HINT_EN else HINT_EN['hold_still']}"
        return {"kind": "count", "sure": False, "count": None, "approx": False, "exact": False, "agreed": False,
                "reason": reason, "hint": h, "aim_hint": hint, **what, "say_en": say_en, "facts": say_en,
                "answer": "", "objects": [], "confidence": conf}
    is_approx = approx is not None
    say_en = _count_say_en(n, tgt.phrase, is_approx)
    if hint:
        say_en = f"{say_en} {hint_en(hint)}".strip()
    return {"kind": "count", "sure": True, "count": n, "approx": is_approx, "exact": not is_approx,
            "agreed": not is_approx, "hint": short_hint(hint), "aim_hint": hint, **what, "say_en": say_en,
            "facts": say_en, "answer": say_en, "objects": shown.objects if shown else [], "confidence": conf}


async def camera_ask(question: str, kind: Literal["look", "read", "count", "path", "list"] = "look",
                     item: str = "", product: str = "") -> dict:
    """Look through the camera and answer the owner's question. Call it for ANY question about what is around
    the owner or in front of the camera, in any language. You decide what he wants and pass it in the
    arguments; the code never guesses it from his words.

    question: his question, as he asked it.
    kind: "count" for ANY how-many / count question in any language ("how many curd packets?", "எத்தனை
      பாக்கெட்?", "कितने पैकेट हैं?"); "read" to read a label, price tag, expiry date or any printed text;
      "list" for what is in or on a fridge, shelf or rack ("what's in my fridge?", "list the products here");
      "path" for whether his way is clear, obstacles ahead, can he walk forward; "look" for everything else
      (what is in front of me, where is the door, which shelf has curd).
    item: for kind="count", the generic thing to count as a short English noun ("curd packet", "milk pouch",
      "bottle"); empty if he did not say.
    product: for kind="count", the brand or product when he named one, in English ("Hatsun Curd"); else empty.

    The result is facts in English (facts, objects with side and distance, count, approx, hint) and a short
    English say_en meaning hint. Say the result to the owner in the language he is speaking; say numbers
    exactly as given; if approx is true say 'about'. sure false means not sure: say so and give the hint;
    never guess a count or an object yourself. An approx count is an estimate: never use it as a stock,
    delivery or sale quantity.
    """
    s: Session = get_session()
    kind = normalize_kind(kind)
    if kind == "count":
        # the background count of this scene, if there is one
        precounted = await precount.answer(s, kind, item=item, product=product)
        if precounted is not None:
            return precounted
    async with owner_first(s):
        if kind == "count":
            return await _count(s, item, product)
        return await _ask(s, kind, question)


async def _ask(s: Session, kind: str, question: str) -> dict:
    if kind == "path":                                         # a path needs a short burst: things move
        frames = await s.request_frames("ask", count=2, interval_ms=400, timeout_s=FRAME_TIMEOUT_S)
    else:  # a look or a read: a frame that arrived under a second ago is reused (no phone round trip)
        # (small print needs a full 1280 px owner frame; a 640 px ambient frame is enough for a look)
        frames = await s.look_frame("ask", timeout_s=FRAME_TIMEOUT_S,
                                    min_side=READ_MIN_SIDE if kind == "read" else REUSE_MIN_SIDE)
    diag("camera_ask", stage="frames", session=s.session_id, kind=kind, lang=s.lang, question=trunc(question),
         frames=len(frames), bytes=[len(f.jpeg_b64) for f in frames], sizes=[[f.w, f.h] for f in frames])
    if not frames:
        diag("camera_ask", severity="WARNING", stage="no_frames", session=s.session_id, kind=kind,
             timeout_s=FRAME_TIMEOUT_S)
        return _no_camera(kind)
    try:
        if kind == "read":
            out = await _read_twice(frames[-1].jpeg_b64)
            diag("camera_ask", stage="done", session=s.session_id, kind=kind, confidence=out["confidence"],
                 agreed=out.get("agreed"), aim_hint=out.get("aim_hint"), say=trunc(out["say_en"]))
            return out
        if kind == "list":
            out = _as_list_facts(await listing.list_shelf(s, frames[-1].jpeg_b64, question))
            diag("camera_ask", stage="done", session=s.session_id, kind=kind, verdict="list",
                 confidence=out.get("confidence"), say=trunc(out.get("say_en", "")))
            return out
        parts = [image_part(f.jpeg_b64) for f in frames]
        parts.append(ASK_PROMPT.format(question=question, hints=", ".join(HINT_KEYS)))
        # The owner is waiting: low thinking, LOOK_TIMEOUT_S per attempt and a single retry (worst case ~8 s,
        # not 3 x 6 s).
        data = await generate_json("point", parts, ASK_SCHEMA, thinking="low", timeout_s=LOOK_TIMEOUT_S,
                                   max_attempts=2)
    except VisionError as exc:
        log.warning("camera_ask: vision failed for %s: %s", s.shop_id, exc)
        diag("camera_ask", severity="WARNING", stage="error", session=s.session_id, kind=kind,
             error=type(exc).__name__, message=trunc(str(exc), 200))
        return _unavailable(kind)

    objects = parse_objects(data.get("objects"))
    conf = _conf(data.get("confidence"))
    steps = _steps(data.get("guidance"))
    answer = str(data.get("answer") or "").strip()
    raw_objects = data.get("objects")
    diag("camera_ask", stage="model", session=s.session_id, kind=kind, model=data.get("_model"),
         keys=sorted(k for k in data if k != "_model"), confidence=conf, raw_confidence=summarize(data.get("confidence")),
         objects=len(objects), raw_objects=len(raw_objects) if isinstance(raw_objects, list) else None,
         aim_hint=summarize(data.get("aim_hint")), answer=trunc(answer), steps=len(steps))
    # Every ER 2 result is offered to the observers (the "last seen" memory), whatever gets spoken.
    # The last-seen memory and other subscribers get the objects (text only); a failing observer never breaks
    # the answer.
    await observers.on_vision_objects(s, objects, {"source": "camera_ask", "purpose": "ask", "kind": kind,
                                                   "question": question, "answer": answer,
                                                   "frame_id": frames[-1].id, "frame_ts": frames[-1].ts,
                                                   "confidence": conf, "model": data.get("_model")})
    # ...and to the live monitor and the phone's "Show boxes" overlay (source "er2", boxes included).
    await listing.publish_boxes(s, objects, f"camera_ask:{kind}", frame_id=frames[-1].id)
    # A tip alone is not doubt: the model often adds one ("show me the items") while it is sure of what it sees
    # (seen live: confidence 0.9, an answer, and a tip). It means "not sure" only when the model is unsure, or is
    # sure but has nothing to say.
    if conf < MIN_CONF or (data.get("aim_hint") and not answer and not objects):
        hint = _hint(data.get("aim_hint"))
        out = _not_sure(kind, "low_confidence", hint, confidence=conf, objects=objects)
        diag("camera_ask", stage="done", session=s.session_id, kind=kind, verdict="not_sure", confidence=conf,
             aim_hint=hint, say=trunc(out["say_en"]))
        return out
    out = {"kind": kind, "sure": True, "answer": answer, "guidance": steps, "objects": objects,
           "seen": fact_objects(objects), "confidence": conf}
    if kind == "path":
        facts, obstacle = _facts_path(steps, objects)
        out["obstacle"] = obstacle                # never "safe" / "clear": only what is in the way
        out["answer"] = ""                        # the model's own words could say "safe"
    else:
        facts = _facts_look(answer, steps, objects)
    out["facts"] = out["say_en"] = facts
    diag("camera_ask", stage="done", session=s.session_id, kind=kind, verdict="answer", confidence=conf,
         objects=len(objects), say=trunc(facts))
    return out
