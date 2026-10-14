""""Walk with me": ``walk_to`` / ``walk_stop`` / ``nav_report`` Live tools and the background walk loop.

Gemini Live is the only model: it sees the phone's 1 fps live video, talks with the owner, and is also the walk's
eyes. No Robotics-ER call anywhere in walking.

The owner says once "take me to the fridge" (any language); Live calls ``walk_to`` (NON_BLOCKING) which starts
``WalkLoop`` on the session and returns at once. The loop then drives perception WITHOUT waiting for the owner:
every ``CHECK_INTERVAL_S`` (and at once when the phone marks a live frame ``changed``) it sends ONE short
``[NAV CHECK n]`` system note into the same Live session (``LiveBridge.send_text``: not a notice and not an owner
turn, so it never lifts a long-press stop). Live answers by calling ``nav_report(check_id=n, ...)`` (scheduled
SILENT: its function response never makes Live speak). The report goes through the policy in code
(``vision.walk.decide`` / ``gate`` / ``Speech``) and at most one line is said through the notice path
(``bridge.speak_notice``: urgent for stops, with a "warn" earcon).

State machine (``WalkLoop.state``; every transition is logged as ``walk_state``)::

    walk_to -> ACQUIRING --first usable report--> NAVIGATING <----2 clear fresh reports---- HAZARD
                  |                                 |  ^  \\--obstacle / hazard / phone stop--> HAZARD
                  |                                 |  |   \\--target large + ahead (confirmed)--> ARRIVAL_CANDIDATE
                  |                                 v  |                                          (keeps watching)
                  +--unusable view / 3 failed checks--> DEGRADED --2 usable fresh reports--> NAVIGATING
    any state --walk_stop / long-press / disconnect / timeout--> STOPPED (late reports ignored)

Pausing is walk_stop then walk_to again. Arrival is never claimed: ARRIVAL_CANDIDATE says "The fridge appears to
be just ahead. Reach forward carefully to confirm." and keeps watching; the walk ends on walk_stop (the owner says he is
there, or stop, in any language: Live decides), the long-press, a disconnect, or after ``MAX_S``.

Freshness: an observation's age is (now - when the newest live frame was forwarded to Live before its check was
sent), measured just before its line is said, so it includes the check's latency. Stops are always said; other
lines only when age <= ``vision.walk.WALK_DIRECTION_MAX_AGE_S``. A report whose check was replaced, timed out or
belongs to an ended walk is ignored.

The phone signal: walk start sends ``RequestFrameMsg(purpose="walk", continuous=True)`` and the end sends its
``stop=True`` twin, unchanged, only as the "walk active" signal for the phone. The loop never waits for or reads
``walk`` frames.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Literal

from aira_live.contracts import CueMsg, RequestFrameMsg, TranscriptMsg
from aira_live.diag import diag
from aira_live.session import Session, get_session, notice_key
from aira_live.vision import walk as vwalk

log = logging.getLogger(__name__)

PURPOSE = "walk"
TICK_S = 0.5               # the loop's own heartbeat (timeouts, check pacing)
CHECK_INTERVAL_S = 2.0     # a nav check at most this often (one in flight at a time)
CHANGED_CHECK_GAP_S = 0.75  # a "changed" live frame triggers a check this soon after the previous one
CHECK_TIMEOUT_S = 4.0      # a check without its nav_report after this long counts as a failed look
MAX_FAILS = 3              # failed looks in a row -> DEGRADED
CLEAR_TO_RESUME = 2        # fresh usable reports without the hazard before directions resume
MAX_S = 300.0
STILL_GUIDING_SILENT_S = 20.0
STILL_GUIDING_EVERY_S = 60.0
MEMORY_TIMEOUT_S = 1.5
FAST_STOP_GAP_S = 3.0
MAX_IN_FLIGHT = 1          # nav checks in flight at once

ACQUIRING, NAVIGATING, HAZARD = "ACQUIRING", "NAVIGATING", "HAZARD"
ARRIVAL_CANDIDATE, DEGRADED, STOPPED = "ARRIVAL_CANDIDATE", "DEGRADED", "STOPPED"

CHECK_PREFIX = "[NAV CHECK"

_ACTIVE: dict[str, "WalkLoop"] = {}


def check_text(check_id: int, target: str) -> str:
    """The system note that asks Live for one observation. Never from the owner, never to be spoken about."""
    return (f"{CHECK_PREFIX} {check_id}] System check for the walk to the {target}; not from the owner. Look at the "
            f"newest camera frame and call nav_report once with check_id={check_id}: whether the {target} is "
            "visible (its side and size), the obstacles on the floor (label, side, whether in his walking path "
            "straight ahead, size), path clear_in_view / blocked / uncertain, and view_ok false if the view is "
            "dark, blurred or covered. Do not speak.")


def notice_text(meaning: str, facts: dict | None) -> str:
    """What goes to Live's notice path: the facts, then the English meaning as a hint."""
    if not facts:
        return meaning
    shown = {k: v for k, v in facts.items() if v not in (None, "")}
    return f"{json.dumps(shown, ensure_ascii=False)} (meaning: {meaning})"


def _bridge_of(s: Session):
    from aira_live import sessions

    return next((b for sess, b in sessions.by_shop(s.shop_id) if sess.session_id == s.session_id), None)


class NavChecker:
    """How a nav check reaches Live. The default asks the bridge (``LiveBridge.request_nav_check``), which sends
    only while Live is idle (not answering, the owner not speaking, not stopped) and never lifts a long-press stop
    or counts as an owner turn. ``request`` returns False when the check could not go now (try again later)."""

    def __init__(self, bridge: Callable[[], object]):
        self._bridge = bridge

    async def request(self, check_id: int, target: str) -> bool:
        bridge = self._bridge()
        send = getattr(bridge, "request_nav_check", None)
        if send is None:
            return False
        return bool(await send(check_text(check_id, target)))


class WalkLoop:
    def __init__(self, session: Session, target: str, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable] = asyncio.sleep, bridge: Callable[[], object] | None = None,
                 interval_s: float = CHECK_INTERVAL_S, check_timeout_s: float = CHECK_TIMEOUT_S,
                 max_s: float = MAX_S, memory_place: str | None = None, memory_side: str | None = None,
                 checker: NavChecker | None = None):
        self.session, self.target = session, target
        self._clock, self._sleep = clock, sleep
        self._bridge = bridge or (lambda: _bridge_of(session))
        self.checker = checker or NavChecker(self._bridge)
        self.owner_name: str | None = getattr(session, "owner_name", None) or None
        self.hazard_label: str | None = None   # what the last stop was for (the resume line names it)
        self.interval_s, self.check_timeout_s, self.max_s = interval_s, check_timeout_s, max_s
        self.started_at = clock()
        self.state = ACQUIRING
        self.active = True
        self.ended: str | None = None          # why: stopped | timeout
        self.task: asyncio.Task | None = None
        # perception
        self.frame_at: float | None = None     # when the newest live frame was forwarded to Live (loop clock)
        self.check_seq = 0
        self.pending: tuple[int, float, float] | None = None   # (check id, sent at, frame forwarded at)
        self.last_check_at = float("-inf")
        self.last_check_frame_at = float("-inf")
        self.checks_sent = 0
        self.fails = 0
        # policy
        self.recent: deque[vwalk.NavReport] = deque(maxlen=3)
        self.recent_keys: deque[str] = deque(maxlen=3)
        self.search = vwalk.Search(memory_place=memory_place, memory_side=memory_side)
        self.last_seen_at = self.started_at
        self.speech = vwalk.Speech()
        self.resume_streak = 0                 # fresh usable reports in a row while HAZARD / DEGRADED
        self.degraded_said = False
        self.last_spoken_at = self.started_at
        self.last_still_at = self.started_at
        self.fast_stop_at = float("-inf")
        self.fast_stop_label = ""

    # ---------- state ----------
    def _to(self, state: str, why: str) -> None:
        if state == self.state:
            return
        diag("walk_state", session=self.session.session_id, frm=self.state, to=state, why=why)
        if state != DEGRADED:
            self.degraded_said = False
        self.resume_streak = 0
        self.state = state

    def retarget(self, target: str) -> None:
        """walk_to with a new destination while walking: same loop, new target. Any check in flight was about
        the old target, so its report is ignored."""
        self.target = target
        self.pending = None
        self.recent.clear()
        self.recent_keys.clear()
        self.search = vwalk.Search()
        self.last_seen_at = self._clock()
        self.speech.reset()
        self._to(ACQUIRING, "retarget")
        diag("walk_retarget", session=self.session.session_id)

    # ---------- perception: nav checks ----------
    def on_live_frame(self, now: float | None = None) -> None:
        """A live frame was just forwarded to Live (main.live_frame)."""
        self.frame_at = self._clock() if now is None else now

    async def maybe_check(self, reason: str = "tick") -> bool:
        """Send one nav check into Live when none is in flight and the voice channel is free. True when sent."""
        if not self.active or self.pending is not None:
            return False
        now = self._clock()
        if self._bridge() is None:
            self.last_check_at = now
            await self._failed("no_live")
            return False
        if self.frame_at is None or self.frame_at <= self.last_check_frame_at:
            self.last_check_at = now      # no new picture: try again next interval
            await self._failed("no_frame")
            return False
        check_id, frame_at = self.check_seq + 1, self.frame_at
        try:
            sent = await self.checker.request(check_id, self.target)
        except Exception:
            log.warning("walk: nav check not sent for %s", self.session.session_id, exc_info=True)
            self.last_check_at = now
            await self._failed("send")
            return False
        if not self.active:
            return False
        if not sent:   # Live is answering, the owner is speaking or a stop holds: try again on the next tick
            diag("walk_check", session=self.session.session_id, sent=False, reason=reason, skipped="busy")
            return False
        self.check_seq = check_id
        self.pending = (check_id, now, frame_at)
        self.last_check_at, self.last_check_frame_at = now, frame_at
        self.checks_sent += 1
        diag("walk_check", session=self.session.session_id, sent=True, reason=reason, check=check_id,
             frame_age_ms=round((now - frame_at) * 1000))
        return True

    async def on_changed_frame(self) -> None:
        """The phone saw the scene change: check now (if a check may go at all)."""
        if self.active and self._clock() - self.last_check_at >= CHANGED_CHECK_GAP_S:
            await self.maybe_check("changed")

    async def on_report(self, report: vwalk.NavReport) -> str:
        """Live's ``nav_report`` for a check: acted on only when it answers the check in flight."""
        if not self.active:
            return "inactive"
        if self.pending is None or report.check_id != self.pending[0]:
            diag("walk_tick", session=self.session.session_id, state=self.state, said="stale_report",
                 check=report.check_id)
            return "stale"
        _, sent_at, frame_at = self.pending
        self.pending = None
        self.fails = 0
        now = self._clock()
        await self.process(report, now - frame_at, ms=round((now - sent_at) * 1000))
        return "ok"

    # ---------- the policy ----------
    async def process(self, report: vwalk.NavReport, age: float | None, ms: int = 0) -> None:
        s, now = self.session, self._clock()
        self.recent.append(report)
        if report.target_visible:
            self.last_seen_at = now
            if report.target_side in ("left", "right"):
                self.search.last_side = report.target_side
        self.search.lost_s = now - self.last_seen_at
        ins = vwalk.decide(report, self.target, list(self.recent), self.search, name=self.owner_name)
        if ins.key == "walk.not_found":
            self.last_seen_at = now           # say it once per NOT_FOUND_S of searching
        self.recent_keys.append(ins.key)
        fresh = not vwalk.expired(age)
        said, text = self._choose(ins, report, fresh, now)
        age_ms = round(age * 1000) if age is not None and fresh else None
        diag("walk_tick", session=s.session_id, state=self.state, has_target=report.target_visible,
             n_obstacles=len(report.obstacles), corridor=report.path, view_ok=report.view_ok, age_ms=age_ms,
             key=ins.key, said=said.key if said and text else None, ms=ms)
        if said is not None and text is not None:
            await self._say(text, cue="warn" if said.urgent else None, facts=said.facts, urgent=said.urgent)

    def _choose(self, ins: vwalk.Instruction, report: vwalk.NavReport, fresh: bool,
                now: float) -> tuple[vwalk.Instruction | None, str | None]:
        """The state transition for this instruction, and the instruction + line to say now (or Nones)."""
        if ins.kind in (vwalk.HAZARD_KIND, vwalk.OBSTACLE_KIND):
            self._to(HAZARD, ins.kind)
            self.resume_streak = 0
            self.hazard_label = ins.facts.get("obstacle") or ins.facts.get("hazard") or None
            return ins, self.speech.pick(ins, now)          # stops: never held back
        if ins.kind == vwalk.DEGRADED_KIND:
            self._to(DEGRADED, "view")
            self.resume_streak = 0
            return ins, self._degraded_line(now)
        if ins.kind == vwalk.HOLD_KIND:                     # maybe something in the path: wait, no direction
            self.resume_streak = 0
            return None, None
        # A usable view with nothing in the path.
        if self.state in (HAZARD, DEGRADED):
            self.resume_streak = self.resume_streak + 1 if fresh else 0
            if self.resume_streak < CLEAR_TO_RESUME:
                return None, None
            was = self.state
            self._to(NAVIGATING, "clear")
            self.speech.last_key = None       # the next direction counts as new
            if was == HAZARD:
                line = vwalk.resume_line(self.target, self.hazard_label)
            else:
                line = vwalk.Instruction("walk.resume", f"I can see the path again. Walk on slowly toward the "
                                         f"{self.target}.", facts={"action": "resume", "target": self.target,
                                                                   "view": "clear_again", "slowly": True})
            self.hazard_label = None
            self.speech.mark(line.key, now)
            return line, line.text
        if vwalk.gate(ins, 0.0 if fresh else None) is None:
            return None, None                 # too old to say anything but a stop
        if self.recent_keys.count(ins.key) < 2:
            return None, None                 # not confirmed yet: 2 of the last 3 reports
        if ins.kind == vwalk.ARRIVAL_KIND:
            self._to(ARRIVAL_CANDIDATE, "target_close")
        elif self.state in (ACQUIRING, ARRIVAL_CANDIDATE):
            self._to(NAVIGATING, ins.kind)
        reassure = self.state == NAVIGATING and fresh and report.usable \
            and now - self.speech.last_at >= vwalk.REASSURE_S
        return ins, self.speech.pick(ins, now, reassure=reassure)

    def _degraded_line(self, now: float) -> str | None:
        if self.degraded_said:
            return None
        self.degraded_said = True
        self.speech.mark("walk.degraded", now)
        return vwalk.DEGRADED_TEXT

    async def _failed(self, why: str) -> None:
        self.fails += 1
        diag("walk_tick", session=self.session.session_id, state=self.state, key=f"fail.{why}", said=None,
             fails=self.fails)
        if self.fails >= MAX_FAILS:
            self._to(DEGRADED, f"fail.{why}")
            text = self._degraded_line(self._clock())
            if text:
                await self._say(text, cue="warn", urgent=True,
                                facts={"action": "stop", "reason": "camera_unclear", "wait_for": "clear_view"})

    # ---------- the loop ----------
    async def tick(self) -> None:
        now = self._clock()
        if self.pending is not None and now - self.pending[1] > self.check_timeout_s:
            diag("walk_check", session=self.session.session_id, sent=False, check=self.pending[0],
                 skipped="timeout")
            self.pending = None
            await self._failed("timeout")
        if self.pending is None and now - self.last_check_at >= self.interval_s:
            await self.maybe_check("tick")
        if (self.active and self.state in (NAVIGATING, ACQUIRING)
                and now - self.last_spoken_at > STILL_GUIDING_SILENT_S
                and now - self.last_still_at >= STILL_GUIDING_EVERY_S):
            self.last_still_at = now
            await self._say(f"Still guiding you to the {self.target}.",
                            facts={"action": "still_guiding", "target": self.target})

    async def run(self) -> None:
        deadline = self.started_at + self.max_s
        while self.active:
            if self._clock() >= deadline:
                await self._end("timeout", "I stopped guiding. Ask me again when you are ready.", cue="done",
                                facts={"action": "guidance_ended", "reason": "timeout"})
                return
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("walk tick failed for %s", self.session.session_id)
            await self._sleep(TICK_S)

    # ---------- fast stop from the phone's own detector ----------
    async def on_phone_boxes(self, objects) -> bool:
        """The phone's MediaPipe / bump-guard boxes (5 a second): a danger box, or a person or large object
        close in the corridor, says "Stop." at once. Never for the target. The detector sends 5 boxes a second:
        the SAME label is said again only after ``FAST_STOP_GAP_S`` (a rate limit, not a cooldown on new
        dangers: a different label is said at once)."""
        if not self.active:
            return False
        now = self._clock()
        hit = vwalk.fast_stop_label(objects, self.target)
        if hit is None:
            return False
        if hit.casefold() == self.fast_stop_label and now - self.fast_stop_at < FAST_STOP_GAP_S:
            return False
        self.fast_stop_at, self.fast_stop_label = now, hit.casefold()
        self._to(HAZARD, "phone")
        self.resume_streak = 0
        self.hazard_label = hit
        self.speech.mark("walk.fast_stop", now)
        diag("walk_fast_stop", session=self.session.session_id)
        await self._say(f"{vwalk.stop_word(self.owner_name)} {vwalk._cap(hit)} in front.", cue="warn", urgent=True,
                        facts={"action": "stop", "hazard": hit, "address": self.owner_name})
        return True

    # ---------- speaking and ending ----------
    async def _say(self, text: str, cue: str | None = None, facts: dict | None = None, urgent: bool = False,
                   final: bool = False) -> None:
        """Caption + earcon + the live voice (the notice path); urgent lines cut in at once. Nothing once the walk
        ended (``final``: the walk's own last line)."""
        if not self.active and not final:
            return
        s = self.session
        notice = notice_text(text, facts)
        self.last_spoken_at = self._clock()
        try:
            await s.send(TranscriptMsg(role="aira", text=text, final=True, lang="en"))
            if cue:
                await s.send(CueMsg(cue=cue))
            bridge = self._bridge()
            if bridge is not None:
                getattr(bridge, "_notices", {}).pop(notice_key(notice), None)   # Speech already dedupes
                await bridge.speak_notice(notice, urgent=urgent or cue == "warn")
        except Exception:
            log.warning("walk: session %s unreachable", s.session_id, exc_info=True)

    async def _end(self, why: str, text: str | None, cue: str = "done", facts: dict | None = None) -> bool:
        if not self.active:
            return False
        self.active = False
        self.ended = why
        self.pending = None
        self._to(STOPPED, why)
        sid = self.session.session_id
        if _ACTIVE.get(sid) is self:
            del _ACTIVE[sid]
        diag("walk_end", session=sid, why=why, s=round(self._clock() - self.started_at, 1),
             checks=self.checks_sent)
        if text:
            await self._say(text, cue=cue, facts=facts, final=True)
        await self._cleanup()
        return True

    async def stop(self) -> None:
        """Stop quietly: no more checks or lines; late reports are ignored. Safe twice and on a closed socket."""
        if not self.active:
            return
        self.active = False
        self.pending = None
        self.ended = self.ended or "stopped"
        self._to(STOPPED, self.ended)
        await self._cleanup()

    async def _cleanup(self) -> None:
        try:   # the phone's "walk ended" signal (the same message as before; no walk frames are used)
            await self.session.send(RequestFrameMsg(purpose=PURPOSE, stop=True))
        except Exception as exc:  # noqa: BLE001 - the phone socket may already be closed
            log.debug("walk: stop not sent to session %s: %s", self.session.session_id, exc)
        if self.task is not None and self.task is not asyncio.current_task() and not self.task.done():
            self.task.cancel()


# --- the saved location (Firestore only, no model) -----------------------------------------------------------------


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
    return items.speak_place(sighting, "en"), side


# --- start / stop ----------------------------------------------------------------------------------------------------


async def start(session: Session, target: str, run: bool = True, **kw) -> WalkLoop:
    """Start a walk for ``session`` (one loop per session: an earlier walk is stopped first)."""
    await stop_walk(session)
    if "memory_place" not in kw:
        kw["memory_place"], kw["memory_side"] = await remembered_place(session, target)
    loop = WalkLoop(session, target, **kw)
    await session.send(RequestFrameMsg(purpose=PURPOSE, continuous=True))   # the phone's "walk active" signal
    _ACTIVE[session.session_id] = loop
    if run:
        loop.task = asyncio.create_task(loop.run(), name=f"walk-{session.session_id}")
    diag("walk_start", session=session.session_id, memory=kw.get("memory_place") is not None)
    diag("walk_state", session=session.session_id, frm=None, to=ACQUIRING, why="start")
    return loop


def active(session: Session) -> WalkLoop | None:
    loop = _ACTIVE.get(session.session_id)
    return loop if isinstance(loop, WalkLoop) else None


async def stop_walk(session: Session) -> bool:
    """Stop the walk of ``session`` quietly. True when one was running. Safe to call twice."""
    loop = _ACTIVE.pop(session.session_id, None)
    if not isinstance(loop, WalkLoop):
        return False
    loop.ended = loop.ended or "stopped"
    await loop.stop()
    return True


async def on_phone_boxes(session: Session, msg) -> None:
    """main.py hook for every phone ``boxes`` message: feeds the walk's fast stop. Never raises."""
    loop = active(session)
    if loop is None:
        return
    try:
        await loop.on_phone_boxes(getattr(msg, "objects", None) or [])
    except Exception:
        log.warning("walk: phone boxes failed for %s", session.session_id, exc_info=True)


async def on_live_frame(session: Session, changed: bool = False) -> None:
    """main.py hook: a live frame was forwarded into Live during a walk (and ``changed``: check now)."""
    loop = active(session)
    if loop is None:
        return
    try:
        loop.on_live_frame()
        if changed:
            await loop.on_changed_frame()
    except Exception:
        log.warning("walk: live frame hook failed for %s", session.session_id, exc_info=True)


async def stop_session(session: Session) -> None:
    """main.py hook: a `stop` message or a disconnect ends the walk."""
    await stop_walk(session)


# --- Live tools ------------------------------------------------------------------------------------------------------


async def walk_to(target: str) -> dict:
    """Walk the owner to a place or thing in the shop and keep guiding him: call it ONCE when he wants to go
    somewhere or reach something, in any language ("take me to the fridge", "guide me to the curd shelf", "help
    me reach the door"). target: what to reach, in short English ("fridge", "curd shelf", "door"). Calling it
    again with a new target changes the destination. The system then guides him by itself with short
    [SYSTEM NOTE] lines until he says stop. While he walks, do not chatter; answer his questions normally (the
    walk goes on). The result is facts with a short English say_en meaning hint: say it to the owner in the
    language he is speaking.
    """
    s: Session = get_session()
    name = re.sub(r"\s+", " ", target or "").strip()[:40]
    if not name:
        return {"active": False, "reason": "no_target", "ask": "where to go", "say_en": "Where do you want to go?"}
    loop = active(s)
    if loop is not None and loop.active:
        if vwalk.same_thing(name, loop.target) and name.casefold() == loop.target.casefold():
            return {"active": True, "target": name, "action": "walk_slowly", "already": True,
                    "say_en": f"I am guiding you to the {name}. Walk slowly."}
        loop.retarget(name)
        return {"active": True, "target": name, "action": "walk_slowly", "changed": True,
                "say_en": f"Okay. Now I will guide you to the {name}. Walk slowly."}
    await start(s, name)
    return {"active": True, "target": name, "action": "walk_slowly",
            "say_en": f"I'll guide you to the {name} and tell you about anything in the way. Walk slowly."}


async def walk_stop() -> dict:
    """Stop walking guidance at once. Call it the moment the owner wants to stop, wait, cancel, has had enough
    or says he is there while walking, in ANY language ("stop", "wait", "I'm here", "that's enough", "நில்லு",
    "போதும்", "रुको", "बस"). Nothing else stops the walk by voice: only this call. Say the result to the owner in
    the language he is speaking."""
    s: Session = get_session()
    was = await stop_walk(s)
    return {"stopped": True, "was_active": was, "say_en": "Okay, stopped."}


async def nav_report(target_visible: bool = False,
                     target_side: Literal["left", "ahead", "right", "none"] = "none",
                     target_size: Literal["small", "medium", "large", "none"] = "none",
                     obstacles: list[dict] | None = None,
                     path: Literal["clear_in_view", "blocked", "uncertain"] = "uncertain",
                     view_ok: bool = True,
                     check_id: int = 0) -> dict:
    """System only: call it ONLY when a "[NAV CHECK n]" message asks, once, with check_id=n, and never speak
    about it. Report what the newest camera frame shows for the walk: target_visible and the target's side
    (left / ahead / right, none when not visible) and size (small / medium / large: large means close; none
    when not visible); obstacles on the floor as a
    list of {label, side: left|ahead|right, in_path: true if in his walking path straight ahead, size:
    small|medium|large}; path: clear_in_view, blocked or uncertain; view_ok false when the view is dark,
    blurred or covered. Never guess: when unsure use uncertain."""
    s: Session = get_session()
    loop = active(s)
    if loop is not None:
        report = vwalk.parse_report(loop.target, check_id=check_id, target_visible=target_visible,
                                    target_side=target_side, target_size=target_size, obstacles=obstacles,
                                    path=path, view_ok=view_ok)
        await loop.on_report(report)
    return {"status": "ok", "retryable": False, "message": ""}
