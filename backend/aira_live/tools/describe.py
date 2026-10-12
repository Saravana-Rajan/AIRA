"""Describe mode: live narration of what comes into view while the owner walks (spec "next-describe-mode" §2).

``describe_mode(on=True)`` ("describe as I walk", "keep telling me what's around") asks the phone for a
low-rate continuous stream of ``describe`` frames: one every 3 s, and only when the scene changed (the app
gates on frame difference or on the motion sensor). The server runs ER 2 on each frame, keeps a short memory
of what it already announced (label + rough side, 20 s TTL) and speaks ONLY new or changed objects, at most
one short sentence every 4 s: "Milk crates to your left, a few steps away." It pauses while the owner is
talking or AIRA is answering, stops on ``describe_mode(on=False)`` ("stop describing"), after 10 minutes, or
after the per-session ER 2 budget is spent.

Danger stays with the phone's bump guard: this mode never says "safe" or "clear" and never describes people.
Every ER 2 result is also handed to ``vision.observers.on_vision_objects`` for the "last seen" memory.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aira_live import sessions
from aira_live.contracts import RequestFrameMsg, TranscriptMsg
from aira_live.i18n import t
from aira_live.live_bridge import on_input_transcript
from aira_live.session import Session, get_session
from aira_live.tools.camera import MIN_CONF, dist_key, nearest_first, parse_objects, side_key
from aira_live.vision import listing, observers
from aira_live.vision.gemini import VisionError, generate_json, image_part

log = logging.getLogger(__name__)

PURPOSE = "describe"
INTERVAL_S = 3.0          # one look per describe frame; the phone sends at most one frame every 3 s
FRAME_TIMEOUT_S = 3.5
MIN_GAP_S = 4.0           # at most one short sentence every 4 s
TTL_S = 20.0              # an object announced within 20 s is not announced again unless it moved
MAX_S = 600.0             # auto-stop after 10 minutes
MAX_CALLS = 30            # ER 2 calls per describe run (cost control, spec §3)
PAUSE_AFTER_TALK_S = 2.0  # the owner just finished a sentence: let AIRA answer first
MAX_OBJECTS = 4

DESCRIBE_PROMPT = (
    "You are the eyes of a blind shopkeeper wearing this chest camera while he walks. List the main objects "
    "in view (at most 4, nearest and most useful first): furniture, doors, steps, crates, shelves, fridges, "
    "bags, keys, phones, tools. For each give a short plain label, its point [y, x] normalized 0-1000 (x: 0 is "
    "his left, 1000 his right), and its "
    "distance: near (within reach), arm (an arm's length), far (a step or more away). Walls, floor, ceiling and "
    "sky are not objects. Never "
    "describe or identify people. If the image is dark or blurred, return no objects and confidence below 0.5."
)
DESCRIBE_SCHEMA = ('{"objects": [{"label": "<name>", "point": [y, x], '
                   '"distance": "near|arm|far"}], "confidence": 0.0}')

_ON = re.compile(r"(describ|narrat|tell me what.?s around|keep telling|what.?s around me as i|"
                 r"விவரி|சொல்லிக்கொண்டே|बताते रह|बताओ क्या है आसपास)", re.I)
_OFF = re.compile(r"(stop|enough|quiet|don.?t|no more|நிறுத்து|போதும்|बंद|रुक|बस)", re.I)
_WS = re.compile(r"\s+")

_ACTIVE: dict[str, "DescribeLoop"] = {}


def parse_describe_command(text: str) -> bool | None:
    """True for "describe as I walk" / "keep telling me what's around", False for "stop describing", else None.
    A sentence that mentions describing and a stop word means stop."""
    q = _WS.sub(" ", text or "").strip()
    if not q or not _ON.search(q):
        return None
    return not _OFF.search(q)


def coarse_side(x: float) -> str:
    """"left", "ahead" or "right" from an x of 0-1000: the memory key, coarser than the spoken side."""
    return "left" if x < 400 else "right" if x > 600 else "ahead"


def phrase(obj: dict, lang: str) -> str:
    """One short sentence: "Milk crates to your left, a few steps away." in the session language."""
    label = obj["label"]
    label = label[0].upper() + label[1:] if label else label
    return t("describe.item", lang, label=label, side=t(side_key(obj["point"][1]), lang),
             dist=t(dist_key(obj["distance"]), lang))


class DescribeLoop:
    def __init__(self, session: Session, *, interval_s: float = INTERVAL_S, frame_timeout_s: float = FRAME_TIMEOUT_S,
                 min_gap_s: float = MIN_GAP_S, ttl_s: float = TTL_S, max_s: float = MAX_S,
                 max_calls: int = MAX_CALLS, busy: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.session = session
        self.interval_s, self.frame_timeout_s = interval_s, frame_timeout_s
        self.min_gap_s, self.ttl_s, self.max_s, self.max_calls = min_gap_s, ttl_s, max_s, max_calls
        self._busy, self._clock = busy, clock
        self.active = True
        self.task: asyncio.Task | None = None
        self.last_ts = 0.0                 # newest describe frame already looked at (phone clock)
        self.calls = 0                     # ER 2 calls so far
        self.spoken = 0
        self.last_spoken_at = float("-inf")
        self.last_heard_at = float("-inf")  # when the owner last finished an utterance
        self.started_at = clock()
        self.announced: dict[tuple[str, str], tuple[float, str]] = {}  # (label, side) -> (at, distance)
        self._unsubscribe: Callable[[], None] | None = None

    # ---------- what to say ----------
    def new_items(self, objects: list[dict], now: float | None = None) -> list[dict]:
        """Objects not announced in the last ``ttl_s`` (same label and rough side), or whose distance changed.
        Nearest first."""
        now = self._clock() if now is None else now
        for key, (at, _) in list(self.announced.items()):
            if now - at > self.ttl_s:
                del self.announced[key]
        fresh: list[dict] = []
        for obj in nearest_first(objects):
            key = (obj["label"].casefold(), coarse_side(obj["point"][1]))
            seen = self.announced.get(key)
            if seen is None or seen[1] != obj["distance"]:
                fresh.append(obj)
        return fresh

    def remember(self, obj: dict, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        self.announced[(obj["label"].casefold(), coarse_side(obj["point"][1]))] = (now, obj["distance"])

    def busy(self) -> bool:
        """True while the owner is talking (or just finished) or AIRA is speaking: nothing is said then."""
        if self._clock() - self.last_heard_at < PAUSE_AFTER_TALK_S:
            return True
        if self._busy is not None:
            return bool(self._busy())
        return _bridge_busy(self.session)

    # ---------- the loop ----------
    async def step(self) -> bool:
        """One tick: newest unseen describe frame -> ER 2 -> speak at most one new object. False once stopped."""
        if not self.active:
            return False
        s = self.session
        fresh = await s.frames.wait_for(count=1, purpose=PURPOSE, after_ts=self.last_ts,
                                        timeout_s=self.frame_timeout_s)
        if not fresh or not self.active:
            return self.active
        newest = s.frames.latest(1, purpose=PURPOSE)
        frame = newest[-1] if newest and newest[-1].ts > self.last_ts else fresh[-1]
        self.last_ts = frame.ts
        if self.busy():
            return self.active                       # the frame is skipped: no ER 2 call while someone talks
        if self.calls >= self.max_calls:
            if await self._finish():
                await self._say(t("describe.cap", s.lang), cue="done")
            return False
        self.calls += 1
        try:
            data = await generate_json("point", [image_part(frame.jpeg_b64), DESCRIBE_PROMPT], DESCRIBE_SCHEMA,
                                       thinking="low", timeout_s=6.0)
        except VisionError as exc:
            log.warning("describe: vision failed for %s: %s", s.shop_id, exc)
            return self.active
        if not self.active:
            return False
        objects = parse_objects(data.get("objects"))
        conf = _conf(data.get("confidence"))
        log.info("describe: session=%s call=%d model=%s objects=%d conf=%.2f", s.session_id, self.calls,
                 data.get("_model"), len(objects), conf)
        await observers.on_vision_objects(s, objects, {"source": "describe", "frame_id": frame.id,
                                                       "frame_ts": frame.ts, "confidence": conf,
                                                       "model": data.get("_model")})
        await listing.publish_boxes(s, objects, "describe", frame_id=frame.id)
        if conf < MIN_CONF or not objects:
            return self.active
        now = self._clock()
        if now - self.last_spoken_at < self.min_gap_s or self.busy():
            return self.active
        fresh_items = self.new_items(objects, now)
        if not fresh_items:
            return self.active
        obj = fresh_items[0]
        self.remember(obj, now)
        self.last_spoken_at = now
        self.spoken += 1
        await self._say(phrase(obj, s.lang))
        return self.active

    async def run(self) -> None:
        """Tick until stopped; a broken tick is logged and the loop goes on. Stops itself after ``max_s``, and
        when the session is gone (the phone disconnected)."""
        deadline = self.started_at + self.max_s
        while self.active:
            if self._clock() >= deadline:
                if await self._finish():
                    await self._say(t("describe.timeout", self.session.lang), cue="done")
                return
            if not _registered(self.session):
                await self._finish()
                return
            try:
                if not await self.step():
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("describe step failed for %s", self.session.session_id)
            await asyncio.sleep(self.interval_s)

    async def on_utterance(self, text: str) -> None:
        """Transcript listener: the owner spoke (pause), and "stop describing" ends the mode directly."""
        self.last_heard_at = self._clock()
        if parse_describe_command(text) is False:
            if await self._finish():
                await self._say(t("describe.off", self.session.lang), cue="done")

    async def _say(self, text: str, cue: str | None = None) -> None:
        """Caption + live voice for this session only (no earcon per object: that would be noise)."""
        s = self.session
        try:
            await s.send(TranscriptMsg(role="aira", text=text, final=True, lang=s.lang))
            bridge = _bridge_of(s)
            if bridge is not None:
                await bridge.speak_notice(text)
        except Exception:
            log.warning("describe: session %s unreachable", s.session_id, exc_info=True)

    async def _finish(self) -> bool:
        sid = self.session.session_id
        if _ACTIVE.get(sid) is self:
            del _ACTIVE[sid]
        was_active = self.active
        await self.stop()
        return was_active

    async def stop(self) -> None:
        """Stop ticking and ask the app to stop the describe frames. Safe to call twice and on a closed socket."""
        if not self.active:
            return
        self.active = False
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self.task is not None and self.task is not asyncio.current_task() and not self.task.done():
            self.task.cancel()
        try:
            await self.session.send(RequestFrameMsg(purpose=PURPOSE, stop=True))
        except Exception:
            log.warning("describe: could not send stop to session %s", self.session.session_id, exc_info=True)


def _conf(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f > 1.0:
        f = f / 100.0 if f <= 100.0 else 0.0
    return min(max(f, 0.0), 1.0)


def _bridge_of(session: Session):
    for s, bridge in sessions.by_shop(session.shop_id):
        if s is session:
            return bridge
    return None


def _registered(session: Session) -> bool:
    return any(s is session for s, _ in sessions.by_shop(session.shop_id))


def _bridge_busy(session: Session) -> bool:
    """AIRA is mid-answer or the owner is mid-sentence, as far as the Live bridge knows."""
    bridge = _bridge_of(session)
    if bridge is None:
        return False
    return bool(getattr(bridge, "_answering", False)) or bool(getattr(bridge, "_in_partial", ""))


async def start(session: Session, run: bool = True, **kw) -> DescribeLoop:
    """Replace any describe loop of this session: ask the app for continuous describe frames and start."""
    await stop_describe(session)
    loop = DescribeLoop(session, **kw)
    await session.send(RequestFrameMsg(purpose=PURPOSE, continuous=True))
    _ACTIVE[session.session_id] = loop
    loop._unsubscribe = on_input_transcript(session.session_id, loop.on_utterance)
    if run:
        loop.task = asyncio.create_task(loop.run(), name=f"describe-{session.session_id}")
    return loop


def active(session: Session) -> DescribeLoop | None:
    """The running describe loop of ``session``, or None."""
    return _ACTIVE.get(session.session_id)


async def stop_describe(session: Session) -> bool:
    """Stop describe mode for ``session``. True when something was running. Safe to call twice."""
    loop = _ACTIVE.pop(session.session_id, None)
    if loop is None:
        return False
    await loop.stop()
    return True


async def describe_mode(on: bool) -> dict:
    """Switch live narration on or off. on=True when the owner says "describe as I walk", "keep telling me
    what's around" or "guide me as I move": AIRA then speaks, by itself, each new object that comes into view
    (what it is, which side, how far) and stays quiet about things it already said. on=False for "stop
    describing", "enough", "quiet". It stops by itself after ten minutes.
    """
    s: Session = get_session()
    from aira_live.tools import ambient  # ambient mode owns the scene loop now (and the hold-up loop)
    await ambient.set_mode(s, on)
    if on:
        return {"say": t("describe.on", s.lang), "active": True}
    return {"say": t("describe.off", s.lang), "active": False}
