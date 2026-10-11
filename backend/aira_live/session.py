"""One phone connection: who is talking, in which language, and how to send them server messages."""
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from pydantic import BaseModel

from aira_live.contracts import Burst, RequestFrameMsg
from aira_live.frames import Frame, FrameStore
from aira_live.i18n.detect import follow_lang

SendFn = Callable[[BaseModel], Awaitable[None]]


class Session:
    def __init__(self, uid: str, shop_id: str, lang: str, verbosity: str, send: SendFn,
                 session_id: str | None = None):
        # ``session_id`` is given only when a hello resumes a session that just ended (sessions.take_ended).
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.uid, self.shop_id, self.lang, self.verbosity = uid, shop_id, lang, verbosity
        self.lang_pending: str | None = None  # a language heard in the last utterance but not yet adopted
        self.frames = FrameStore()
        self.tool_results: list[dict] = []
        self._send = send

    async def send(self, msg: BaseModel) -> None:
        await self._send(msg)

    def follow_lang(self, text: str) -> str:
        """Update ``lang`` from one user utterance (sticky: see ``i18n.follow_lang``) and return it."""
        self.lang, self.lang_pending = follow_lang(text, self.lang, self.lang_pending)
        return self.lang

    async def request_frames(self, purpose: str, count: int = 1, interval_ms: int = 0,
                             timeout_s: float = 4.0) -> list[Frame]:
        """Ask the phone for ``count`` fresh frames and wait for them ([] on timeout).

        "Fresh" means newer than the newest frame already stored for ``purpose``. Frame timestamps come from
        the phone's clock, so they are never compared with the server's clock.
        """
        since = self.frames.last_ts(purpose)
        burst = Burst(count=count, interval_ms=interval_ms) if count > 1 else None
        await self.send(RequestFrameMsg(purpose=purpose, burst=burst))
        return await self.frames.wait_for(count=count, purpose=purpose, after_ts=since, timeout_s=timeout_s)


current_session: ContextVar[Session] = ContextVar("current_session")
# Id of the Live tool call running now ("" outside a tool call); set by tool_runtime.wrap_tool.
current_tool_call_id: ContextVar[str] = ContextVar("current_tool_call_id", default="")


def get_session() -> Session:
    return current_session.get()
