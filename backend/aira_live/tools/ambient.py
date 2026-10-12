"""Ambient mode: AIRA speaks by itself about what the owner shows the camera and what comes into view.

The owner asked: "When I show it something in the video, it should speak and guide me about it, without me
asking." Ambient mode is ON by default once the session is up (the switch is persisted per shop):

- Hold-up: the phone watches its own MediaPipe hand and object boxes and sends ONE ``identify_held`` frame when
  a hand holds something near the centre of the view, close and steady (at most one every 3 s, never twice for
  the same object). ``HoldUpLoop`` identifies it against the catalog first ("That's Aavin Milk, 500 ml. 30
  rupees.") and otherwise with a free-text look ("That's a water bottle."). Below confidence it says "Hold it a
  little closer", at most once per 8 s.
- Scene: ``tools.describe.DescribeLoop`` (what comes into view as he moves) runs at the same time, with its own
  rate limits and ER 2 budget.
- Priorities: nothing is said while the owner talks or AIRA answers (the Live bridge knows); a hold-up line makes
  the scene loop wait ``HOLD_AFTER_SPEECH_S``; the phone's bump guard mutes hold-up frames by itself. Every line
  is in the language of the session, and the model is told these lines are system notices not to repeat.
- Voice: "stop describing" / "quiet mode" turn it off, "describe again" turns it on (handled here, before the
  model, so it is instant and never doubled); ``describe_mode`` (the Live tool) switches it as well.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from aira_live import db as dbmod
from aira_live import sessions
from aira_live.contracts import RequestFrameMsg, TranscriptMsg
from aira_live.i18n import t
from aira_live.live_bridge import on_input_transcript
from aira_live.session import Session
from aira_live.tools import describe
from aira_live.tools.camera import PERSON
from aira_live.tools.products import MIN_ID_CONF, shop_products
from aira_live.vision.gemini import VisionError, generate_json, image_part
from aira_live.vision.identify import identify_product

log = logging.getLogger(__name__)

PURPOSE = "identify_held"
MIN_GAP_S = 3.0             # at most one identification every 3 s (the phone keeps the same limit)
AIM_GAP_S = 8.0             # "hold it a little closer" at most once per 8 s
HOLD_AFTER_SPEECH_S = 4.0   # the scene loop stays quiet this long after a hold-up line
FRAME_TIMEOUT_S = 3.0
FREE_MIN_CONF = 0.5         # a free-text label below this is "hold it closer", never a guess
MAX_CALLS = 120             # identifications per session (cost control)
START_DELAY_S = 1.5         # after hello: let the greeting go first
SETTING_TIMEOUT_S = 2.0
MAX_LABEL = 40

FREE_PROMPT = (
    "A blind shopkeeper is holding up ONE object to this chest camera. Name the object in two or three plain "
    "words with an article, for example \"a water bottle\", \"a bunch of keys\", \"a blue plastic bag\". "
    "Never describe or identify people: if the main thing is a person, return an empty label. "
    "If nothing is clearly held up, or the picture is dark or blurred, return an empty label and confidence "
    "below 0.5."
)
FREE_SCHEMA = '{"label": "<a short name with an article, or empty>", "confidence": 0.0}'

_ON = re.compile(r"(describe again|start describ|keep describ|describe mode on|start telling|tell me again what|"
                 r"மீண்டும் விவரி|விவரி.*தொடங்கு|फिर से बताओ|बताना शुरू|फिर बताओ)", re.I)
_OFF = re.compile(r"(stop describing|stop telling|quiet mode|be quiet|stay quiet|don.?t describe|no more describing|"
                  r"silent mode|அமைதி|விவரிப்பதை நிறுத்து|சொல்லாதே|शांत मोड|चुप|बताना बंद|मत बताओ)", re.I)
_WS = re.compile(r"\s+")

_ACTIVE: dict[str, "Ambient"] = {}
_SWITCH: dict[str, Callable[[], None]] = {}   # session_id -> unsubscribe of its voice switch


def parse_ambient_command(text: str) -> bool | None:
    """False for "stop describing" / "quiet mode", True for "describe again" / "start describing", else None."""
    q = _WS.sub(" ", text or "").strip()
    if not q:
        return None
    if _OFF.search(q):
        return False
    if _ON.search(q):
        return True
    return None


# --- the persisted switch (shops/{shop}/prefs/ambient) ------------------------------------------------------------


def _setting_doc(client, shop_id: str):
    return client.collection("shops").document(shop_id).collection("prefs").document("ambient")


async def load_setting(shop_id: str) -> bool:
    """The shop's ambient switch; True (on) until the owner turns it off."""
    client = await asyncio.to_thread(dbmod.db)        # the first client build may look for credentials
    snap = await _setting_doc(client, shop_id).get()
    data = (snap.to_dict() or {}) if snap.exists else {}
    return data.get("on") is not False


async def save_setting(shop_id: str, on: bool) -> None:
    await _setting_doc(dbmod.db(), shop_id).set({"on": bool(on)}, merge=True)


async def _persist(shop_id: str, on: bool) -> None:
    try:
        await asyncio.wait_for(save_setting(shop_id, on), SETTING_TIMEOUT_S)
    except Exception:
        log.warning("ambient: could not save the switch for %s", shop_id, exc_info=True)


# --- the identification --------------------------------------------------------------------------------------------


def _conf(v: Any) -> float:
    if isinstance(v, bool):
        return 0.0
    try:
        f = float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if f > 1.0:
        f = f / 100.0 if f <= 100.0 else 0.0
    return min(max(f, 0.0), 1.0)


def parse_free_label(data: Any) -> tuple[str, float, bool]:
    """Pure: ``(label, confidence, person)`` from the free-text reply. A person gives an empty label and
    ``person=True`` (silence, not even an aim hint); junk gives an empty label."""
    if not isinstance(data, dict):
        return "", 0.0, False
    raw = data.get("label")
    label = _WS.sub(" ", raw).strip().strip(".")[:MAX_LABEL] if isinstance(raw, str) else ""
    if label and PERSON.search(label):
        return "", 0.0, True
    return label, _conf(data.get("confidence")), False


async def _price(shop_id: str, sku: str):
    try:
        snap = await dbmod.db().collection("shops").document(shop_id).collection("products").document(sku).get()
    except Exception:
        log.warning("ambient: price lookup failed for %s/%s", shop_id, sku, exc_info=True)
        return None
    price = (snap.to_dict() or {}).get("price") if snap.exists else None
    if price is None or isinstance(price, bool) or not isinstance(price, (int, float)):
        return None
    return int(price) if float(price).is_integer() else price


async def identify_held(session: Session, frame) -> tuple[str | None, str]:
    """``(line, kind)``: the catalog line with its price (kind "catalog"), a free-text line ("free"), or
    ``(None, "low")`` when neither is confident. ``(None, "person")`` is silence. Raises VisionError."""
    s = session
    candidates = await shop_products(s.shop_id)
    if candidates:
        res = await identify_product(frame, candidates)
        ref = next((c for c in candidates if c.sku == res.sku), None)
        if ref is not None and res.confidence >= MIN_ID_CONF:
            line = t("ambient.held", s.lang, name=ref.name, pack=ref.pack)
            price = await _price(s.shop_id, ref.sku)
            if price is not None:
                line = f"{line} {t('ambient.held_price', s.lang, price=price)}"
            return line, "catalog"
    data = await generate_json("point", [image_part(frame.jpeg_b64), FREE_PROMPT], FREE_SCHEMA,
                               thinking="low", timeout_s=6.0)
    label, conf, person = parse_free_label(data)
    if person:
        return None, "person"
    if not label or conf < FREE_MIN_CONF:
        return None, "low"
    return t("ambient.held_free", s.lang, label=label), "free"


class HoldUpLoop:
    """Waits for ``identify_held`` frames and speaks one short line per held-up object."""

    def __init__(self, session: Session, *, min_gap_s: float = MIN_GAP_S, aim_gap_s: float = AIM_GAP_S,
                 frame_timeout_s: float = FRAME_TIMEOUT_S, max_calls: int = MAX_CALLS,
                 busy: Callable[[], bool] | None = None, clock: Callable[[], float] = time.monotonic):
        self.session = session
        self.min_gap_s, self.aim_gap_s, self.frame_timeout_s, self.max_calls = min_gap_s, aim_gap_s, frame_timeout_s, max_calls
        self._busy, self._clock = busy, clock
        self.active = True
        self.task: asyncio.Task | None = None
        self.last_ts = 0.0                  # newest hold-up frame already looked at (phone clock)
        self.calls = 0
        self.spoken = 0
        self.last_call_at = float("-inf")
        self.last_aim_at = float("-inf")
        self.hold_until = float("-inf")     # scene lines wait until then
        self.identifying = False

    def busy(self) -> bool:
        if self._busy is not None:
            return bool(self._busy())
        return describe._bridge_busy(self.session)

    def holding(self) -> bool:
        """True while a hold-up line is fresh or an identification is in flight: scene lines wait."""
        return self.identifying or self._clock() < self.hold_until

    async def step(self) -> bool:
        """One tick: newest unseen hold-up frame -> identify -> at most one line. False once stopped."""
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
        now = self._clock()
        if self.busy() or now - self.last_call_at < self.min_gap_s or self.calls >= self.max_calls:
            return self.active                          # dropped: the phone sends again for a new object
        self.calls += 1
        self.last_call_at = now
        self.identifying = True
        try:
            line, kind = await identify_held(s, frame)
        except VisionError as exc:
            log.warning("ambient: identification failed for %s: %s", s.shop_id, exc)
            return self.active
        finally:
            self.identifying = False
        if not self.active:
            return False
        log.info("ambient: session=%s call=%d kind=%s", s.session_id, self.calls, kind)
        if self.busy():
            return self.active                          # someone started talking meanwhile: not now
        now = self._clock()
        if line is None:
            if kind == "low" and now - self.last_aim_at >= self.aim_gap_s:
                self.last_aim_at = now
                await self._say(t("ambient.closer", s.lang))
            return self.active
        self.spoken += 1
        self.hold_until = now + HOLD_AFTER_SPEECH_S
        await self._say(line)
        return self.active

    async def run(self) -> None:
        while self.active:
            if not describe._registered(self.session):
                await self.stop()
                return
            try:
                if not await self.step():
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ambient hold-up step failed for %s", self.session.session_id)
                await asyncio.sleep(0.5)

    async def _say(self, text: str) -> None:
        s = self.session
        try:
            await s.send(TranscriptMsg(role="aira", text=text, final=True, lang=s.lang))
            bridge = describe._bridge_of(s)
            if bridge is not None:
                await bridge.speak_notice(text)
        except Exception:
            log.warning("ambient: session %s unreachable", s.session_id, exc_info=True)

    async def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        if self.task is not None and self.task is not asyncio.current_task() and not self.task.done():
            self.task.cancel()
        try:
            await self.session.send(RequestFrameMsg(purpose=PURPOSE, stop=True))
        except Exception:
            log.warning("ambient: could not send stop to session %s", self.session.session_id, exc_info=True)


# --- the mode -------------------------------------------------------------------------------------------------------


class Ambient:
    """One session's running ambient mode: the hold-up loop plus the scene loop."""

    def __init__(self, session: Session, holdup: HoldUpLoop):
        self.session = session
        self.holdup = holdup

    async def close(self) -> None:
        await self.holdup.stop()
        await describe.stop_describe(self.session)


def _ensure_switch(session: Session) -> None:
    """The voice switch of ``session``: registered once, kept while the session lives (also while ambient mode
    is off, so "describe again" works), and ahead of the scene loop's own listener so "stop describing" is
    answered once."""
    if session.session_id in _SWITCH:
        return

    async def on_utterance(text: str) -> None:
        want = parse_ambient_command(text)
        if want is None:
            return
        if want:
            await start(session)
            await _say(session, t("ambient.on", session.lang))
        elif await stop(session):
            await _say(session, t("ambient.off", session.lang))

    _SWITCH[session.session_id] = on_input_transcript(session.session_id, on_utterance)


def _drop_switch(session: Session) -> None:
    unsubscribe = _SWITCH.pop(session.session_id, None)
    if unsubscribe is not None:
        unsubscribe()


async def _say(s: Session, text: str) -> None:
    try:
        await s.send(TranscriptMsg(role="aira", text=text, final=True, lang=s.lang))
        bridge = describe._bridge_of(s)
        if bridge is not None:
            await bridge.speak_notice(text)
    except Exception:
        log.warning("ambient: session %s unreachable", s.session_id, exc_info=True)


def active(session: Session) -> Ambient | None:
    return _ACTIVE.get(session.session_id)


def is_on(session: Session) -> bool:
    return session.session_id in _ACTIVE


async def start(session: Session, run: bool = True, persist: bool = True, **kw) -> Ambient:
    """(Re)start ambient mode for ``session``: the hold-up loop, the scene loop and the voice switch."""
    previous = _ACTIVE.pop(session.session_id, None)
    if previous is not None:
        await previous.close()
    clock = kw.pop("clock", time.monotonic)
    busy = kw.pop("busy", None)
    holdup = HoldUpLoop(session, clock=clock, busy=busy, **kw)
    amb = Ambient(session, holdup)
    _ACTIVE[session.session_id] = amb
    _ensure_switch(session)
    scene_busy = (lambda: holdup.holding() or busy()) if busy is not None else \
        (lambda: holdup.holding() or describe._bridge_busy(session))
    await describe.start(session, run=run, busy=scene_busy, clock=clock)
    await session.send(RequestFrameMsg(purpose=PURPOSE, continuous=True))
    if run:
        holdup.task = asyncio.create_task(holdup.run(), name=f"ambient-{session.session_id}")
    if persist:
        await _persist(session.shop_id, True)
    return amb


async def stop(session: Session, persist: bool = True) -> bool:
    """Turn ambient mode off (and remember it). True when it was on."""
    amb = _ACTIVE.pop(session.session_id, None)
    if amb is None:
        return False
    await amb.close()
    if persist:
        await _persist(session.shop_id, False)
    return True


async def set_mode(session: Session, on: bool) -> None:
    """The ``describe_mode`` Live tool: on starts (or restarts) ambient mode, off stops it."""
    if on:
        await start(session)
    else:
        await stop(session)


async def _delayed_start(session: Session, delay_s: float) -> None:
    try:
        if delay_s > 0:
            await asyncio.sleep(delay_s)
        if not describe._registered(session):
            return
        _ensure_switch(session)
        try:
            on = await asyncio.wait_for(load_setting(session.shop_id), SETTING_TIMEOUT_S)
        except Exception:
            log.warning("ambient: could not read the switch for %s; assuming on", session.shop_id, exc_info=True)
            on = True
        if on and describe._registered(session) and session.session_id not in _ACTIVE:
            await start(session, persist=False)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("ambient: start failed for %s", session.session_id)


async def on_session_start(session: Session, delay_s: float = START_DELAY_S) -> None:
    """main.py hook (real mode, after Live is up): start ambient mode unless the owner switched it off."""
    asyncio.create_task(_delayed_start(session, delay_s), name=f"ambient-start-{session.session_id}")


async def stop_session(session: Session) -> None:
    """main.py hook on disconnect: end the loops and the voice switch, keep the persisted setting."""
    _drop_switch(session)
    await stop(session, persist=False)
