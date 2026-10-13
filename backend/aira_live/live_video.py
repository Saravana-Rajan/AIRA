"""The phone camera as Gemini Live video, and the "say what is being shown" nudge.

While a Live session runs and the camera is on, the phone sends one ``live`` frame a second (JPEG, <= 768 px:
the official Live API guidance). ``LiveVideo.offer`` forwards it into the Live session as realtime video
(``LiveRequestQueue.send_realtime(Blob(image/jpeg))``) so "what is this?" is answered by Live itself. Precise
work (counts, label prices, money, expiry dates) stays on the tools; the system prompt says so.

- Throttle: at most one frame per ``MIN_GAP_S`` per session; a frame is dropped while the Live request queue
  is already ``MAX_BACKLOG`` items behind (stale video is worse than none). A ``changed`` frame (the phone's
  "something is being shown" fast path) only needs ``CHANGED_MIN_GAP_S``: it never loses to the 1 fps tick.
- Nudge: right after a ``changed`` frame is forwarded (same call, so Live already has that picture) one short
  user turn asks Live to say in ONE sentence what is being shown, in the owner's language; price and stock only
  through the tools. Gated: never while Sam or a customer speaks, while AIRA is mid-sentence or a tool runs; at
  most one per ``NUDGE_GAP_S``; the same labels at most once per ``NUDGE_DEDUPE_S``. Every decision is logged
  (``diag("live_nudge", decision=...)``): ``sent`` or ``skipped:speaking|aira|tool|busy|gap|dedup|off``.
- Diagnostics: forwarded / dropped / nudged counts and the skip reasons, once a minute per session (never any
  picture).
- Flags: ``AIRA_LIVE_VIDEO`` (default on) and ``AIRA_LIVE_NUDGE`` (default on), read in ``aira_live.config``.
"""
from __future__ import annotations

import base64
import binascii
import logging
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from aira_live.diag import diag

if TYPE_CHECKING:
    from aira_live.live_bridge import LiveBridge

log = logging.getLogger(__name__)

PURPOSE = "live"
MIN_GAP_S = 0.9          # ~1 fps per session (a little slack for the phone's timer jitter)
CHANGED_MIN_GAP_S = 0.25  # a "shown" frame from the phone's fast path goes even right after a regular one
MAX_BACKLOG = 6          # Live request queue items waiting: more than this and the frame is dropped
NUDGE_GAP_S = 3.0        # at most one nudge every 3 s
NUDGE_DEDUPE_S = 10.0    # the same labels nudge at most once per 10 s
QUIET_AFTER_SPEECH_S = 1.5  # no nudge this soon after the last words heard or spoken
DIAG_EVERY_S = 60.0

NUDGE_PREFIX = "[SYSTEM NOTE] Camera"


def labels_key(labels: Iterable[str] | None) -> str:
    """The identity of a label set: trimmed, lower case, sorted, unique."""
    return ",".join(sorted({str(x).strip().lower() for x in (labels or []) if str(x).strip()}))


def nudge_text(labels: Iterable[str] | None, lang_name: str) -> str:
    seen = labels_key(labels)
    hint = f" (the phone's detector guesses: {seen.replace(',', ', ')})" if seen else ""
    return (f"{NUDGE_PREFIX}: something is being shown to the camera right now{hint}. Look at the latest video "
            f"frame and say in ONE short sentence, in {lang_name}, what is being shown. Price or stock only from "
            "your tools: never read a count, a price, money or an expiry date off the video and never invent "
            "one. If nothing new is being shown, stay silent.")


def narrates(bridge: object | None) -> bool:
    """True when Live itself speaks up on scene changes for this bridge (video and nudge on): the old ER-based
    scene narration (tools.describe) then keeps its boxes and memory but stays silent."""
    video = getattr(bridge, "video", None)
    return bool(getattr(video, "nudge_on", False))


def ambient_holding(session_id: str) -> bool:
    """The ambient hold-up loop is identifying something or has just spoken about it (tools.ambient)."""
    from aira_live.tools import ambient  # lazy: tools import the bridge

    amb = ambient._ACTIVE.get(session_id)
    return bool(amb is not None and amb.holdup.holding())


class LiveVideo:
    """One Live session's video throttle, nudge gate and counters. The clock is injectable for tests."""

    def __init__(self, session_id: str, *, nudge: bool = True, clock: Callable[[], float] = time.monotonic,
                 tools_running: Callable[[], int] | None = None, other_busy: Callable[[], bool] | None = None):
        self.session_id = session_id
        self.nudge_on = nudge
        self._clock = clock
        self._tools_running = tools_running or (lambda: 0)
        self._other_busy = other_busy or (lambda: False)  # e.g. ambient hold-up identifying / just spoke
        self.last_sent_at = float("-inf")
        self.last_nudge_at = float("-inf")
        self._nudged: dict[str, float] = {}  # labels key -> when it last nudged
        self.forwarded = self.dropped = self.nudged = self.nudge_skipped = 0
        self.skip_reasons: dict[str, int] = {}
        self._window_at = clock()

    # ---------- forwarding ----------
    async def offer(self, bridge: LiveBridge, jpeg_b64: str, changed: bool = False,
                    labels: Iterable[str] | None = None) -> bool:
        """Forward one live frame into ``bridge`` (True), or drop it (False). A ``changed`` frame is followed at
        once by the nudge (when the gate allows). Never raises."""
        now = self._clock()
        try:
            gap = CHANGED_MIN_GAP_S if changed else MIN_GAP_S
            if now - self.last_sent_at < gap or bridge.video_backlog() > MAX_BACKLOG:
                self.dropped += 1
                return False
            try:
                jpeg = base64.b64decode(jpeg_b64, validate=True)
            except (binascii.Error, ValueError):
                self.dropped += 1
                return False
            if not await bridge.send_video(jpeg):
                self.dropped += 1
                return False
            self.last_sent_at = now
            self.forwarded += 1
            if changed:
                await self._maybe_nudge(bridge, labels, now)
            return True
        finally:
            self.flush(now)

    # ---------- the nudge ----------
    def nudge_block(self, bridge: LiveBridge, labels: Iterable[str] | None, now: float) -> str | None:
        """Why a nudge may not go now (``skipped:<reason>``), or None when it may."""
        if not self.nudge_on:
            return "skipped:off"
        if bridge.answering:
            return "skipped:aira"      # AIRA is mid-sentence
        if bridge.user_speaking:
            return "skipped:speaking"  # Sam or a customer is talking
        if now - bridge.last_speech_at < QUIET_AFTER_SPEECH_S:
            return "skipped:speaking"  # words were just heard or spoken
        if self._tools_running() > 0:
            return "skipped:tool"
        try:
            if self._other_busy():
                return "skipped:busy"  # a walk guidance or an ambient hold-up line owns the voice
        except Exception:
            log.debug("live video: busy check failed", exc_info=True)
        if now - self.last_nudge_at < NUDGE_GAP_S:
            return "skipped:gap"
        key = labels_key(labels)
        if key and now - self._nudged.get(key, float("-inf")) < NUDGE_DEDUPE_S:
            return "skipped:dedup"
        return None

    def _decided(self, decision: str, labels: Iterable[str] | None) -> None:
        diag("live_nudge", session=self.session_id, decision=decision, labels=labels_key(labels)[:120])

    async def _maybe_nudge(self, bridge: LiveBridge, labels: Iterable[str] | None, now: float) -> bool:
        block = self.nudge_block(bridge, labels, now)
        if block is not None:
            self.nudge_skipped += 1
            self.skip_reasons[block] = self.skip_reasons.get(block, 0) + 1
            self._decided(block, labels)
            return False
        self.last_nudge_at = now
        key = labels_key(labels)
        if key:
            self._nudged = {k: at for k, at in self._nudged.items() if now - at < NUDGE_DEDUPE_S}
            self._nudged[key] = now
        self.nudged += 1
        self._decided("sent", labels)
        await bridge.send_text(nudge_text(labels, bridge.lang_name()))
        return True

    # ---------- diagnostics ----------
    def flush(self, now: float | None = None, force: bool = False) -> None:
        """Log the counts once a minute (or now, with ``force``), then start a new window."""
        now = self._clock() if now is None else now
        if not force and now - self._window_at < DIAG_EVERY_S:
            return
        if self.forwarded or self.dropped or self.nudged or self.nudge_skipped:
            diag("live_video", session=self.session_id, forwarded=self.forwarded, dropped=self.dropped,
                 nudged=self.nudged, nudge_skipped=self.nudge_skipped, skip_reasons=dict(self.skip_reasons),
                 window_s=round(now - self._window_at))
        self.forwarded = self.dropped = self.nudged = self.nudge_skipped = 0
        self.skip_reasons = {}
        self._window_at = now
