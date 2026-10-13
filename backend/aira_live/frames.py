"""Camera frames from the phone: a small ring buffer per session that tools can wait on.

One-off tool requests (``Session.request_frames``) are correlated by id: ``open_request`` registers a request,
the phone echoes its ``request_id`` on every frame it captures for it, and ``add`` hands such a frame to that
request only. A frame for an unknown or expired request never satisfies anything (``stale_frame``); a duplicate
(same id and ``ts``) is stored once. Phones that predate request ids send untagged frames: each one is claimed by
the oldest open request of its purpose that still needs frames (it arrived after that request opened), so two
concurrent same-purpose requests never share a frame. Once a phone has sent one tagged frame, its untagged frames
(continuous streams) are never claimed by requests; a frame carrying ``capture_ts`` marks such a phone too.

Clocks: ``Frame.ts`` and ``Frame.capture_ts`` come from the phone; they are only ever subtracted from each other
(``phone_age_ms``: grab -> send on the phone). Everything else is measured on the server's monotonic clock (arrival,
request start). So a frame's capture age is ``phone_age_ms`` + time since it arrived here; the uplink transit is
not measurable without synchronised clocks and is not counted (for a request's own frames it is bounded by the
request's wait, since the phone grabs them only after it received the request).
"""
import asyncio
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field

from aira_live.diag import diag

# Per-purpose limit on a frame's capture age (ms, see the module doc) for a tool request to use it.
MAX_CAPTURE_AGE_MS: dict[str, int] = {
    "ask": 2000, "identify": 2000, "point": 2000, "identify_held": 2000,
    "count": 3000, "walk": 1500, "guide": 1500,
    "invoice": 5000, "cash": 5000, "describe": 5000,
}
DEFAULT_MAX_CAPTURE_AGE_MS = 3000
# An expired request id is remembered this long (late frames for it are logged, then ignored) ...
EXPIRED_KEEP_S = 10.0
# ... and at most this many are kept.
MAX_EXPIRED = 64
MAX_SEEN = 256  # (frame id, ts) pairs remembered to drop duplicates


def max_capture_age_ms(purpose: str) -> int:
    return MAX_CAPTURE_AGE_MS.get(purpose, DEFAULT_MAX_CAPTURE_AGE_MS)


@dataclass(frozen=True)
class Frame:
    id: str
    jpeg_b64: str
    w: int
    h: int
    ts: float  # phone clock, epoch ms (stamped after encoding)
    purpose: str
    request_id: str | None = None  # the tool request this frame answers (None: continuous/background/old phone)
    capture_ts: float | None = None  # phone clock, epoch ms, when the picture was grabbed (before encoding)

    @property
    def phone_age_ms(self) -> float:
        """Grab -> send on the phone (same clock, so skew-free); 0 when the phone did not say."""
        return max(0.0, self.ts - self.capture_ts) if self.capture_ts is not None else 0.0

    @property
    def order_key(self) -> float:
        return self.capture_ts if self.capture_ts is not None else self.ts


@dataclass
class _Request:
    id: str
    purpose: str
    count: int
    max_age_ms: float
    started: float  # server monotonic
    frames: list[Frame] = field(default_factory=list)


def _rid(frame) -> str | None:
    """The frame's request id; frames built without the field (tests, other sources) have none."""
    return getattr(frame, "request_id", None)


def phone_age_ms(frame) -> float:
    """Grab -> send on the phone (same clock, skew-free); 0 when the frame does not say."""
    cap = getattr(frame, "capture_ts", None)
    return max(0.0, frame.ts - cap) if cap is not None else 0.0


def _order_key(frame) -> float:
    cap = getattr(frame, "capture_ts", None)
    return cap if cap is not None else frame.ts


LateFn = Callable[[Frame, int], None]  # (the late frame, frames that had arrived in time)


class FrameStore:
    def __init__(self, maxlen: int = 30):
        self._buf: deque[Frame] = deque(maxlen=maxlen)
        self._cond = asyncio.Condition()
        # Server receive time (monotonic s) per stored frame, oldest first: the phone clock in ``Frame.ts`` is
        # never compared with the server's, so "how old is this frame" is measured from its arrival here (plus
        # the phone's own grab -> send time when the frame says).
        self._recent_frames: deque[tuple[float, Frame]] = deque(maxlen=maxlen)
        self._open: dict[str, _Request] = {}  # insertion order = start order
        # expired id -> (request, expired at, late callback or None once it fired)
        self._expired: OrderedDict[str, tuple[_Request, float, LateFn | None]] = OrderedDict()
        self._seen: deque[tuple[str, float]] = deque(maxlen=MAX_SEEN)
        self._seen_set: set[tuple[str, float]] = set()
        self.phone_tags = False  # this phone echoes request ids (then untagged frames are never claimed)

    def remember_recent(self, frame: Frame) -> None:
        """A frame for ``recent`` reuse only (the 1 fps ``live`` video): never in the tool ring buffer, so it
        never pushes out the frames tools wait for, and never wakes a waiter."""
        self._recent_frames.append((time.monotonic(), frame))

    def _duplicate(self, frame: Frame) -> bool:
        key = (frame.id, frame.ts)
        if key in self._seen_set:
            return True
        if len(self._seen) == self._seen.maxlen:
            self._seen_set.discard(self._seen[0])
        self._seen.append(key)
        self._seen_set.add(key)
        return False

    def add(self, frame: Frame) -> str:
        """Store one frame from the phone. Returns what happened: "stored", "request" (given to an open
        request), "duplicate", "stale" (unknown/expired request id) or "too_old" (over the request's age limit)."""
        if self._duplicate(frame):
            diag("stale_frame", reason="duplicate", purpose=frame.purpose, id=frame.id, request_id=_rid(frame))
            return "duplicate"
        self._prune_expired()
        outcome = "stored"
        if _rid(frame) is not None or getattr(frame, "capture_ts", None) is not None:
            self.phone_tags = True  # a phone that sends either field echoes request ids on its request frames
        if _rid(frame) is not None:
            req = self._open.get(_rid(frame))
            if req is None:
                gone = self._expired.get(_rid(frame))
                if gone is not None:
                    self._fire_late(_rid(frame), frame)
                diag("stale_frame", reason="expired" if gone else "unknown", purpose=frame.purpose, id=frame.id,
                     request_id=_rid(frame), phone_age_ms=round(phone_age_ms(frame)))
                self.remember_recent(frame)  # reusable by look_frame only while fresh (``recent``)
                return "stale"
            if not self._fresh_for(req, frame):
                return "too_old"
            if len(req.frames) < req.count:
                req.frames.append(frame)
                outcome = "request"
        else:
            req = self._claimant(frame)
            if req is not None and self._fresh_for(req, frame):  # too old: stays for continuous readers only
                req.frames.append(frame)
                outcome = "request"
            elif not self.phone_tags:
                self._late_untagged(frame)
        self._buf.append(frame)
        self._recent_frames.append((time.monotonic(), frame))
        try:
            asyncio.get_running_loop().create_task(self._notify())
        except RuntimeError:  # no running loop (sync callers): waiters re-check on their next wake-up
            pass
        return outcome

    def _fresh_for(self, req: _Request, frame: Frame) -> bool:
        if phone_age_ms(frame) <= req.max_age_ms:
            return True
        diag("stale_frame", reason="too_old", purpose=frame.purpose, id=frame.id, request_id=_rid(frame),
             phone_age_ms=round(phone_age_ms(frame)), max_age_ms=req.max_age_ms)
        return False

    def _claimant(self, frame: Frame) -> _Request | None:
        """The oldest open request of this purpose still short of frames (old phones only)."""
        if self.phone_tags:
            return None
        for req in self._open.values():
            if req.purpose == frame.purpose and len(req.frames) < req.count:
                return req
        return None

    def _late_untagged(self, frame: Frame) -> None:
        for rid, (req, _, on_late) in self._expired.items():
            if on_late is not None and req.purpose == frame.purpose:
                self._fire_late(rid, frame)
                return

    def _fire_late(self, rid: str, frame: Frame) -> None:
        req, at, on_late = self._expired[rid]
        if on_late is None:
            return
        self._expired[rid] = (req, at, None)  # once per request
        try:
            on_late(frame, len(req.frames))
        except Exception:  # a log line must never break frame intake
            pass

    def _prune_expired(self) -> None:
        now = time.monotonic()
        while self._expired:
            rid, (_, at, _) = next(iter(self._expired.items()))
            if now - at < EXPIRED_KEEP_S and len(self._expired) <= MAX_EXPIRED:
                break
            del self._expired[rid]  # only ever expired ids: open requests live in ``_open``

    # ---------- tool requests ----------
    def open_request(self, purpose: str, count: int, max_age_ms: float | None = None) -> str:
        rid = uuid.uuid4().hex[:12]
        self._open[rid] = _Request(id=rid, purpose=purpose, count=max(1, count),
                                   max_age_ms=max_capture_age_ms(purpose) if max_age_ms is None else max_age_ms,
                                   started=time.monotonic())
        return rid

    async def wait_request(self, rid: str, timeout_s: float) -> list[Frame]:
        """The open request's frames in capture order once it has all of them; [] on timeout."""
        req = self._open.get(rid)
        if req is None:
            return []
        try:
            async with asyncio.timeout(timeout_s):
                async with self._cond:
                    await self._cond.wait_for(lambda: len(req.frames) >= req.count)
        except TimeoutError:
            return []
        return sorted(req.frames, key=_order_key)[:req.count]

    def got(self, rid: str) -> int:
        req = self._open.get(rid) or (self._expired.get(rid) or (None,))[0]
        return len(req.frames) if req else 0

    def close_request(self, rid: str, on_late: LateFn | None = None) -> None:
        """End a request: its id is expired, so a frame that still arrives for it satisfies nothing else
        (``on_late`` hears about the first one). Never touches any other request."""
        req = self._open.pop(rid, None)
        if req is None:
            return
        self._expired[rid] = (req, time.monotonic(), on_late)
        self._prune_expired()

    def open_requests(self) -> int:
        return len(self._open)

    # ---------- continuous streams and reuse ----------
    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    def latest(self, n: int = 1, purpose: str | None = None) -> list[Frame]:
        items = [f for f in self._buf if purpose is None or f.purpose == purpose]
        return items[-n:] if n > 0 else []

    def recent(self, max_age_s: float, min_side: int = 0,
               exclude: frozenset[str] = frozenset({"preview"})) -> Frame | None:
        """The newest frame whose capture age (the phone's grab -> send time plus time since it reached the
        server) is under ``max_age_s``, with a long side of at least ``min_side`` px and a purpose outside
        ``exclude`` (the tiny monitor preview); None when there is none."""
        now = time.monotonic()
        for at, f in reversed(self._recent_frames):
            since_arrival = now - at
            if since_arrival >= max_age_s:
                return None  # older arrivals can only be older
            if since_arrival + phone_age_ms(f) / 1000 >= max_age_s:
                continue
            if f.purpose not in exclude and max(f.w, f.h) >= min_side:
                return f
        return None

    def arrival_monotonic(self, frame) -> float | None:
        """Server ``time.monotonic`` when this very frame (identity) reached the store, or None when it is not
        among the recent frames (never arrived, or already pushed out)."""
        return next((at for at, f in reversed(self._recent_frames) if f is frame), None)

    def last_ts(self, purpose: str | None = None) -> float:
        """The newest timestamp stored (for ``purpose``), or 0 when there is none."""
        return max((f.ts for f in self._buf if purpose is None or f.purpose == purpose), default=0.0)

    async def wait_for(self, count: int, purpose: str, after_ts: float, timeout_s: float) -> list[Frame]:
        """The first ``count`` frames of ``purpose`` newer than ``after_ts``; [] on timeout (continuous streams)."""
        def ready() -> list[Frame]:
            return [f for f in self._buf if f.purpose == purpose and f.ts > after_ts]

        try:
            async with asyncio.timeout(timeout_s):
                async with self._cond:
                    await self._cond.wait_for(lambda: len(ready()) >= count)
        except TimeoutError:
            return []
        return ready()[:count]
