"""Pre-count: count the packs in view in the background as soon as the camera settles on a new scene.

An exact ER 2 count takes 14-21 s. The phone's change detector marks a live frame ``changed`` when the view
settles on something new (the fridge, a crate); Live already says what it is from the video (no numbers). In the
background this module asks the phone for one sharp ``count`` frame and runs ``count_in_frame`` on it, so when
Sam then asks "how many ...?", the answer is ready (or already on its way).

- Scenes: the ``ts`` of the ``changed`` live frame is the scene id. A newer change invalidates the cached count.
- One count in flight per session. A change during a count marks the newer scene as wanted; when the running
  count ends (its result is dropped: its scene is gone) the wanted scene is counted next.
- ``cached(session_id, product)`` -> (count, confidence, age_s) for the current scene, the objects filtered by
  the product's label words; ``answer(session, kind, item, product)`` serves a ``kind="count"`` camera_ask from it, waiting for a
  count still in flight.
- Diagnostics: ``precount`` lines (started / done / served / invalidated, ms). Never any picture.
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
GENERIC = {"packet", "packets", "pack", "packs", "item", "items", "object", "objects", "product", "products",
           "box", "boxes", "bottle", "bottles", "pouch", "pouches", "carton", "cartons", "thing", "things"}
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
        result = await counter(frames[-1], LABEL)
        ms = round((_monotonic() - started) * 1000)
        if st.scene != scene:  # the view changed meanwhile: this count belongs to nothing any more
            diag("precount", event="stale", session=session.session_id, ms=ms)
            return
        st.result, st.counted_scene, st.done_at = result, scene, _monotonic()
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


def cached(session_id: str, product: str | None = None) -> tuple[int, float, float] | None:
    """(count, confidence, age_s) of the current scene's pre-count, filtered by ``product``'s label words.

    None when there is no usable count for this scene, or when ``product`` names something the counted labels
    cannot tell apart (labels of other products)."""
    st = _STATE.get(session_id)
    if st is None or st.result is None or st.counted_scene != st.scene:
        return None
    age = _monotonic() - st.done_at
    r = st.result
    if age > MAX_AGE_S or getattr(r, "count", None) is None or getattr(r, "needs_retake", False):
        return None
    objects = list(getattr(r, "objects", None) or [])
    want = _words(product or "")
    if not want:
        return int(r.count), float(r.confidence), age
    matched = [o for o in objects if want & _words(str(o.get("label", "")))]
    if matched:
        return len(matched), float(r.confidence), age
    if objects and all(not _words(str(o.get("label", ""))) for o in objects):
        return int(r.count), float(r.confidence), age  # generic labels only ("packet"): the total is the answer
    return None


def in_flight(session_id: str) -> asyncio.Task | None:
    st = _STATE.get(session_id)
    return st.task if st is not None and st.task_scene == st.scene else None


async def answer(session: Session, kind: str, item: str = "", product: str = "",
                 wait_s: float = WAIT_S) -> dict | None:
    """A camera_ask-shaped reply from the pre-count of the current scene when the model asked for a count
    (``kind`` "count"), or None (another kind, no count for this scene, or a product the count cannot tell
    apart). ``item`` / ``product`` are the model's own arguments; the owner's words are never parsed here."""
    if kind != "count":
        return None
    sid = session.session_id
    want = " ".join(w for w in (product, item) if w)
    task = in_flight(sid)
    if task is not None and cached(sid, want) is None:
        try:
            await asyncio.wait_for(asyncio.shield(task), wait_s)
        except (TimeoutError, asyncio.CancelledError, Exception):
            return None
    hit = cached(sid, want)
    if hit is None:
        return None
    n, conf, age = hit
    diag("precount", event="served", session=sid, count=n, confidence=conf, age_ms=round(age * 1000))
    say_en = f"I count {n} packs here."
    return {"kind": "count", "sure": True, "count": n, "approx": False, "exact": True, "label": item,
            "product": product, "hint": "", "say_en": say_en, "facts": say_en, "answer": str(n), "objects": [],
            "confidence": conf, "precounted": True}


def forget(session_id: str) -> None:
    st = _STATE.pop(session_id, None)
    if st is not None and st.task is not None:
        st.task.cancel()
