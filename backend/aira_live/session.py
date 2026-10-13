"""One phone connection: who is talking, in which language, and how to send them server messages."""
import asyncio
import re
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel

from aira_live.contracts import Burst, RequestFrameMsg
from aira_live.diag import diag
from aira_live.frames import EXPIRED_KEEP_S, Frame, FrameStore
from aira_live.i18n.detect import LangStep, judge_lang

SendFn = Callable[[BaseModel], Awaitable[None]]

# The same notice (vendor reply, alert, guidance verdict) reaching one session twice within this many seconds
# is spoken once. Loops (guidance, delivery counts, jobs) may fire the same line again; the owner hears it once.
NOTICE_DEDUPE_S = 10.0
# A camera look may reuse a frame that arrived less than FRAME_REUSE_S ago (``request_frames(reuse_s=...)``)
# when it is at least REUSE_MIN_SIDE px on its long side (ambient / describe frames are 640 px).
FRAME_REUSE_S = 1.0
REUSE_MIN_SIDE = 640
# Frames that reach the server after a request's wait ended (up to LATE_FRAME_WINDOW_S later) are logged as
# ``late_frame``: a slow phone and a slow uplink look the same from the tool, and this says by how much it missed.
LATE_FRAME_WINDOW_S = EXPIRED_KEEP_S
_monotonic = time.monotonic  # tests swap in a clock
_WS = re.compile(r"\s+")


def notice_key(text: str) -> str:
    """The identity of a notice: trimmed, case-folded, punctuation and symbols dropped, spaces collapsed."""
    chars = (" " if unicodedata.category(c)[0] in "PS" else c for c in text.casefold())
    return _WS.sub(" ", "".join(chars)).strip()


class Session:
    def __init__(self, uid: str, shop_id: str, lang: str, verbosity: str, send: SendFn,
                 session_id: str | None = None, languages: list[str] | None = None):
        # ``session_id`` is given only when a hello resumes a session that just ended (sessions.take_ended).
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.uid, self.shop_id, self.lang, self.verbosity = uid, shop_id, lang, verbosity
        self.lang_pending: str | None = None  # a language heard in the last utterance but not yet adopted
        # The languages this shop usually speaks (i18n.expected); None means any language switches as before.
        self.languages: list[str] | None = languages
        self.lang_streak = 0  # consecutive full sentences in a language outside ``languages``
        self.frames = FrameStore()
        self.tool_results: list[dict] = []
        # Sent-notice ledger: notice key -> when it was last sent. ``sessions.register`` hands a resumed session
        # the ledger of the session it continues, so a reconnect never replays a notice the owner already heard.
        self.notice_ledger: dict[str, float] = {}
        # Owner questions to the camera in flight (camera_ask, counts). While > 0 the ambient and describe loops
        # make no ER 2 call: the owner's own question gets the camera and the model first.
        self.owner_asks = 0
        self._send = send
        self._late_watches: set[asyncio.Task] = set()  # kept for callers/tests: late frames are logged inline now

    @property
    def owner_busy(self) -> bool:
        return self.owner_asks > 0

    @contextmanager
    def owner_asking(self):
        """Mark an owner camera question in flight (nestable); background vision waits until it ends."""
        self.owner_asks += 1
        try:
            yield
        finally:
            self.owner_asks -= 1

    async def send(self, msg: BaseModel) -> None:
        await self._send(msg)

    def notice_is_repeat(self, text: str, window_s: float = NOTICE_DEDUPE_S) -> bool:
        """True when ``text`` (or the same words) was already sent to this session within ``window_s``.

        Otherwise records it as sent now. An empty notice is always a repeat: there is nothing to say.
        """
        key = notice_key(text)
        if not key:
            return True
        now = _monotonic()
        for stale in [k for k, at in self.notice_ledger.items() if now - at > window_s]:
            del self.notice_ledger[stale]
        last = self.notice_ledger.get(key)
        if last is not None and now - last <= window_s:
            return True
        self.notice_ledger[key] = now
        return False

    def judge(self, text: str, typed: bool = False) -> LangStep:
        """Update ``lang`` from one user utterance (sticky, guided by ``languages``: see ``i18n.judge_lang``)
        and return the whole judgement, including whether the transcript looked misheard (``unclear``).
        Typed text cannot be misheard, so it follows any language, whatever the shop usually speaks."""
        expected = None if typed else self.languages
        step = judge_lang(text, self.lang, self.lang_pending, expected=expected, streak=self.lang_streak)
        self.lang, self.lang_pending, self.lang_streak = step.lang, step.pending, step.streak
        return step

    def follow_lang(self, text: str, typed: bool = False) -> str:
        """Update ``lang`` from one user utterance and return it."""
        return self.judge(text, typed=typed).lang

    def latest_frame(self, max_age_s: float = 1.0) -> Frame | None:
        """The newest frame of any purpose but the preview (the 1 fps live video included) that arrived at most
        ``max_age_s`` ago, or None (``FrameStore.recent``; ``look_frame`` reuses the same frames)."""
        return self.frames.recent(max_age_s)

    @property
    def walking(self) -> bool:
        """A walk-with-me guidance runs for this session (tools.walk): it owns the voice and the camera."""
        try:
            from aira_live.tools import walk
        except ImportError:
            return False
        return self.session_id in walk._ACTIVE

    def cached_count(self, product: str | None = None) -> tuple[int, float, float] | None:
        """(count, confidence, age_s) of the background pre-count of the current scene (vision.precount)."""
        from aira_live.vision import precount
        return precount.cached(self.session_id, product)

    @property
    def scene_facts(self):
        """What AIRA last counted in this session, with age and evidence (``aira_live.scene_facts``); created on
        first use."""
        sf = self.__dict__.get("_scene_facts")
        if sf is None:
            from aira_live.scene_facts import SceneFacts
            sf = self.__dict__["_scene_facts"] = SceneFacts()
        return sf

    async def request_frames(self, purpose: str, count: int = 1, interval_ms: int = 0,
                             timeout_s: float = 4.0, partial: bool = False) -> list[Frame]:
        """Ask the phone for ``count`` fresh frames and wait for them ([] on timeout; with ``partial`` the
        frames of THIS request that did arrive, possibly fewer than ``count``, never another request's).

        Each call has its own request id: the phone echoes it on the frames it captures for this request, and
        only those frames satisfy it (``FrameStore.open_request``), so a late frame of an earlier timed-out
        request, or a concurrent same-purpose request (background pre-count vs the owner's count), never hands
        this one someone else's picture. A phone that predates request ids gets frames of ``purpose`` that
        arrived after this request opened, each frame going to one request only. Frames older than the
        purpose's capture-age limit (``frames.MAX_CAPTURE_AGE_MS``) are refused. Phone timestamps are never
        compared with the server's clock. ``ttl_ms`` tells the phone not to start a capture after we stopped
        waiting (measured from when the phone received the request).
        """
        rid = self.frames.open_request(purpose, count)
        burst = Burst(count=count, interval_ms=interval_ms) if count > 1 else None
        started = time.perf_counter()
        frames: list[Frame] = []
        try:
            diag("request_frame", session=self.session_id, purpose=purpose, count=count, interval_ms=interval_ms,
                 timeout_s=timeout_s, request_id=rid)
            await self.send(RequestFrameMsg(purpose=purpose, burst=burst, request_id=rid,
                                            ttl_ms=max(1, round(timeout_s * 1000))))
            frames = await self.frames.wait_request(rid, timeout_s)
            if partial and len(frames) < count:
                frames = self.frames.own_frames(rid)
        finally:
            got = self.frames.got(rid)
            on_late = (self._late_logger(purpose, rid, count, started, timeout_s)
                       if len(frames) < count else None)
            self.frames.close_request(rid, on_late=on_late)
        diag("frames_got", session=self.session_id, purpose=purpose, wanted=count, got=len(frames),
             request_id=rid, waited_ms=round((time.perf_counter() - started) * 1000),
             bytes=[len(f.jpeg_b64) for f in frames], sizes=[[f.w, f.h] for f in frames],
             phone_age_ms=[round(f.phone_age_ms) for f in frames])
        diag("latency", stage="frame_wait", session=self.session_id, purpose=purpose, reused=False,
             ms=round((time.perf_counter() - started) * 1000), got=len(frames), wanted=count)
        if len(frames) < count and got:
            diag("frames_partial", session=self.session_id, purpose=purpose, request_id=rid, got=got, wanted=count)
        return frames

    def _late_logger(self, purpose: str, rid: str, wanted: int, started: float, timeout_s: float):
        """After a request's wait ran out, the first frame that still arrives for it (its id, or for an old
        phone its purpose, within ``frames.EXPIRED_KEEP_S``) is logged as ``late_frame`` (how late, how big):
        it satisfies nothing, but is never dropped silently. Nothing waits on this."""
        def late(f: Frame, got_in_time: int) -> None:
            waited_ms = round((time.perf_counter() - started) * 1000)
            diag("late_frame", severity="WARNING", session=self.session_id, purpose=purpose, request_id=rid,
                 wanted=wanted, got_in_time=got_in_time, waited_ms=waited_ms,
                 late_ms=max(0, waited_ms - round(timeout_s * 1000)), bytes=len(f.jpeg_b64), size=[f.w, f.h])
        return late

    async def look_frame(self, purpose: str, timeout_s: float = 4.0, max_age_s: float = FRAME_REUSE_S,
                         min_side: int = REUSE_MIN_SIDE) -> list[Frame]:
        """One frame for a single-frame look (camera_ask "look"/"read", identify, free-text find): a frame of any
        purpose whose capture age (the phone's own grab -> send time plus time since it reached the server, see
        ``FrameStore.recent``) is under ``max_age_s`` and is at least ``min_side`` px on its long side is used at once; otherwise one fresh frame is requested (``request_frames``). Never for a count:
        a count needs its own fresh, sharp count frame."""
        hit = self.frames.recent(max_age_s, min_side=min_side)
        if hit is not None:
            diag("latency", stage="frame_wait", session=self.session_id, purpose=purpose, reused=True,
                 ms=0, frame_purpose=hit.purpose, size=[hit.w, hit.h])
            return [hit]
        return await self.request_frames(purpose, count=1, timeout_s=timeout_s)


current_session: ContextVar[Session] = ContextVar("current_session")
# Id of the Live tool call running now ("" outside a tool call); set by tool_runtime.wrap_tool.
current_tool_call_id: ContextVar[str] = ContextVar("current_tool_call_id", default="")


def get_session() -> Session:
    return current_session.get()
