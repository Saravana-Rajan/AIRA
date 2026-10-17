"""Low light: the flashlight offer and "where is the light switch". Flags AIRA_TORCH and AIRA_LOWLIGHT (off).

The phone decides the light level over seconds with hysteresis (frontend/app/src/camera/lowlight.ts) and sends a
``light`` message (level, raw luma, real torch state). This module only records it, and then:

- **Torch (AIRA_TORCH).** Sustained darkness with the torch off: AIRA turns the flashlight on by itself and
  says so, informing the owner rather than asking. The spoken line goes once per dark episode; the phone answers
  with its real state: ``on``, or ``unsupported`` (then AIRA says once that it cannot switch the flashlight on
  this phone and stops). Bright again with the torch on, at the bright level (hysteresis-smoothed, not the noisy
  raw luma) for ``TORCH_OFF_BRIGHT_S``: AIRA turns it off by itself and says so. The phone also switches it off after
  ``TORCH_AUTO_OFF_MS`` (frontend/app/src/camera/torch.ts); while it is still dark the next report re-lights it
  quietly. The owner can override by voice at any time (the ``flashlight`` tool); a spoken "turn it off" holds
  the auto-on until the view is no longer dark. The light-switch flow below still asks before using the torch.
- **Light switch (AIRA_LOWLIGHT).** ``light_switch_find``: with a shop map and a route follower (Part A,
  ``aira_live.tools.routes.route_to``, imported only if present) the walk goes there first. Near the board, the
  switch loop asks the ``point`` model for structured detections (``vision.switches``) and CODE chooses the
  target: a switch, never a socket, never a switch next to a socket; unsure which is which -> "not sure" and
  guidance stops. The phone's existing hand guidance (``target`` messages, tones, ``fingertip_in_target``) moves
  his fingertip there. After the press the raw luma before and after are compared in code: a rise of
  ``BRIGHT_RISE_LUMA`` says "the light is on now" (and offers to turn the torch off); otherwise "still dark, try
  the next switch".

Every line is English facts to Live (``say_en``) and, without a Live bridge, the fixed phone line from
``system_lines`` in the owner's language. Nothing here writes stock, money or state.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from dataclasses import dataclass, field

from aira_live import config, confirm, monitor, sessions
from aira_live.contracts import CueMsg, LightMsg, RequestFrameMsg, TargetMsg, TorchMsg, TranscriptMsg
from aira_live.diag import diag
from aira_live.session import Session, get_session
from aira_live.system_lines import line
from aira_live.vision import switches as vswitch
from aira_live.vision.gemini import VisionError

log = logging.getLogger(__name__)

# English facts for Live (the phone's fixed lines are the system_lines keys of the same name).
TORCH_OFFER_EN = "It's too dark. Shall I turn on the flashlight?"
TORCH_OFF_OFFER_EN = "It's bright now. Shall I turn off the flashlight?"
TORCH_ON_EN = "Turning on the flashlight."
TORCH_OFF_EN = "Turning off the flashlight."
# The automatic path's spoken facts (descriptive: they tell the owner why). The owner's own flashlight request
# uses the brief TORCH_ON_EN/TORCH_OFF_EN above instead.
TORCH_ON_DARK_EN = "It's getting dark, turning on the flashlight."
TORCH_OFF_BRIGHT_EN = "It's bright again, turning the flashlight off."
TORCH_UNSUPPORTED_EN = "I can't turn on the flashlight on this phone."
SWITCH_START_EN = "Point the phone at the switch board and hold your hand out. I'll guide your finger."
SWITCH_PRESS_EN = "Your finger is on {name}. Press it."
LIGHT_ON_EN = "The light is on now."
STILL_DARK_EN = "Still dark. Try the next switch."
NOT_SURE_EN = "Not sure which is a switch and which is a socket. I've stopped guiding. Ask someone sighted."
NO_BOARD_EN = "I can't see the switch board. Turn slowly."
NO_MORE_EN = "No more switches here."
SWITCH_STOPPED_EN = "I stopped guiding to the switch."

# How long the view must stay at the "bright" LEVEL, with the torch on, before AIRA turns it off. The decision
# rides the phone's level (which is hysteresis-smoothed over seconds), not the raw luma: on a real device the
# raw luma swings wildly even in steady light (measured 75-220 within seconds), so a raw-luma bar never settles,
# while the torch alone only reaches "dim", so the level stays dark/dim until real ambient light arrives.
# The phone already needs ~3 s of steady brightness before it reports "bright", so this short hold on top keeps
# the light-returns-to-torch-off latency around 6 s total while still filtering a brief flash or reflection.
TORCH_OFF_BRIGHT_S = 2.0
# A luma rise after a press that counts as "the light came on". Not measured on a device yet.
BRIGHT_RISE_LUMA = 30.0
# How long after "press it" the loop waits for the brightness to rise before "still dark".
PRESS_WINDOW_S = 8.0
# The switch loop: one guide frame a second at most, stops by itself after MAX_SWITCH_S.
SWITCH_INTERVAL_S = 1.0
SWITCH_FRAME_TIMEOUT_S = 3.0
MAX_SWITCH_S = 120.0
LOST_AFTER = 3  # board not seen this many ticks in a row -> "I can't see the switch board", once per loss
ROUTE_MODULE = "aira_live.tools.routes"  # Part A: ``async def route_to(session, place) -> dict | None``
ROUTE_PLACE = "switch_board"

TORCH_ON_COMMITTER = "light.torch_on"
TORCH_OFF_COMMITTER = "light.torch_off"
_monotonic = time.monotonic  # tests swap in a clock


@dataclass
class LightState:
    level: str = "bright"
    luma: float | None = None
    torch: str = "off"
    dark_offered: bool = False       # the auto-on was announced in this dark episode (announce once; re-armed when not dark)
    off_offered: bool = False        # the turn-off was announced/offered in this torch-on episode
    off_override: bool = False       # the owner turned it off by voice: hold auto-on until the view is no longer dark
    unsupported_said: bool = False
    bright_since: float | None = None
    torch_requested: str | None = None   # "on" / "off": the command sent, until the phone reports
    switch_after_torch: bool = False     # start the switch loop once the torch report lands
    pending: dict = field(default_factory=dict)  # confirm id -> what it proposes ("on" / "off")


_STATE: dict[str, LightState] = {}
_LOOPS: dict[str, "SwitchLoop"] = {}


def state(session: Session) -> LightState:
    st = _STATE.get(session.session_id)
    if st is None:
        st = _STATE[session.session_id] = LightState()
    return st


def _bridge_of(s: Session):
    return next((b for sess, b in sessions.by_shop(s.shop_id) if sess.session_id == s.session_id), None)


async def _tell(s: Session, key: str, text: str, cue: str = "ack", urgent: bool = False, **kw) -> None:
    """Facts to Live (the bridge says them in the owner's language); without a bridge, the phone's fixed line."""
    try:
        await s.send(CueMsg(cue=cue))
        bridge = _bridge_of(s)
        if bridge is None:
            await s.send(TranscriptMsg(role="aira", text=line(key, s.lang, **kw), final=True, lang=s.lang))
        else:
            await bridge.speak_notice(text, urgent=urgent)
    except Exception:
        log.warning("light: session %s unreachable", s.session_id, exc_info=True)


# ---------- torch: propose, commit only after yes ----------
async def _propose(s: Session, st: LightState, on: bool) -> dict:
    prompt = TORCH_OFFER_EN if on else TORCH_OFF_OFFER_EN
    committer = TORCH_ON_COMMITTER if on else TORCH_OFF_COMMITTER
    out = await confirm.propose("generic", prompt, {"on": on, "session_id": s.session_id}, committer)
    cid = out.get("confirm_id")
    if cid:
        st.pending[cid] = "on" if on else "off"
    diag("light", action="propose", session=s.session_id, on=on, confirm_id=cid, level=st.level, luma=st.luma)
    # Live is told the question and how to answer it: the proposal came from the system, not from a tool call.
    await _tell(s, "light.torch_offer" if on else "light.torch_off_offer",
                f"{prompt} If the owner says yes, call confirm_action with confirm_id {cid} and answer yes; "
                "if he says no, answer no. Ask him now, in a few words.", cue="warn")
    return out


async def _commit_torch(payload: dict, value: str | None, on: bool) -> dict:
    s = get_session()
    st = state(s)
    for cid in [c for c, what in st.pending.items() if what == ("on" if on else "off")]:
        st.pending.pop(cid, None)
    st.torch_requested = "on" if on else "off"
    await s.send(TorchMsg(on=on))
    diag("light", action="torch_command", session=s.session_id, on=on)
    return {"torch": "requested", "on": on, "say_en": TORCH_ON_EN if on else TORCH_OFF_EN}


async def _commit_torch_on(payload: dict, value: str | None) -> dict:
    return await _commit_torch(payload, value, True)


async def _commit_torch_off(payload: dict, value: str | None) -> dict:
    return await _commit_torch(payload, value, False)


confirm.register_committer(TORCH_ON_COMMITTER, _commit_torch_on)
confirm.register_committer(TORCH_OFF_COMMITTER, _commit_torch_off)


async def flashlight(on: bool) -> dict:
    """Turn the phone's flashlight on or off when the owner asks, in any language ("turn off the flashlight",
    "torch on", "லைட்டை அணை", "टॉर्च बंद करो"). His own request is the consent: no confirm_action. Off always
    works at once; on works only on phones whose camera has a torch (some, like iPhone browsers, have none:
    then say so). Say the result in his language."""
    s: Session = get_session()
    st = state(s)
    if on and st.torch == "unsupported":
        return {"torch": "unsupported", "on": False, "say_en": TORCH_UNSUPPORTED_EN}
    for cid in list(st.pending):   # a pending offer is answered by his own request
        st.pending.pop(cid, None)
    st.torch_requested = "on" if on else "off"
    if not on:
        st.off_offered = True      # do not announce/offer the turn-off after he already did it
        st.off_override = True     # and do not auto-on again until the view is no longer dark
    await s.send(TorchMsg(on=on))
    diag("light", action="torch_command", session=s.session_id, on=on, source="owner")
    return {"torch": "requested", "on": on, "say_en": TORCH_ON_EN if on else TORCH_OFF_EN}


async def on_light(session: Session, msg: LightMsg) -> None:
    """Cross-team hook: main.py calls this for every ``light`` message. Records the report; with the flags on it
    turns the torch on in the dark and off again when it is bright (informing the owner, not asking), and feeds
    the switch loop's before/after brightness check."""
    st = state(session)
    prev_torch, prev_level = st.torch, st.level
    st.level, st.luma, st.torch = msg.level, float(msg.luma), msg.torch
    diag("light", action="report", session=session.session_id, level=msg.level, luma=msg.luma, torch=msg.torch)
    monitor.event(session, "light", level=msg.level, luma=msg.luma, torch=msg.torch)
    # A sent command is fulfilled as soon as the phone reports the state we asked for, even when that is not a
    # change (the owner said "turn it on" while it was already on): otherwise torch_requested would stay set and
    # block the automatic turn-off, which only fires when no command is in flight.
    if st.torch_requested is not None and msg.torch == st.torch_requested:
        st.torch_requested = None
    if msg.torch != prev_torch:
        st.torch_requested = None
        if msg.torch == "unsupported" and not st.unsupported_said:
            st.unsupported_said = True
            await _tell(session, "light.torch_unsupported", TORCH_UNSUPPORTED_EN, cue="warn")
        if msg.torch == "off":
            st.bright_since = None
        if msg.torch == "on":
            # A fresh torch-on episode: allow a new automatic turn-off. off_offered is reset here (not only when
            # the torch turns off) so a manual "turn it off" from the previous episode, which sets off_offered
            # while the torch is already off, cannot carry over and block this episode's auto-off.
            st.off_offered, st.bright_since = False, None
        if st.switch_after_torch and msg.torch in ("on", "unsupported"):
            st.switch_after_torch = False
            await start_switch(session)
    if msg.level != "dark":
        st.dark_offered = False  # a new dark episode announces again
        st.off_override = False  # a new dark episode may auto-on again after a manual "turn it off"
    loop = _LOOPS.get(session.session_id)
    if loop is not None and loop.active:
        await loop.on_light(st.luma)
    settings = config.load()
    if not settings.torch:
        return
    # Dark with the torch off: turn it on and say so. The command goes on each dark+off report (the phone's own
    # hysteresis debounces the dark signal, and torch_requested debounces the command until the phone reports
    # back), so if the phone's 5-minute auto-off fires while it is still dark the next report re-lights it. The
    # spoken line goes only the first time in each dark episode (dark_offered, re-armed above when the view is no
    # longer dark), so a quiet re-light does not nag. A spoken "turn it off" (off_override) holds the auto-on
    # until the view is no longer dark.
    if msg.level == "dark" and msg.torch == "off" and not st.unsupported_said \
            and st.torch_requested is None and not st.off_override:
        st.torch_requested = "on"
        await session.send(TorchMsg(on=True))
        diag("light", action="torch_command", session=session.session_id, on=True, source="auto")
        if not st.dark_offered:
            st.dark_offered = True
            await _tell(session, "light.torch_on_dark", TORCH_ON_DARK_EN)
        return
    # Bright again, sustained for TORCH_OFF_BRIGHT_S: turn it off and say so, once per torch-on episode
    # (off_offered, cleared when the phone reports the torch off). Rides the hysteresis-smoothed level, not the
    # noisy raw luma, so a steady lit room turns it off reliably and the torch alone (only "dim") never does.
    if msg.torch == "on":
        now = _monotonic()
        if msg.level == "bright":
            if st.bright_since is None:
                st.bright_since = now
            elif now - st.bright_since >= TORCH_OFF_BRIGHT_S and not st.off_offered and st.torch_requested is None:
                st.off_offered = True
                st.torch_requested = "off"
                await session.send(TorchMsg(on=False))
                diag("light", action="torch_command", session=session.session_id, on=False, source="auto")
                await _tell(session, "light.torch_off_bright", TORCH_OFF_BRIGHT_EN)
        else:
            st.bright_since = None


# ---------- the light switch ----------
def _route_module():
    """Part A's route follower, if it is on this branch; None otherwise (never raises)."""
    try:
        mod = importlib.import_module(ROUTE_MODULE)
    except ImportError:
        return None
    return mod if callable(getattr(mod, "route_to", None)) else None


async def light_switch_find(target: str = "light switch") -> dict:
    """Help the owner find and press the light switch when he asks where it is or wants the lights on, in any
    language ("where is the light switch", "put the light on", "லைட் ஸ்விட்ச் எங்கே", "लाइट कहाँ है"). With a shop
    map that knows the switch board, the walk goes there first; then the camera finds the switches and the
    hand guidance moves his finger to one (never to a power socket). The result is facts with a say_en meaning:
    say it in the language he is speaking. If it needs_confirm (the flashlight first), read the question and
    wait for his yes."""
    s: Session = get_session()
    settings = config.load()
    st = state(s)
    route = _route_module()
    if route is not None:
        try:
            r = await route.route_to(s, ROUTE_PLACE)
        except Exception:
            log.exception("light: route_to failed; falling back to the camera")
            r = None
        if isinstance(r, dict) and r:
            return {"routed": True, "then": "light_switch_find", **r,
                    "say_en": r.get("say_en") or "I'll guide you to the switch board first."}
    if settings.torch and st.level == "dark" and st.torch == "off" and not st.unsupported_said \
            and st.torch_requested is None:
        st.switch_after_torch = True
        st.dark_offered = True
        out = await _propose(s, st, on=True)
        return {**out, "then": "light_switch_find"}
    loop = await start_switch(s)
    return {"active": loop.active, "target": target, "say_en": SWITCH_START_EN}


class SwitchLoop:
    """Guide frames -> structured detections -> a code decision -> ``target`` for the phone's hand guidance."""

    def __init__(self, session: Session, label: str | None = None, interval_s: float = SWITCH_INTERVAL_S,
                 frame_timeout_s: float = SWITCH_FRAME_TIMEOUT_S, max_s: float = MAX_SWITCH_S,
                 press_window_s: float = PRESS_WINDOW_S):
        self.session, self.label = session, label
        self.interval_s, self.frame_timeout_s, self.max_s, self.press_window_s = (
            interval_s, frame_timeout_s, max_s, press_window_s)
        self.active = True
        self.index = 0                  # which switch (from the left) to try next
        self.stage = "seek"             # seek -> press -> seek (next) / done
        self.last_ts = 0.0
        self.misses = 0
        self.decision: vswitch.Decision | None = None
        self.press_luma: float | None = None
        self.press_at: float | None = None
        self.task: asyncio.Task | None = None
        self._press_timer: asyncio.Task | None = None
        self._touch_busy = False

    async def step(self) -> bool:
        """One tick: newest unseen guide frame -> detection -> decision -> target (or stop). False once stopped."""
        if not self.active or self.stage != "seek":
            return self.active
        s = self.session
        fresh = await s.frames.wait_for(count=1, purpose="guide", after_ts=self.last_ts, timeout_s=self.frame_timeout_s)
        if not fresh or not self.active:
            return self.active
        newest = s.frames.latest(1, purpose="guide")
        frame = newest[-1] if newest and newest[-1].ts > self.last_ts else fresh[-1]
        self.last_ts = frame.ts
        try:
            det = await vswitch.detect_switchboard(frame)
        except VisionError as exc:
            log.warning("light: switch detection failed: %s", exc)
            return self.active
        if not self.active:
            return False
        await self.on_detection(det, frame.id)
        return self.active

    async def on_detection(self, det: vswitch.BoardDetection, frame_id: str) -> vswitch.Decision:
        """Apply one detection (pure decision in ``vision.switches.choose_switch``)."""
        d = vswitch.choose_switch(det, self.index, self.label)
        diag("light", action="switch_decision", session=self.session.session_id, kind=d.kind, reason=d.reason,
             index=d.index, total=d.total, sockets=d.sockets)
        if d.kind == "guide":
            self.decision, self.misses = d, 0
            await self.session.send(TargetMsg(frame_id=frame_id, label=d.name, point=d.point, box=d.box,
                                              confidence=det.confidence))
            monitor.event(self.session, "switch_target", name=d.name, index=d.index, total=d.total)
        elif d.kind == "unsure":
            # Never a target toward a socket, and never a guess: say so and stop. The stop also ends the phone's
            # tones and fingertip tracking (request_frame guide stop).
            await self._finish()
            await _tell(self.session, "light.not_sure", NOT_SURE_EN, cue="unsure", urgent=True)
        elif d.reason == "exhausted":
            await self._finish()
            await _tell(self.session, "light.no_more", NO_MORE_EN, cue="unsure")
        else:
            self.misses += 1
            if self.misses == LOST_AFTER:
                await _tell(self.session, "light.no_board", NO_BOARD_EN, cue="unsure")
        return d

    async def run(self) -> None:
        deadline = _monotonic() + self.max_s
        while self.active:
            if _monotonic() >= deadline:
                if await self._finish():
                    await _tell(self.session, "light.switch_stopped", SWITCH_STOPPED_EN, cue="warn")
                return
            try:
                if not await self.step():
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("light: switch step failed")
            await asyncio.sleep(self.interval_s)

    async def on_touch(self) -> dict | None:
        """The fingertip dwelt on the target: say which switch it is, remember the brightness, wait for the press."""
        if not self.active or self.stage != "seek" or self.decision is None or self._touch_busy:
            return None
        self._touch_busy = True
        try:
            st = state(self.session)
            self.stage = "press"
            self.press_luma = st.luma
            self.press_at = _monotonic()
            await _tell(self.session, "light.switch_press", SWITCH_PRESS_EN.format(name=self.decision.name),
                        cue="match", name=self.decision.name)
            self._arm_press_timer()
            return {"stage": "press", "name": self.decision.name, "luma_before": self.press_luma}
        finally:
            self._touch_busy = False

    def _arm_press_timer(self) -> None:
        if self._press_timer is not None and not self._press_timer.done():
            self._press_timer.cancel()

        async def wait() -> None:
            await asyncio.sleep(self.press_window_s)
            await self.on_press_timeout()

        try:
            self._press_timer = asyncio.create_task(wait())
        except RuntimeError:  # no running loop (tests driving it by hand)
            self._press_timer = None

    async def on_light(self, luma: float | None) -> bool:
        """A new raw luma while waiting for the press: rose enough -> the light is on (True)."""
        if not self.active or self.stage != "press" or luma is None:
            return False
        if not brightness_rose(self.press_luma, luma):
            return False
        if self._press_timer is not None and not self._press_timer.done():
            self._press_timer.cancel()
        self.stage = "done"
        await self._finish()
        await _tell(self.session, "light.on_now", LIGHT_ON_EN, cue="done")
        st = state(self.session)
        if config.load().torch and st.torch == "on" and not st.off_offered and st.torch_requested is None:
            st.off_offered = True
            await _propose(self.session, st, on=False)
        return True

    async def on_press_timeout(self) -> None:
        """No brightness rise within the window: still dark, try the next switch from the left."""
        if not self.active or self.stage != "press":
            return
        self.stage = "seek"
        self.press_luma, self.press_at = None, None
        self.index += 1
        total = self.decision.total if self.decision else 0
        self.decision = None
        if total and self.index >= total:
            await self._finish()
            await _tell(self.session, "light.still_dark", f"{STILL_DARK_EN} {NO_MORE_EN}", cue="unsure")
            return
        await _tell(self.session, "light.still_dark", STILL_DARK_EN, cue="unsure")

    async def _finish(self) -> bool:
        sid = self.session.session_id
        if _LOOPS.get(sid) is self:
            del _LOOPS[sid]
        was = self.active
        await self.stop()
        return was

    async def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        for t in (self.task, self._press_timer):
            if t is not None and t is not asyncio.current_task() and not t.done():
                t.cancel()
        try:
            await self.session.send(RequestFrameMsg(purpose="guide", stop=True))
        except Exception:
            log.warning("light: could not send guide stop to %s", self.session.session_id, exc_info=True)


def brightness_rose(before: float | None, after: float | None, rise: float = BRIGHT_RISE_LUMA) -> bool:
    """Pure: the raw luma after the press is at least ``rise`` above the one before it."""
    if before is None or after is None:
        return False
    return (after - before) >= rise


async def start_switch(session: Session, label: str | None = None, run: bool = True, **kw) -> SwitchLoop:
    """Replace any switch loop (and any product hand guidance) of this session and start a new one."""
    await stop_session(session)
    try:  # the product guidance loop uses the same guide frames and target messages: one at a time
        from aira_live.tools import guidance
        await guidance.stop_guidance(session)
    except ImportError:
        pass
    loop = SwitchLoop(session, label, **kw)
    await session.send(RequestFrameMsg(purpose="guide", continuous=True))
    _LOOPS[session.session_id] = loop
    if run:
        loop.task = asyncio.create_task(loop.run(), name=f"switch-{session.session_id}")
    return loop


def active(session: Session) -> SwitchLoop | None:
    return _LOOPS.get(session.session_id)


async def handle_client_event(session: Session, name: str, data: dict | None) -> None:
    """Cross-team hook: every client ``event``; only ``fingertip_in_target`` matters, and only during a switch loop."""
    if name != "fingertip_in_target":
        return
    loop = _LOOPS.get(session.session_id)
    if loop is not None and loop.active:
        await loop.on_touch()


async def stop_session(session: Session) -> None:
    """Cross-team hook: a Stop message, a disconnect or a new loop. Safe to call twice."""
    loop = _LOOPS.pop(session.session_id, None)
    if loop is not None:
        await loop.stop()


def forget(session_id: str) -> None:
    """The session ended: drop its light state (tests and ``main._end_session``)."""
    _STATE.pop(session_id, None)
