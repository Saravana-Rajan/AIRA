""""Walk with me": ``walk_to`` / ``walk_stop`` Live tools and the background walk loop.

The owner says once "take me to the fridge"; ``walk_to`` asks the phone for continuous ``walk`` frames (about one
a second) and starts ``WalkLoop`` on the session, then returns at once (NON_BLOCKING) so Live keeps talking.
A new look starts every second (up to two in flight: ER takes 2-6 s) at the newest walk frame with
Robotics-ER (``vision.walk.look``); only the newest finished look is acted on. Each look, turns the look into ONE short
instruction in code (``vision.walk.decide``) and says it only when it changes, or every 4 s as a reassurance.
Safety lines ("Stop. Step in front.") go at once, and the phone's own MediaPipe boxes (5 a second) can say
"Stop." before ER answers. The walk ends when he arrives, when the Live model calls ``walk_stop`` (he asked to
stop in any language: the model decides, no word list here), on the phone's long-press stop, after 90 s, or after
three ticks in a row without a usable look ("I can't see well. Please stop and wait.").

Freshness: each look is judged by how long ago its frame was TAKEN, measured just before its line is said
(``capture_age_s``: server-side arrival time plus the phone's own capture-to-send delay, no clock sync needed).
``vision.walk.gate`` holds the per-instruction policy: stops always go; a step count only from a fresh look; turn
and direction lines up to about 3 s; older lines are never said. "Arrived" needs a fresh look, else "Stop. Hold
still." and a frame taken while he stood still confirms it. Three looks in a row too old to say ask him to slow
down. Not a guarantee: the upload time is not counted, Live may queue the line, and the limits need real-device
measurement.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable

from aira_live.contracts import CueMsg, RequestFrameMsg, TranscriptMsg
from aira_live.diag import diag
from aira_live.session import Session, get_session, notice_key
from aira_live.vision import walk as vwalk
from aira_live.vision.gemini import VisionError

log = logging.getLogger(__name__)

PURPOSE = "walk"
INTERVAL_S = 1.0
FRAME_TIMEOUT_S = 2.5
MAX_S = 90.0
MAX_FAILS = 3
MEMORY_TIMEOUT_S = 1.5
MAX_IN_FLIGHT = 2        # ER looks running at once (each takes 2-6 s; a new one starts every second)
FAST_STOP_GAP_S = 3.0
# Freshness (the per-instruction limits are vision.walk.WALK_MAX_AGE_S / WALK_DIRECTION_MAX_AGE_S):
FRESH_VIEW_AFTER = 3     # looks in a row too old to say anything but a stop -> ask him to slow down
HOLD_SETTLE_S = 1.0      # after "Stop. Hold still." a frame taken this long later shows him standing still
HOLD_MAX_S = 10.0        # a "hold still" older than this no longer counts; the next one starts a new hold
MAX_PHONE_DELAY_S = 5.0  # capture -> phone send stamp (``ts - capture_ts``) beyond this is not believed

_ACTIVE: dict[str, "WalkLoop"] = {}

Look = Callable[..., Awaitable[vwalk.WalkView]]


def capture_age_s(store, frame, now: float | None = None) -> float | None:
    """Seconds since ``frame`` was taken, without trusting the phone clock against the server's.

    age = (server now - server arrival of the frame) + (phone send stamp ``ts`` - phone capture stamp
    ``capture_ts``). Both server times are ``time.monotonic`` (FrameStore records the arrival); both phone times
    are the same phone clock, so neither difference needs synced clocks. The upload time (phone send -> server
    arrival) is not measurable without synced clocks and is NOT included: the age is a slight underestimate.
    Without ``capture_ts`` the phone part is 0 (``ts`` is stamped right after encoding). None (= treat as stale)
    when the frame's arrival here is unknown or its phone stamps are missing or impossible."""
    ts = getattr(frame, "ts", None)
    cap = getattr(frame, "capture_ts", None) or ts
    try:
        ts, cap = float(ts), float(cap)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(ts) and math.isfinite(cap)) or ts <= 0 or cap <= 0:
        return None
    phone_s = (ts - cap) / 1000.0
    if not 0.0 <= phone_s <= MAX_PHONE_DELAY_S:
        return None
    # FrameStore keeps (monotonic arrival, frame) for its recent frames; the walk frame is that same object.
    arrived = next((at for at, f in reversed(getattr(store, "_recent_frames", ())) if f is frame), None)
    if arrived is None:
        return None
    held = (time.monotonic() if now is None else now) - arrived
    return held + phone_s if held >= 0 else None


def walk_facts(ins: vwalk.Instruction, view: vwalk.WalkView, target: str,
               search: vwalk.Search | None = None) -> dict:
    """The instruction as structured facts, e.g. {"action": "stop", "obstacle": "chair", "move": "left"} or
    {"action": "go", "direction": "ahead", "steps_about": 3}. Live says them in the owner's language."""
    key = ins.key
    near = sorted((o for o in view.obstacles if vwalk.in_corridor(o.box)), key=lambda o: -o.box[2])
    if key == "walk.hazard":
        hazard = next((o.label for o in near if o.hazard), near[0].label if near else "")
        return {"action": "stop", "hazard": hazard}
    if key == "walk.arrived":
        return {"action": "arrived", "target": target, "reach": "forward"}
    if key == "walk.hold":
        return {"action": "stop", "hold_still": True, "reason": "checking_arrival", "target": target}
    if key.startswith("walk.obstacle_"):
        return {"action": "stop", "obstacle": near[0].label if near else "", "move": key.rsplit("_", 1)[-1]}
    if key in ("walk.left", "walk.right"):
        return {"action": "turn", "direction": key.split(".", 1)[1], "target": target}
    if key == "walk.ahead":
        return {"action": "go", "direction": "ahead", "steps_about": ins.steps, "target": target}
    if key == "walk.ahead_slow":
        return {"action": "go", "direction": "ahead", "slowly": True, "target": target}
    if key == "walk.not_found":
        return {"action": "search", "found": False, "target": target, "turn": "around slowly"}
    if key == "walk.memory":
        side = (search.memory_side if search else None) or "left"
        return {"action": "search", "target": target, "last_seen": search.memory_place if search else "",
                "turn": side}
    if key.startswith("walk.looking_"):
        return {"action": "search", "target": target, "turn": key.rsplit("_", 1)[-1]}
    return {"action": key.split(".", 1)[-1], "target": target}


def notice_text(meaning: str, facts: dict | None) -> str:
    """What goes to Live's notice path: the facts, then the English meaning as a hint. Numbers stay exact."""
    if not facts:
        return meaning
    shown = {k: v for k, v in facts.items() if v not in (None, "")}
    return f"{json.dumps(shown, ensure_ascii=False)} (meaning: {meaning})"


def _bridge_of(s: Session):
    from aira_live import sessions

    return next((b for sess, b in sessions.by_shop(s.shop_id) if sess.session_id == s.session_id), None)


class WalkLoop:
    def __init__(self, session: Session, target: str, *, look: Look | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable] = asyncio.sleep,
                 interval_s: float = INTERVAL_S, frame_timeout_s: float = FRAME_TIMEOUT_S, max_s: float = MAX_S,
                 memory_place: str | None = None, memory_side: str | None = None,
                 max_in_flight: int = MAX_IN_FLIGHT,
                 frame_age: Callable[[object], float | None] | None = None):
        self.session, self.target = session, target
        self._look = look or vwalk.look
        self._clock, self._sleep = clock, sleep
        # Seconds since a frame was taken, on the same time scale as ``clock`` (the default: both monotonic).
        self._frame_age = frame_age or (lambda frame: capture_age_s(session.frames, frame))
        self.stale_streak = 0            # looks in a row too old for anything but a stop
        self.hold_at: float | None = None  # when "Stop. Hold still." was said to confirm an arrival
        self.interval_s, self.frame_timeout_s, self.max_s = interval_s, frame_timeout_s, max_s
        self.started_at = clock()
        self.last_seen_at = self.started_at
        self.search = vwalk.Search(memory_place=memory_place, memory_side=memory_side)
        self.speech = vwalk.Speech("en")     # English meaning only: Live says it in the owner's language
        self.last_ts = 0.0               # newest frame taken (phone clock)
        self.acted_ts = 0.0              # newest frame whose look was acted on
        self.max_in_flight = max_in_flight
        self.fast_stop_at = float("-inf")
        self.fails = 0
        self.active = True
        self.ended: str | None = None     # why: arrived | stopped | timeout | blind
        self.task: asyncio.Task | None = None
        self._unsubscribe: Callable[[], None] | None = None

    # ---------- one tick ----------
    async def next_frame(self):
        """The newest walk frame not yet sent to ER, or None after ``frame_timeout_s`` without one."""
        s = self.session
        fresh = await s.frames.wait_for(count=1, purpose=PURPOSE, after_ts=self.last_ts,
                                        timeout_s=self.frame_timeout_s)
        if not fresh:
            return None
        newest = s.frames.latest(1, purpose=PURPOSE)
        frame = newest[-1] if newest and newest[-1].ts > self.last_ts else fresh[-1]
        self.last_ts = frame.ts
        return frame

    async def step(self) -> bool:
        """Sequential tick: newest unseen walk frame -> one ER look -> at most one line. False once ended."""
        if not self.active:
            return False
        frame = await self.next_frame()
        if not self.active:
            return False
        if frame is None:
            return await self._failed("no_frame")
        return await self.process(frame)

    async def process(self, frame) -> bool:
        """One ER look at ``frame`` and the line it gives. A result older than one already acted on (pipelined
        looks can finish out of order) is dropped."""
        s = self.session
        started = time.perf_counter()
        try:
            view = await self._look(frame, self.target)
        except VisionError as exc:
            log.warning("walk: look failed for %s: %s", s.session_id, exc)
            if frame.ts < self.acted_ts:
                return self.active       # a newer look already answered: this failure changes nothing
            return await self._failed("vision", round((time.perf_counter() - started) * 1000))
        ms = round((time.perf_counter() - started) * 1000)
        if not self.active:
            return False
        if frame.ts < self.acted_ts:
            diag("walk_tick", session=s.session_id, has_target=view.has_target, n_obstacles=len(view.obstacles),
                 key="stale", spoke=False, ms=ms)
            return self.active
        self.acted_ts = frame.ts
        self.fails = 0
        now = self._clock()
        if view.has_target:
            self.last_seen_at = now
            self.search.last_x = view.target_point[1]
        self.search.lost_s = now - self.last_seen_at
        ins = vwalk.decide(view, self.target, "en", self.search)
        if ins.key == "walk.not_found":
            self.last_seen_at = now      # say it once per 8 s of searching, not every tick
        # Freshness, judged now (just before speaking) from when the frame was taken: see vision.walk.gate.
        age = self._frame_age(frame)
        held = (self.hold_at is not None and now - self.hold_at <= HOLD_MAX_S and age is not None
                and math.isfinite(age) and age >= 0 and now - age >= self.hold_at + HOLD_SETTLE_S)
        if held:                         # taken while he stood still after "hold still": as good as fresh
            self.hold_at, age = None, 0.0
        self.stale_streak = self.stale_streak + 1 if vwalk.expired(age) else 0
        decided, ins = ins.key, vwalk.gate(ins, age, self.target)
        age_ms = round(age * 1000) if age is not None and math.isfinite(age) else None
        await self._publish(view, frame.id)
        if ins is None:                  # too old to say as current: never spoken
            diag("walk_tick", session=s.session_id, has_target=view.has_target, n_obstacles=len(view.obstacles),
                 key=decided, said="expired", spoke=False, ms=ms, age_ms=age_ms, stale=self.stale_streak)
            if self.stale_streak >= FRESH_VIEW_AFTER:
                self.stale_streak = 0
                self.speech.last_key, self.speech.last_at = "walk.fresh_view", now   # the next line counts as new
                await self._say("Please slow down, I need a fresh look.",
                                facts={"action": "slow_down", "reason": "view_too_old", "wait_for": "fresh_look"})
            return self.active
        text = self.speech.pick(ins, now)
        if ins.key == "walk.hold" and text is not None and (self.hold_at is None or now - self.hold_at > HOLD_MAX_S):
            self.hold_at = now
        facts = walk_facts(ins, view, self.target, self.search)
        if text is not None and text != ins.text:          # the 4 s reassurance of a direction line
            facts = {"action": "keep_going", "target": self.target}
        diag("walk_tick", session=s.session_id, has_target=view.has_target, n_obstacles=len(view.obstacles),
             key=decided, said=ins.key, spoke=text is not None, ms=ms, age_ms=age_ms, stale=self.stale_streak)
        if ins.arrived:
            await self._end("arrived", ins.text, cue="match", facts=facts)
            return False
        if text is not None:
            await self._say(text, cue="warn" if ins.urgent else None, facts=facts)
        return self.active

    async def _failed(self, why: str, ms: int = 0) -> bool:
        self.fails += 1
        diag("walk_tick", session=self.session.session_id, has_target=False, n_obstacles=0, key=f"fail.{why}",
             spoke=False, ms=ms, fails=self.fails)
        if self.fails >= MAX_FAILS:
            await self._end("blind", "I can't see well. Please stop and wait.", cue="warn",
                            facts={"action": "stop", "reason": "camera_unclear", "wait": True})
            return False
        return self.active

    async def _publish(self, view: vwalk.WalkView, frame_id: str) -> None:
        from aira_live.vision.listing import publish_boxes

        await publish_boxes(self.session, vwalk.overlay_objects(view, self.target), "walk", target=self.target,
                            found=view.has_target, frame_id=frame_id)

    # ---------- the loop ----------
    async def run(self) -> None:
        """Pipelined: a new look starts every ``interval_s`` (ER takes 2-6 s), at most ``max_in_flight`` at a
        time; each result is acted on only if no newer frame was acted on already."""
        deadline = self.started_at + self.max_s
        inflight: set[asyncio.Task] = set()
        try:
            while self.active:
                if self._clock() >= deadline:
                    await self._end("timeout", "I stopped guiding. Ask me again when you are ready.", cue="done",
                                    facts={"action": "guidance_ended", "reason": "timeout"})
                    return
                inflight = {x for x in inflight if not x.done()}
                if len(inflight) < self.max_in_flight:
                    try:
                        frame = await self.next_frame()
                        if not self.active:
                            return
                        if frame is None:
                            await self._failed("no_frame")
                        else:
                            inflight.add(asyncio.create_task(self._guarded(frame)))
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        log.exception("walk tick failed for %s", self.session.session_id)
                await self._sleep(self.interval_s)
        finally:
            for x in inflight:
                if x is not asyncio.current_task() and not x.done():
                    x.cancel()

    async def _guarded(self, frame) -> None:
        try:
            await self.process(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("walk look failed for %s", self.session.session_id)

    # ---------- fast stop from the phone's own detector ----------
    async def on_phone_boxes(self, objects) -> bool:
        """The phone's MediaPipe / bump-guard boxes (5 a second, no ER delay): a danger box, or a person or
        large object close in the corridor, says "Stop." at once (at most once per ``FAST_STOP_GAP_S``)."""
        if not self.active:
            return False
        now = self._clock()
        if now - self.fast_stop_at < FAST_STOP_GAP_S:
            return False
        hit = vwalk.fast_stop_label(objects)
        if hit is None:
            return False
        self.fast_stop_at = now
        self.speech.last_key, self.speech.last_at = "walk.fast_stop", now   # the next ER line counts as new
        diag("walk_fast_stop", session=self.session.session_id)
        await self._say(f"Stop. {hit} in front.", cue="warn", facts={"action": "stop", "hazard": hit})
        return True

    # ---------- speaking and ending ----------
    async def _say(self, text: str, cue: str | None = None, fresh: bool = True, facts: dict | None = None) -> None:
        """Caption + earcon + the live voice (the proactive notice path). ``text`` is the English meaning and
        ``facts`` the structured instruction; Live says it in the language the owner is speaking. ``fresh``: a
        safety line or a repeat is said again even when the bridge said the same words a few seconds ago
        (``Speech`` already limits repeats to one per 4 s)."""
        s = self.session
        notice = notice_text(text, facts)
        try:
            await s.send(TranscriptMsg(role="aira", text=text, final=True, lang="en"))
            if cue:
                await s.send(CueMsg(cue=cue))
            bridge = _bridge_of(s)
            if bridge is not None:
                if fresh:
                    getattr(bridge, "_notices", {}).pop(notice_key(notice), None)
                # A hazard or obstacle ("warn" cue, or an action of stop) cuts in at once for his safety.
                urgent = cue == "warn" or (facts or {}).get("action") == "stop"
                await bridge.speak_notice(notice, urgent=urgent)
        except Exception:
            log.warning("walk: session %s unreachable", s.session_id, exc_info=True)

    async def _end(self, why: str, text: str | None, cue: str = "done", facts: dict | None = None) -> bool:
        """End the walk once: unregister, say the last line, then stop the frames and the loop (the line goes
        before the loop is cancelled, so a look that ends the walk is never cut off mid-sentence)."""
        if not self.active:
            return False
        self.active = False
        self.ended = why
        sid = self.session.session_id
        if _ACTIVE.get(sid) is self:
            del _ACTIVE[sid]
        diag("walk_end", session=sid, why=why, s=round(self._clock() - self.started_at, 1))
        if text:
            await self._say(text, cue=cue, facts=facts)
        await self._cleanup()
        return True

    async def stop(self) -> None:
        """Stop ticking and the walk frames quietly. Safe to call twice and on a closed socket."""
        if not self.active:
            return
        self.active = False
        await self._cleanup()

    async def _cleanup(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        try:
            await self.session.send(RequestFrameMsg(purpose=PURPOSE, stop=True))
        except Exception as exc:  # noqa: BLE001 - the phone socket may already be closed
            log.debug("walk: stop not sent to session %s: %s", self.session.session_id, exc)
        if self.task is not None and self.task is not asyncio.current_task() and not self.task.done():
            self.task.cancel()


# --- the saved location --------------------------------------------------------------------------------------------


async def remembered_place(session: Session, target: str) -> tuple[str | None, str | None]:
    """``(place, side)`` from the last-seen memory, e.g. ("on your left, a few steps away", "left"); Nones when
    there is nothing (or the memory is slow or down: the walk never waits on it)."""
    try:
        from aira_live.memory import items

        async def _find():
            cfg = await items.settings(session.shop_id)
            name = items.canonical_item(target, cfg["items"]) or items.normalize_name(target)
            return await items.last_seen(session.shop_id, name)

        sighting = await asyncio.wait_for(_find(), MEMORY_TIMEOUT_S)
    except Exception:
        log.debug("walk: no saved place for %r", target, exc_info=True)
        return None, None
    if sighting is None:
        return None, None
    side_key = str((sighting.place_parts or {}).get("side") or "")
    side = "left" if "left" in side_key else "right" if "right" in side_key else None
    if side is None:
        low = sighting.place.casefold()
        side = "left" if "left" in low else "right" if "right" in low else None
    return items.speak_place(sighting, "en"), side   # English facts: Live says them in the owner's language


# --- start / stop ----------------------------------------------------------------------------------------------------


async def start(session: Session, target: str, run: bool = True, **kw) -> WalkLoop:
    """Replace any walk of this session: ask for continuous walk frames and start the loop."""
    await stop_walk(session)
    if "memory_place" not in kw:
        kw["memory_place"], kw["memory_side"] = await remembered_place(session, target)
    loop = WalkLoop(session, target, **kw)
    await session.send(RequestFrameMsg(purpose=PURPOSE, continuous=True))
    _ACTIVE[session.session_id] = loop
    if run:
        loop.task = asyncio.create_task(loop.run(), name=f"walk-{session.session_id}")
    diag("walk_start", session=session.session_id, memory=kw.get("memory_place") is not None)
    return loop


def active(session: Session) -> WalkLoop | None:
    return _ACTIVE.get(session.session_id)


async def stop_walk(session: Session) -> bool:
    """Stop the walk of ``session`` quietly. True when one was running. Safe to call twice."""
    loop = _ACTIVE.pop(session.session_id, None)
    if loop is None:
        return False
    loop.ended = loop.ended or "stopped"
    await loop.stop()
    return True


async def on_phone_boxes(session: Session, msg) -> None:
    """main.py hook for every phone ``boxes`` message: feeds the walk's fast stop. Never raises."""
    loop = _ACTIVE.get(session.session_id)
    if loop is None:
        return
    try:
        await loop.on_phone_boxes(getattr(msg, "objects", None) or [])
    except Exception:
        log.warning("walk: phone boxes failed for %s", session.session_id, exc_info=True)


async def stop_session(session: Session) -> None:
    """main.py hook: a `stop` message or a disconnect ends the walk."""
    await stop_walk(session)


# --- Live tools ------------------------------------------------------------------------------------------------------


async def walk_to(target: str) -> dict:
    """Walk the owner to a place or thing in the shop and keep guiding him until he arrives: call it ONCE when
    he wants to go somewhere or reach something ("take me to the fridge", "guide me to the curd shelf", "walk me
    to the door"). target: what to reach, in short English ("fridge", "curd shelf", "door"). The system then
    says each next step by itself ("Fridge ahead, about 3 steps", "Stop. Chair in front. Move a little left.")
    until he arrives or says stop. While he walks, do not talk and do not call camera_ask: the system guides.
    The result is facts with a short English say_en meaning hint. Say the result to the owner in the language
    he is speaking; say numbers exactly as given; if approx is true say 'about'.
    """
    s: Session = get_session()
    name = re.sub(r"\s+", " ", target or "").strip()[:40]
    if not name:
        return {"active": False, "reason": "no_target", "ask": "where to go", "say_en": "Where do you want to go?"}
    await start(s, name)
    return {"active": True, "target": name, "action": "walk_slowly",
            "say_en": f"Okay. Walk slowly. I will guide you to the {name}."}


async def walk_stop() -> dict:
    """Stop walking guidance at once. Call it the moment the owner wants to stop, wait, or has had enough while
    walking, in ANY language ("stop", "wait", "I'm here", "that's enough", "நில்லு", "போதும்", "रुको", "बस").
    Nothing else stops the walk by voice: only this call. Say the result to the owner in the language he is
    speaking."""
    s: Session = get_session()
    was = await stop_walk(s)
    return {"stopped": True, "was_active": was, "say_en": "Okay, stopped."}
