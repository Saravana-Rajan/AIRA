"""One phone connection: who is talking, in which language, and how to send them server messages."""
import re
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from pydantic import BaseModel

from aira_live.contracts import Burst, RequestFrameMsg
from aira_live.diag import diag
from aira_live.frames import Frame, FrameStore
from aira_live.i18n.detect import LangStep, judge_lang

SendFn = Callable[[BaseModel], Awaitable[None]]

# The same notice (vendor reply, alert, guidance verdict) reaching one session twice within this many seconds
# is spoken once. Loops (guidance, delivery counts, jobs) may fire the same line again; the owner hears it once.
NOTICE_DEDUPE_S = 10.0
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
        self._send = send

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

    async def request_frames(self, purpose: str, count: int = 1, interval_ms: int = 0,
                             timeout_s: float = 4.0) -> list[Frame]:
        """Ask the phone for ``count`` fresh frames and wait for them ([] on timeout).

        "Fresh" means newer than the newest frame already stored for ``purpose``. Frame timestamps come from
        the phone's clock, so they are never compared with the server's clock.
        """
        since = self.frames.last_ts(purpose)
        burst = Burst(count=count, interval_ms=interval_ms) if count > 1 else None
        started = time.perf_counter()
        diag("request_frame", session=self.session_id, purpose=purpose, count=count, interval_ms=interval_ms,
             timeout_s=timeout_s, since_ts=since)
        await self.send(RequestFrameMsg(purpose=purpose, burst=burst))
        frames = await self.frames.wait_for(count=count, purpose=purpose, after_ts=since, timeout_s=timeout_s)
        diag("frames_got", session=self.session_id, purpose=purpose, wanted=count, got=len(frames),
             waited_ms=round((time.perf_counter() - started) * 1000), bytes=[len(f.jpeg_b64) for f in frames],
             sizes=[[f.w, f.h] for f in frames])
        return frames


current_session: ContextVar[Session] = ContextVar("current_session")
# Id of the Live tool call running now ("" outside a tool call); set by tool_runtime.wrap_tool.
current_tool_call_id: ContextVar[str] = ContextVar("current_tool_call_id", default="")


def get_session() -> Session:
    return current_session.get()
