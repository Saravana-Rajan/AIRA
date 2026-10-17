"""Pre-count: count the packs in view in the background as soon as the camera settles on a new scene.

An exact ER 2 count takes 14-21 s. The phone's change detector marks a live frame ``changed`` when the view
settles on something new (the fridge, a crate); Live already says what it is from the video (no numbers). In the
background this module asks the phone for one sharp ``count`` frame and runs ``count_in_frame`` on it, so when
Sam then asks "how many ...?", the answer is ready (or already on its way).

- Scenes: the ``ts`` of the ``changed`` live frame is the scene id. A newer change invalidates the cached count.
- One count in flight per session. A change during a count marks the newer scene as wanted; when the running
  count ends (its result is dropped: its scene is gone) the wanted scene is counted next.
- ``lookup(session_id, want)`` -> ``Hit`` (count, confidence, age, unknown, frame ts / id, scene) for the current
  scene; ``cached(session_id, product)`` -> (count, confidence, age_s), the same filtered view as a tuple;
  ``answer(session, kind, item, product)`` serves a ``kind="count"`` camera_ask from it, waiting for a count
  still in flight.
- Diagnostics: ``precount`` lines (started / done / served / invalidated, ms). Never any picture.

Safety design (a pre-count is ONE read of ONE frame, taken when the view settled, before the owner asked):

- It is never exact and never verified. ``answer`` serves it only as a clearly labelled provisional estimate:
  ``approx`` true, ``exact`` / ``verified`` false, ``source`` "background_precount", ``observed_at`` (the count
  frame's capture ts) and ``scene``; ``say_en`` says "about N ... in the view I saw a moment ago".
- It is served at most ONCE per scene. A second count request for the same scene ("are you sure?", "count
  again") gets the fresh two-frame count of ``tools.camera._count``, never the cache again.
- Product identity must come from the labels. A request word that is not a generic pack word ("milk",
  "Hatsun", but also container types like "carton" / "bottle", which the labels must name) has to be on a
  pack's label for that pack to count. Generic labels ("packet") or labels that cannot tell (an unreadable
  "unknown" pack, or a label that is only part of the request: "Hatsun" for "Hatsun curd") make a specific
  request unanswerable here: the fresh count runs instead. A generic request ("how many packets / items")
  gets the total, with the unreadable packs counted and reported (``unknown``), never silently dropped.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aira_live.diag import diag

if TYPE_CHECKING:
    from aira_live.frames import Frame
    from aira_live.session import Session

log = logging.getLogger(__name__)

LABEL = "packet"            # what the background count looks for: every pack, any brand
FRAME_TIMEOUT_S = 4.0
WAIT_S = 20.0               # "how many" while the count is still running: wait this long at most
MIN_GAP_S = 2.0             # at most one count started per 2 s per session
MAX_AGE_S = 120.0           # a count older than this is not served, scene or not
# Words that name no product or item type: a request made only of these is a generic "how many packs". Container
# types (box, bottle, pouch, carton) are NOT generic: "milk carton" must not be answered with every pack in view.
GENERIC = {"packet", "packets", "pack", "packs", "item", "items", "object", "objects", "product", "products",
           "thing", "things"}
UNKNOWN_LABELS = {"", "unknown", "unreadable", "unclear", "n/a", "none", "?"}  # as vision.count
# Filler in the model's item / product arguments and in pack labels: never a product word.
_STOP = {"the", "a", "an", "of", "my", "this", "that", "these", "those", "and", "fridge", "shelf"}
_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

Counter = Callable[["Frame", str], Awaitable[Any]]
_monotonic: Callable[[], float] = time.monotonic   # tests swap in a clock


@dataclass
class _State:
    scene: float = 0.0                       # current scene id (ts of the last changed live frame); 0 = none
    counted_scene: float | None = None       # the scene the cached result belongs to
    result: Any = None                       # CountResult
    done_at: float = 0.0
    task: asyncio.Task | None = None
    task_scene: float | None = None
    wanted: float | None = None              # a newer scene to count once the running count ends
    last_start: float = float("-inf")
    frame_ts: float | None = None            # capture ts (phone clock) of the frame the cached result read
    frame_id: str | None = None
    served_scene: float | None = None        # the scene whose pre-count was already served (once per scene)


@dataclass
class Hit:
    count: int
    confidence: float
    age: float
    unknown: int            # unreadable packs (counted in a generic total; a specific request is never served)
    hidden: int
    edge: int
    frame_ts: float | None
    frame_id: str | None
    scene: float


_STATE: dict[str, _State] = {}


def _default_counter() -> Counter:
    from aira_live.vision.count import count_in_frame
    return count_in_frame


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP and w not in GENERIC}


def scene_changed(session: Session, scene: float, *, counter: Counter | None = None) -> None:
    """A live frame marked ``changed``: a new scene. Drops the old count and starts (or queues) a new one."""
    st = _STATE.setdefault(session.session_id, _State())
    if scene <= st.scene:
        return
    if st.counted_scene is not None or st.task is not None:
        diag("precount", event="invalidated", session=session.session_id, had_result=st.result is not None,
             running=st.task is not None)
    st.scene, st.counted_scene, st.result = scene, None, None
    if st.task is not None:
        st.wanted = scene
        return
    _start(session, st, counter)


def _start(session: Session, st: _State, counter: Counter | None) -> None:
    now = _monotonic()
    if now - st.last_start < MIN_GAP_S:
        st.wanted = st.scene
        # retried when the next live frame arrives (``tick``)
        return
    st.last_start, st.wanted, st.task_scene = now, None, st.scene
    st.task = asyncio.get_running_loop().create_task(
        _run(session, st, st.scene, counter or _default_counter()), name=f"precount-{session.session_id}")


def tick(session: Session, *, counter: Counter | None = None) -> None:
    """Every live frame: start a wanted count once the rate limit allows it."""
    st = _STATE.get(session.session_id)
    if st is not None and st.task is None and st.wanted is not None and st.wanted == st.scene:
        _start(session, st, counter)


async def _run(session: Session, st: _State, scene: float, counter: Counter) -> None:
    started = _monotonic()
    diag("precount", event="started", session=session.session_id)
    try:
        frames = await session.request_frames("count", timeout_s=FRAME_TIMEOUT_S)
        if not frames:
            diag("precount", event="no_frame", session=session.session_id)
            return
        frame = frames[-1]
        result = await counter(frame, LABEL)
        ms = round((_monotonic() - started) * 1000)
        if st.scene != scene:  # the view changed meanwhile: this count belongs to nothing any more
            diag("precount", event="stale", session=session.session_id, ms=ms)
            return
        st.result, st.counted_scene, st.done_at = result, scene, _monotonic()
        st.frame_ts, st.frame_id = getattr(frame, "ts", None), getattr(frame, "id", None)
        diag("precount", event="done", session=session.session_id, ms=ms, count=getattr(result, "count", None),
             confidence=getattr(result, "confidence", None))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # a failed pre-count only means camera_ask counts the slow way
        diag("precount", severity="WARNING", event="error", session=session.session_id, error=type(exc).__name__)
    finally:
        st.task, st.task_scene = None, None
        if st.wanted is not None and st.wanted == st.scene and _STATE.get(session.session_id) is st:
            st.last_start = float("-inf")  # the wanted scene goes next, at once
            _start(session, st, counter)


def _label_state(label: str, want: set[str]) -> str:
    """"match" (every request word on the label), "unsure" (an unreadable or generic-only label, or a label that is
    only part of the request: it may be the requested product) or "other" (a different product)."""
    if label.strip().casefold() in UNKNOWN_LABELS:
        return "unsure"
    words = _words(label)
    if not words:
        return "unsure"
    if want <= words:
        return "match"
    return "unsure" if words < want else "other"


def lookup(session_id: str, want: str | None = None) -> Hit | None:
    """The current scene's pre-count for ``want`` (the model's product + item words), or None.

    None when there is no usable count for this scene, or when ``want`` names a product / item type the counted
    labels cannot establish (see the module's safety design). A generic ``want`` gets the total."""
    st = _STATE.get(session_id)
    if st is None or st.result is None or st.counted_scene != st.scene:
        return None
    age = _monotonic() - st.done_at
    r = st.result
    if age > MAX_AGE_S or getattr(r, "count", None) is None or getattr(r, "needs_retake", False):
        return None
    objects = list(getattr(r, "objects", None) or [])
    unknown = sum(1 for o in objects if str(o.get("label", "")).strip().casefold() in UNKNOWN_LABELS)
    words = _words(want or "")

    def hit(n: int, unk: int) -> Hit:
        return Hit(count=n, confidence=float(r.confidence), age=age, unknown=unk,
                   hidden=int(getattr(r, "hidden", 0) or 0), edge=int(getattr(r, "edge", 0) or 0),
                   frame_ts=st.frame_ts, frame_id=st.frame_id, scene=st.counted_scene)

    if not words:
        return hit(int(r.count), max(unknown, int(getattr(r, "unknown", 0) or 0)))
    states = [_label_state(str(o.get("label", "")), words) for o in objects]
    if "unsure" in states or "match" not in states:
        return None  # nothing labelled as the request, or a pack that may be one more of it: not sure here
    return hit(states.count("match"), 0)


def cached(session_id: str, product: str | None = None) -> tuple[int, float, float] | None:
    """(count, confidence, age_s) of the current scene's pre-count for ``product`` (``lookup``)."""
    h = lookup(session_id, product)
    return (h.count, h.confidence, h.age) if h is not None else None


def in_flight(session_id: str) -> asyncio.Task | None:
    st = _STATE.get(session_id)
    return st.task if st is not None and st.task_scene == st.scene else None


def _what(item: str, product: str) -> str:
    """The English words of the answer: "Hatsun curd packets", "packets"; "packs" when nothing was named."""
    text = " ".join(w for w in (product, item) if w).strip()
    if not text:
        return "packs"
    return text if text.endswith("s") else text + "s"


async def answer(session: Session, kind: str, item: str = "", product: str = "",
                 wait_s: float = WAIT_S) -> dict | None:
    """A provisional camera_ask-shaped reply from the pre-count of the current scene when the model asked for a
    count (``kind`` "count"), or None (another kind, no count for this scene, already served once for this scene,
    or a product the labels cannot establish). Never exact, never verified. ``item`` / ``product`` are the
    model's own arguments; the owner's words are never parsed here."""
    if kind != "count":
        return None
    sid = session.session_id
    st = _STATE.get(sid)
    if st is not None and st.served_scene is not None and st.served_scene == st.scene:
        return None  # served once already: a second ask for this view gets a fresh count
    want = " ".join(w for w in (product, item) if w)
    task = in_flight(sid)
    if task is not None and lookup(sid, want) is None:
        try:
            await asyncio.wait_for(asyncio.shield(task), wait_s)
        except (TimeoutError, asyncio.CancelledError, Exception):
            return None
    h = lookup(sid, want)
    st = _STATE.get(sid)
    if h is None or st is None or st.served_scene == h.scene:
        return None
    st.served_scene = h.scene
    n = h.count
    diag("precount", event="served", session=sid, count=n, confidence=h.confidence, age_ms=round(h.age * 1000),
         unknown=h.unknown)
    say_en = (f"About {n} {_what(item, product)} in the view I saw a moment ago. That was one quick look, not a "
              f"checked count; ask me to count again for a careful count.")
    if h.unknown:
        say_en += f" I could not read the label on {h.unknown} of them."
    return {"kind": "count", "sure": True, "count": n, "visible_count": n, "approx": True, "exact": False,
            "verified": False, "provisional": True, "agreed": False, "scope": "visible_now",
            "source": "background_precount", "observed_at": h.frame_ts,
            "frame_ids": [h.frame_id] if h.frame_id else [], "scene": h.scene, "age_s": round(h.age, 1),
            "evidence": {"frames": 1, "reads": 1, "agreed": False, "unknown": h.unknown, "hidden": h.hidden,
                         "edge": h.edge},
            "label": item, "product": product, "hint": "", "say_en": say_en, "facts": say_en, "answer": say_en,
            "objects": [], "confidence": h.confidence, "precounted": True}


def forget(session_id: str) -> None:
    st = _STATE.pop(session_id, None)
    if st is not None and st.task is not None:
        st.task.cancel()
