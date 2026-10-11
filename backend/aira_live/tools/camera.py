"""Live tool camera_ask: open questions about what the camera sees, answered as guidance for a blind owner.

The Live model only hears audio. For "what's in front of me?", "is my path clear?", "read this label" or
"where is the door?" it calls this tool, which takes a fresh frame from the chest camera and asks the
``point`` model (Robotics-ER 2, Flash fallback through ``generate_json``) for objects with [y, x] points
(0-1000) and distances. The spoken line is built here, not by the model: a side (left / ahead / right), a
distance (arm's length, a few steps) and the next action, in the session language.

Rules: low confidence or a bad frame says "not sure" plus an aim hint, never a guess. "Read this" is read
twice (``read`` and ``verify`` roles) and spoken verbatim only when both reads agree. A path question names
the obstacle and its direction and never says "safe" or "clear".
"""
from __future__ import annotations

import logging
import re
from typing import Any

from aira_live.diag import diag, summarize, trunc
from aira_live.i18n import t
from aira_live.prompt import LANG_NAME
from aira_live.session import Session, get_session
from aira_live.vision.gemini import VisionError, generate_json, image_part
from aira_live.vision.hints import HINT_KEYS

log = logging.getLogger(__name__)

MIN_CONF = 0.5
MAX_OBJECTS = 2
FRAME_TIMEOUT_S = 4.0

READ_WORDS = ("read", "what does it say", "what does this say", "label", "text", "written", "price tag",
              "expiry", "படி", "எழுத", "पढ़", "लिखा")
PATH_WORDS = ("path", "way clear", "walk", "step", "in my way", "obstacle", "trip", "move forward", "go forward",
              "ahead of me", "வழி", "நடக்க", "रास्ता", "चल")

ASK_PROMPT = (
    "You are the eyes of a blind shopkeeper wearing this chest camera in a small dairy shop. "
    "Question: \"{question}\". Answer in {lang}. Give short guidance a blind person can act on, not a "
    "description: name each relevant object with its point [y, x] normalized 0-1000 (x: 0 is the owner's left, "
    "1000 the right) and its distance: near (within reach), arm (an arm's length), far (a step or more away). "
    "guidance lists the next one or two actions. Never describe or identify people. If the image is dark, "
    "blurred or the thing asked about is not visible, set confidence below 0.5 and aim_hint to one of "
    "{hints}."
)
ASK_SCHEMA = ('{"answer": "<one short sentence>", "guidance": ["<step>"], '
              '"objects": [{"label": "<name>", "point": [y, x], "distance": "near|arm|far"}], '
              '"confidence": 0.0, "aim_hint": "<optional key>"}')
READ_PROMPT = (
    "Read every printed text in this image exactly as written, in reading order, as one line. "
    "Return the text verbatim: no translation, no summary, no guess. If nothing is readable, return an empty text."
)
READ_SCHEMA = '{"text": "<verbatim text>", "confidence": 0.0}'
_WS = re.compile(r"\s+")


def classify(question: str) -> str:
    """One of "read", "path" or "look" from the owner's words (any language falls back to "look")."""
    q = question.lower()
    if any(w in q for w in READ_WORDS):
        return "read"
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


def _objects(raw: Any) -> list[dict]:
    """Only well-formed objects: a label, a [y, x] point inside 0-1000 and a distance word."""
    out: list[dict] = []
    for o in raw if isinstance(raw, list) else []:
        if not isinstance(o, dict):
            continue
        p = o.get("point")
        label = str(o.get("label") or "").strip()
        if not label or not (isinstance(p, (list, tuple)) and len(p) == 2
                             and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                     and 0 <= v <= 1000 for v in p)):
            continue
        d = str(o.get("distance") or "").lower()
        out.append({"label": label, "point": [round(p[0]), round(p[1])], "distance": d if d in ("near", "arm", "far") else "arm"})
    return out


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
    """1-3 short sentences: the answer, where the nearest object is, the next action."""
    parts: list[str] = []
    if answer:
        parts.append(_sentence(answer))
    for obj in sorted(objects, key=lambda o: ("near", "arm", "far").index(o["distance"]))[:MAX_OBJECTS]:
        if len(parts) >= 2:
            break
        parts.append(_where(obj, lang))
    if steps and len(parts) < 3:
        parts.append(_sentence(steps[0]))
    return " ".join(parts) if parts else t("cam.not_sure", lang)


def _say_path(steps: list[str], objects: list[dict], lang: str) -> str:
    """The obstacle and its direction. Never the model's own words: those could say "safe"."""
    if not objects:
        return t("cam.path_nothing", lang)
    nearest = sorted(objects, key=lambda o: ("near", "arm", "far").index(o["distance"]))[0]
    line = t("cam.obstacle", lang, label=nearest["label"], side=t(side_key(nearest["point"][1]), lang),
             dist=t(dist_key(nearest["distance"]), lang))
    step = next((s for s in steps if not re.search(r"\bsafe\b|\bclear\b", s, re.I)), "")
    return f"{line} {_sentence(step)}".strip() if step else line


async def _read_twice(frame_b64: str, lang: str) -> dict:
    """Two independent reads (read + verify models) must agree, else "not sure" and the label hint."""
    reads = []
    for role in ("read", "verify"):
        data = await generate_json(role, [image_part(frame_b64), READ_PROMPT], READ_SCHEMA, timeout_s=6.0)
        reads.append((str(data.get("text") or "").strip(), _conf(data.get("confidence"))))
    texts = [_WS.sub(" ", r[0]).casefold() for r in reads]
    conf = min(r[1] for r in reads)
    if not texts[0] or texts[0] != texts[1] or conf < MIN_CONF:
        return {"say": f"{t('cam.not_sure', lang)} {t('vision.aim.show_label', lang)}", "answer": "",
                "kind": "read", "confidence": conf, "agreed": bool(texts[0]) and texts[0] == texts[1],
                "aim_hint": "vision.aim.show_label"}
    return {"say": reads[0][0], "answer": reads[0][0], "kind": "read", "confidence": conf, "agreed": True}


async def camera_ask(question: str) -> dict:
    """Look through the camera and answer the owner's question as guidance: what is in front of him, where
    something is (left / ahead / right, how far), whether something is in his way, or read a label or sign.

    Call it for ANY question about what is around the owner or in front of the camera. The reply is in the
    session language; when the picture is unclear it says "not sure" and how to aim.
    """
    s: Session = get_session()
    kind = classify(question)
    count = 2 if kind == "path" else 1                          # a path needs a short burst: things move
    frames = await s.request_frames("ask", count=count, interval_ms=400 if count > 1 else 0,
                                    timeout_s=FRAME_TIMEOUT_S)
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
        parts = [image_part(f.jpeg_b64) for f in frames]
        parts.append(ASK_PROMPT.format(question=question, lang=LANG_NAME.get(s.lang, "English"),
                                       hints=", ".join(HINT_KEYS)))
        data = await generate_json("point", parts, ASK_SCHEMA, thinking="low", timeout_s=6.0)
    except VisionError as exc:
        log.warning("camera_ask: vision failed for %s: %s", s.shop_id, exc)
        diag("camera_ask", severity="WARNING", stage="error", session=s.session_id, kind=kind,
             error=type(exc).__name__, message=trunc(str(exc), 200))
        return {"say": t("live.unavailable", s.lang), "answer": "", "kind": kind, "objects": [], "confidence": 0.0}

    objects = _objects(data.get("objects"))
    conf = _conf(data.get("confidence"))
    steps = _steps(data.get("guidance"))
    answer = str(data.get("answer") or "").strip()
    raw_objects = data.get("objects")
    diag("camera_ask", stage="model", session=s.session_id, kind=kind, model=data.get("_model"),
         keys=sorted(k for k in data if k != "_model"), confidence=conf, raw_confidence=summarize(data.get("confidence")),
         objects=len(objects), raw_objects=len(raw_objects) if isinstance(raw_objects, list) else None,
         aim_hint=summarize(data.get("aim_hint")), answer=trunc(answer), steps=len(steps))
    if conf < MIN_CONF or data.get("aim_hint"):
        hint = _hint(data.get("aim_hint"))
        say = f"{t('cam.not_sure', s.lang)} {t(hint, s.lang)}"
        diag("camera_ask", stage="done", session=s.session_id, kind=kind, verdict="not_sure", confidence=conf,
             aim_hint=hint, say=trunc(say))
        return {"say": say, "answer": "", "kind": kind, "objects": objects, "confidence": conf, "aim_hint": hint}
    say = _say_path(steps, objects, s.lang) if kind == "path" else _say_look(answer, steps, objects, s.lang)
    diag("camera_ask", stage="done", session=s.session_id, kind=kind, verdict="answer", confidence=conf,
         objects=len(objects), say=trunc(say))
    return {"say": say, "answer": answer, "guidance": steps, "kind": kind, "objects": objects, "confidence": conf}
