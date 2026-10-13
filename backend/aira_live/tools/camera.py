"""Live tool camera_ask: open questions about what the camera sees, answered as guidance for a blind owner.

The Live model only hears audio. For "what's in front of me?", "is my path clear?", "read this label" or
"where is the door?" it calls this tool, which takes a fresh frame from the chest camera and asks the
``point`` model (Robotics-ER 2, Flash fallback through ``generate_json``) for objects with [y, x] points
(0-1000) and distances. The spoken line is built here, not by the model: a side (left / ahead / right), a
distance (arm's length, a few steps) and the next action, in the session language.

"What's in front of me?" names up to three objects, nearest first, each with its side and distance ("a table
straight ahead, at arm's length"). Every ER 2 result also goes to ``vision.observers.on_vision_objects`` so
the visual memory can record what was seen.

Rules: low confidence or a bad frame says "not sure" plus an aim hint, never a guess. "Read this" is read
twice (``read`` and ``verify`` roles) and spoken verbatim only when both reads agree. A path question names
the obstacle and its direction and never says "safe" or "clear".
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any, NamedTuple

from aira_live.contracts import RequestFrameMsg
from aira_live.diag import diag, summarize, trunc
from aira_live.i18n import t
from aira_live.prompt import LANG_NAME
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
COUNT_FRAMES = 2          # a count is two frames 400 ms apart, counted in parallel, spoken only when they agree
COUNT_INTERVAL_MS = 400
LOOK_TIMEOUT_S = 4.0      # per-attempt model timeout for a look / path answer (one retry)
READ_MIN_SIDE = 1280      # a reused frame for reading text must be a full-size owner frame
DIST_ORDER = ("near", "arm", "far")
# Labels that must never be spoken: people are never described, and a label can't smuggle in "safe".
PERSON = re.compile(r"\b(person|people|man|woman|boy|girl|child|kid|customer|face|human|lady|guy)\b", re.I)
UNSAFE_WORDS = re.compile(r"\bsafe\b|\bclear\b", re.I)

READ_WORDS = ("read", "what does it say", "what does this say", "label", "text", "written", "price tag",
              "expiry", "படி", "எழுத", "पढ़", "लिखा")
PATH_WORDS = ("path", "way clear", "walk", "step", "in my way", "obstacle", "trip", "move forward", "go forward",
              "ahead of me", "வழி", "நடக்க", "रास्ता", "चल")

ASK_PROMPT = (
    "You are the eyes of a blind shopkeeper wearing this chest camera in a small dairy shop. "
    "Question: \"{question}\". Answer in {lang}. Describe what is there so a blind person can act on it: "
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


COUNT_EN = re.compile(r"\bhow many\b|\bcount\b|\bcount (?:the|these|those|them|all)\b", re.I)
COUNT_WORDS = ("எத்தனை", "எண்ணு", "எண்ணி", "எண்ணிக்கை", "कितने", "कितनी", "कितना", "गिन")
_LEAD = re.compile(r"^.*?\b(?:how many|count)\b\s*", re.I | re.S)
_ARTICLES = {"the", "these", "those", "all", "of", "my", "your"}
_STOP = re.compile(r"\b(?:are|is|do|does|did|can|could|would|will|in|on|at|there|here|visible|you|i|that|which|"
                   r"left|remaining|kept|lying|placed|now|please|for)\b.*$", re.I | re.S)
_PRONOUNS = {"them", "these", "those", "it", "they", "items", "item", "things", "thing", "this", "that"}
CONTAINERS = {"packet": "packet", "packets": "packet", "pack": "pack", "packs": "pack", "pouch": "pouch",
              "pouches": "pouch", "bottle": "bottle", "bottles": "bottle", "box": "box", "boxes": "box",
              "cup": "cup", "cups": "cup", "tub": "tub", "tubs": "tub", "can": "can", "cans": "can",
              "carton": "carton", "cartons": "carton", "bag": "bag", "bags": "bag", "tin": "tin", "tins": "tin",
              "jar": "jar", "jars": "jar", "sachet": "sachet", "sachets": "sachet", "tray": "tray", "trays": "tray",
              "brick": "brick", "bricks": "brick"}
CATEGORIES = {"milk", "curd", "butter", "ghee", "paneer", "cheese", "buttermilk", "lassi", "yogurt", "yoghurt",
              "cream", "ice", "dahi", "milkshake", "khoa", "flavoured", "flavored", "toned", "skimmed"}
_SIZE = re.compile(r"^\d+(?:\.\d+)?(?:ml|l|ltr|litre|liter|g|gm|kg)?$", re.I)
_UNITS = {"ml", "l", "ltr", "litre", "liter", "litres", "g", "gm", "gram", "grams", "kg"}
_NON_ASCII_LETTER = re.compile(r"[^\x00-\x7f]")


class CountTarget(NamedTuple):
    generic: str            # what ER points at: "curd packet" (generic, so half-readable packs are pointed too)
    product: str | None     # the named product the labels must carry ("Hatsun Curd"), or None for every item
    phrase: str             # the owner's own words for the spoken answer ("Hatsun Curd packets"); "" if unknown


def count_target(question: str) -> CountTarget:
    """What to count, from the owner's question. English questions are parsed; other languages pass the whole
    question to the model as the item hint (the model reads Tamil and Hindi)."""
    q = question.strip()
    if _NON_ASCII_LETTER.search(q):
        return CountTarget(f'item the owner asks about in "{q}"', None, "")
    rest = _LEAD.sub("", q, count=1)
    rest = re.sub(r"[?.!,]", " ", rest)
    rest = _STOP.sub("", rest).strip()
    words = rest.split()
    while words and words[0].lower() in _ARTICLES:
        words = words[1:]
    if not words or all(w.lower() in _PRONOUNS for w in words):
        return CountTarget("item the owner is showing", None, "")
    phrase = " ".join(words)
    container = next((CONTAINERS[w.lower()] for w in reversed(words) if w.lower() in CONTAINERS), None)
    category = [w.lower() for w in words if w.lower() in CATEGORIES]
    brand = [w for w in words if w.lower() not in CONTAINERS and w.lower() not in CATEGORIES
             and w.lower() not in _UNITS and not _SIZE.match(w)]
    if container is None and not category:
        return CountTarget(phrase, None, phrase)
    generic = " ".join([*category, container or "packet"])
    product_words = [w for w in words if w in brand or w.lower() in category]
    product = " ".join(product_words) if brand else None
    return CountTarget(generic, product, phrase)


def classify(question: str) -> str:
    """One of "count", "read", "list", "path" or "look" from the owner's words (any language falls back to
    "look")."""
    q = question.lower()
    if COUNT_EN.search(q) or any(w in q for w in COUNT_WORDS):
        return "count"
    if any(w in q for w in READ_WORDS):
        return "read"
    if listing.is_listing(q):           # "what's in my fridge?": list the items by shelf (spec §4)
        return "list"
    if any(w in q for w in PATH_WORDS):
        return "path"
    return "look"


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


def _where(obj: dict, lang: str) -> str:
    return t("cam.where", lang, label=obj["label"], side=t(side_key(obj["point"][1]), lang),
             dist=t(dist_key(obj["distance"]), lang))


def _sentence(text: str) -> str:
    text = text.strip()
    return text if not text or text[-1] in ".!?।" else text + "."


def _say_look(answer: str, steps: list[str], objects: list[dict], lang: str) -> str:
    """Up to three objects with side and distance, nearest first; the model's answer only when it sees fewer
    than two objects; then the next action. At most four short sentences."""
    parts: list[str] = [_where(obj, lang) for obj in nearest_first(objects)[:MAX_OBJECTS]]
    if answer and len(objects) < 2:
        parts.append(_sentence(answer))
    if steps and len(parts) < 4:
        parts.append(_sentence(steps[0]))
    return " ".join(parts) if parts else t("cam.not_sure", lang)


def _say_path(steps: list[str], objects: list[dict], lang: str) -> str:
    """The obstacle and its direction. Never the model's own words: those could say "safe"."""
    if not objects:
        return t("cam.path_nothing", lang)
    nearest = nearest_first(objects)[0]
    line = t("cam.obstacle", lang, label=nearest["label"], side=t(side_key(nearest["point"][1]), lang),
             dist=t(dist_key(nearest["distance"]), lang))
    step = next((s for s in steps if not UNSAFE_WORDS.search(s)), "")
    return f"{line} {_sentence(step)}".strip() if step else line


async def _read_twice(frame_b64: str, lang: str) -> dict:
    """Two independent reads (read + verify models) must agree, else "not sure" and the label hint."""
    # The two reads are independent: run them at the same time (one model wait instead of two).
    datas = await asyncio.gather(*(generate_json(role, [image_part(frame_b64), READ_PROMPT], READ_SCHEMA,
                                                 timeout_s=6.0) for role in ("read", "verify")))
    reads = [(str(data.get("text") or "").strip(), _conf(data.get("confidence"))) for data in datas]
    texts = [_WS.sub(" ", r[0]).casefold() for r in reads]
    conf = min(r[1] for r in reads)
    if not texts[0] or texts[0] != texts[1] or conf < MIN_CONF:
        return {"say": f"{t('cam.not_sure', lang)} {t('vision.aim.show_label', lang)}", "answer": "",
                "kind": "read", "confidence": conf, "agreed": bool(texts[0]) and texts[0] == texts[1],
                "aim_hint": "vision.aim.show_label"}
    return {"say": reads[0][0], "answer": reads[0][0], "kind": "read", "confidence": conf, "agreed": True}


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
# reads differ by a few. Two reads within ABOUT_TOLERANCE of each other give "about N" (the word is spoken, so it
# is never passed off as exact); reads further apart, or with confidence below ABOUT_MIN_CONF, stay "not sure".
SMALL_COUNT = 10
ABOUT_MIN_CONF = 0.3
ABOUT_TOLERANCE = 0.08


def _about(a: CountResult | None, b: CountResult | None) -> int | None:
    if any(r is None or r.count is None or r.confidence < ABOUT_MIN_CONF for r in (a, b)):
        return None
    hi = max(a.count, b.count)
    if hi <= SMALL_COUNT or abs(a.count - b.count) > max(2, round(ABOUT_TOLERANCE * hi)):
        return None
    return round((a.count + b.count) / 2)


async def _count(s: Session, question: str) -> dict:
    """Two frames counted in parallel; a number only when they agree. Off by one: a third read on the newest
    frame breaks the tie. Anything else is "not sure" plus how to hold the camera. Never an invented count."""
    tgt = count_target(question)
    frames = await s.request_frames("count", count=COUNT_FRAMES, interval_ms=COUNT_INTERVAL_MS,
                                    timeout_s=FRAME_TIMEOUT_S)
    diag("camera_ask", stage="frames", session=s.session_id, kind="count", lang=s.lang, frames=len(frames),
         bytes=[len(f.jpeg_b64) for f in frames], sizes=[[f.w, f.h] for f in frames])
    if not frames:
        diag("camera_ask", severity="WARNING", stage="no_frames", session=s.session_id, kind="count",
             timeout_s=FRAME_TIMEOUT_S)
        return {"say": t("camera.none", s.lang), "answer": "", "kind": "count", "count": None, "agreed": False,
                "objects": [], "confidence": 0.0}
    pair = [frames[0], frames[-1]] if len(frames) > 1 else [frames[0], frames[0]]
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
    if n is None:
        say = t("cam.count_unsure", s.lang)
        if hint and hint != "vision.aim.move_back":            # "step back" is already in the line
            say = f"{say} {t(hint, s.lang)}"
        return {"say": say, "answer": "", "kind": "count", "count": None, "agreed": False,
                "objects": [], "confidence": conf, "aim_hint": hint}
    key = "cam.count_about" if approx is not None else "cam.count"
    say = (t(f"{key}_label", s.lang, n=n, label=tgt.phrase) if tgt.phrase else t(key, s.lang, n=n))
    if hint:
        say = f"{say} {t(hint, s.lang)}"
    return {"say": say, "answer": say, "kind": "count", "count": n, "agreed": True, "approx": approx is not None,
            "objects": shown.objects if shown else [], "confidence": conf}


async def camera_ask(question: str) -> dict:
    """Look through the camera and answer the owner's question as guidance: what is in front of him, where
    something is (left / ahead / right, how far), how many of something are visible, whether something is in
    his way, or read a label or sign.

    Call it for ANY question about what is around the owner or in front of the camera, including "how many
    ... are there?". The reply is in the session language; when the picture is unclear it says "not sure" and
    how to aim. Speak only its 'say': never guess a count or an object yourself.
    """
    s: Session = get_session()
    kind = classify(question)
    precounted = await precount.answer(s, question)  # "how many": the background count of this scene, if any
    if precounted is not None:
        return precounted
    async with owner_first(s):
        if kind == "count":
            return await _count(s, question)
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
        return {"say": t("camera.none", s.lang), "answer": "", "kind": kind, "objects": [], "confidence": 0.0}
    try:
        if kind == "read":
            out = await _read_twice(frames[-1].jpeg_b64, s.lang)
            diag("camera_ask", stage="done", session=s.session_id, kind=kind, confidence=out["confidence"],
                 agreed=out.get("agreed"), aim_hint=out.get("aim_hint"), say=trunc(out["say"]))
            return out
        if kind == "list":
            out = await listing.list_shelf(s, frames[-1].jpeg_b64, question)
            diag("camera_ask", stage="done", session=s.session_id, kind=kind, verdict="list",
                 confidence=out.get("confidence"), say=trunc(out.get("say", "")))
            return out
        parts = [image_part(f.jpeg_b64) for f in frames]
        parts.append(ASK_PROMPT.format(question=question, lang=LANG_NAME.get(s.lang, "English"),
                                       hints=", ".join(HINT_KEYS)))
        # The owner is waiting: low thinking, LOOK_TIMEOUT_S per attempt and a single retry (worst case ~8 s,
        # not 3 x 6 s).
        data = await generate_json("point", parts, ASK_SCHEMA, thinking="low", timeout_s=LOOK_TIMEOUT_S,
                                   max_attempts=2)
    except VisionError as exc:
        log.warning("camera_ask: vision failed for %s: %s", s.shop_id, exc)
        diag("camera_ask", severity="WARNING", stage="error", session=s.session_id, kind=kind,
             error=type(exc).__name__, message=trunc(str(exc), 200))
        return {"say": t("live.unavailable", s.lang), "answer": "", "kind": kind, "objects": [], "confidence": 0.0}

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
        say = f"{t('cam.not_sure', s.lang)} {t(hint, s.lang)}"
        diag("camera_ask", stage="done", session=s.session_id, kind=kind, verdict="not_sure", confidence=conf,
             aim_hint=hint, say=trunc(say))
        return {"say": say, "answer": "", "kind": kind, "objects": objects, "confidence": conf, "aim_hint": hint}
    say = _say_path(steps, objects, s.lang) if kind == "path" else _say_look(answer, steps, objects, s.lang)
    diag("camera_ask", stage="done", session=s.session_id, kind=kind, verdict="answer", confidence=conf,
         objects=len(objects), say=trunc(say))
    return {"say": say, "answer": answer, "guidance": steps, "kind": kind, "objects": objects, "confidence": conf}
