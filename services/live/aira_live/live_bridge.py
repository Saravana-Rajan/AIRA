"""Gemini Live bridge: phone audio <-> Live model.

Covers transcripts, barge-in, the long-press hard stop, reconnect with session resumption, the soundbox
transcript hook (Kanish's UPI check) and the turn-end number safety check (spec §6).

``LiveBridge`` talks to a pluggable ``LiveBackend``: ``AdkLiveBackend`` (Google ADK ``run_live`` on the
google-genai Live API, model ``model_for("live")``) in production, an in-memory fake in tests.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Literal, Protocol

from aira_live import safety
from aira_live.contracts import AudioOutMsg, CueMsg, ErrorMsg, TranscriptMsg
from aira_live.i18n import t

if TYPE_CHECKING:
    from aira_live.session import Session

log = logging.getLogger(__name__)

NOTICE_PREFIX = "[NOTICE FOR THE SHOPKEEPER]"

EventKind = Literal["audio", "input_transcript", "output_transcript", "interrupted", "turn_complete",
                    "resumption_handle", "go_away"]


@dataclass
class LiveEvent:
    kind: EventKind
    audio: bytes = b""
    text: str = ""
    final: bool = False
    handle: str | None = None
    # turn_complete only: the model is still answering the same prompt in another turn
    # (Live ``interaction_status`` IN_PROGRESS), so the answer is not over yet.
    more: bool = False


class LiveBackend(Protocol):
    async def start(self, system_prompt: str, tools: list, resume_handle: str | None) -> None: ...
    async def send_audio(self, pcm16: bytes) -> None: ...
    async def send_text(self, text: str) -> None: ...
    def events(self) -> AsyncIterator[LiveEvent]: ...
    async def close(self) -> None: ...


class LiveConnectionLost(Exception):
    pass


TranscriptListener = Callable[[str], Awaitable[None]]
_listeners: dict[str, list[TranscriptListener]] = {}


def on_input_transcript(session_id: str, callback: TranscriptListener) -> Callable[[], None]:
    """Subscribe to every complete utterance Live hears in this session: owner, customer or the UPI soundbox.

    Used by Kanish's ``sale_pay`` (UPI) together with ``business.soundbox.parse``.
    - The callback gets the full text of each finished input transcript.
    - It runs as a background task, so it never blocks the audio. Keep it quick and idempotent.
    - Listeners survive Live reconnects and are dropped when the session's bridge closes.
    Returns ``unsubscribe()``.
    """
    _listeners.setdefault(session_id, []).append(callback)

    def unsubscribe() -> None:
        cbs = _listeners.get(session_id)
        if cbs and callback in cbs:
            cbs.remove(callback)
            if not cbs:
                _listeners.pop(session_id, None)

    return unsubscribe


async def _aclose(it: object) -> None:
    aclose = getattr(it, "aclose", None)
    if aclose is not None:
        with suppress(Exception):
            await aclose()


class LiveBridge:
    MAX_RECONNECTS = 5
    BACKOFF_BASE_S = 0.5
    BACKOFF_MAX_S = 8.0
    RECENT_RESULT_TURNS = 2   # earlier turns whose tool results still back what AIRA says now
    MAX_PENDING_TEXT = 20

    def __init__(self, session: Session, backend_factory: Callable[[], LiveBackend], system_prompt: str,
                 tools: list):
        self.session = session
        self._factory = backend_factory
        self.system_prompt = system_prompt
        self.tools = tools
        self._backend: LiveBackend | None = None
        self._handle: str | None = None
        self._connected = False
        self._closed = False
        self._muted = False
        self._task: asyncio.Task | None = None
        self._pending_text: list[str] = []
        self._in_partial = ""
        self._out_final: list[str] = []
        self._out_partial = ""
        self._recent_results: deque[list[dict]] = deque(maxlen=self.RECENT_RESULT_TURNS)
        self._listener_tasks: set[asyncio.Task] = set()

    # ---------- lifecycle ----------
    async def start(self) -> None:
        """Connect to Live and start pumping its events. Raises if the first connection fails."""
        await self._connect()
        self._task = asyncio.create_task(self._pump(), name=f"live-bridge-{self.session.session_id}")

    async def close(self) -> None:
        """Stop the pump, close the Live connection and drop this session's transcript listeners. Idempotent."""
        self._closed = True
        self._connected = False
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        if self._backend is not None:
            with suppress(Exception):
                await self._backend.close()
        _listeners.pop(self.session.session_id, None)

    async def _connect(self) -> None:
        self._connected = False
        backend = self._factory()
        self._backend = backend
        await backend.start(self.system_prompt, self.tools, self._handle)
        self._connected = True
        pending, self._pending_text = self._pending_text, []
        for text in pending:
            await self.send_text(text)

    # ---------- phone -> Live ----------
    async def send_audio(self, pcm16_b64: str) -> None:
        """Mic audio (16 kHz PCM16, base64). Dropped while reconnecting: realtime audio can't be replayed."""
        if self._closed or not self._connected:
            return
        try:
            pcm = base64.b64decode(pcm16_b64, validate=True)
        except (binascii.Error, ValueError):
            log.debug("dropping an undecodable audio chunk")
            return
        try:
            await self._backend.send_audio(pcm)
        except Exception as exc:  # the pump notices a dead connection and reconnects
            log.debug("audio send failed: %s", exc)

    async def send_text(self, text: str) -> None:
        """Typed text, notices and corrections. Queued while reconnecting and sent once Live is back."""
        if self._closed:
            return
        if not self._connected:
            self._queue_text(text)
            return
        try:
            await self._backend.send_text(text)
        except Exception as exc:
            log.warning("text send failed, retrying after reconnect: %s", exc)
            self._queue_text(text)

    async def speak_notice(self, text: str) -> None:
        """Have AIRA speak a vendor reply or an alert in the live audio (used by ``sessions.notify``)."""
        await self.send_text(f"{NOTICE_PREFIX} Tell the shopkeeper briefly, in his language: {text}")

    def hard_stop(self) -> None:
        """Long-press: drop the rest of the current answer (audio + captions) until the next question starts."""
        self._muted = True

    def _queue_text(self, text: str) -> None:
        self._pending_text.append(text)
        del self._pending_text[:-self.MAX_PENDING_TEXT]

    # ---------- Live -> phone ----------
    async def _pump(self) -> None:
        failures = 0
        while not self._closed:
            stream = self._backend.events()
            try:
                async for ev in stream:
                    failures = 0
                    await self._dispatch(ev)
                    if ev.kind == "go_away":
                        raise LiveConnectionLost("go_away")
                raise LiveConnectionLost("stream ended")
            except Exception as exc:  # CancelledError is a BaseException: close() stops the pump
                if self._closed:
                    return
                log.warning("live connection lost (session %s): %s", self.session.session_id, exc)
            finally:
                await _aclose(stream)
            self._connected = False
            # A half-heard utterance may be cut short ("rupees 6" of "rupees 60"): never hand it to listeners.
            self._in_partial = self._out_partial = ""
            await self._send(CueMsg(cue="reconnect"))
            while True:
                failures += 1
                if failures > self.MAX_RECONNECTS:
                    await self._send(ErrorMsg(code="network", say=t("live.unavailable", self.session.lang)))
                    self._closed = True
                    with suppress(Exception):
                        await self._backend.close()
                    return
                await asyncio.sleep(min(self.BACKOFF_MAX_S, self.BACKOFF_BASE_S * 2 ** (failures - 1)))
                if self._closed:
                    return
                with suppress(Exception):
                    await self._backend.close()
                try:
                    await self._connect()
                    break
                except Exception as exc:
                    log.warning("live reconnect %d failed: %s", failures, exc)

    async def _dispatch(self, ev: LiveEvent) -> None:
        """Handle one event. A failure here (closed socket, bad listener, ...) never drops the Live connection."""
        try:
            await self._handle_event(ev)
        except Exception:
            log.exception("live event %r failed", ev.kind)

    async def _send(self, msg) -> None:
        try:
            await self.session.send(msg)
        except Exception as exc:
            log.debug("send to phone failed: %s", exc)

    async def _handle_event(self, ev: LiveEvent) -> None:
        s = self.session
        if ev.kind == "audio":
            if not self._muted and ev.audio:
                await s.send(AudioOutMsg(pcm24=base64.b64encode(ev.audio).decode()))
        elif ev.kind == "output_transcript":
            if self._muted:  # the owner stopped this answer: he never hears these words
                return
            # Live streams fragments, then (usually) the whole sentence with final=True.
            if ev.final:
                self._out_final.append(ev.text)
                self._out_partial = ""
            else:
                self._out_partial += ev.text
            if ev.text:
                await s.send(TranscriptMsg(role="aira", text=ev.text, final=ev.final))
        elif ev.kind == "input_transcript":
            self._muted = False  # a new question has started
            if ev.final:
                utterance, self._in_partial = ev.text or self._in_partial, ""
                self._notify_listeners(utterance)
            else:
                self._in_partial += ev.text
            if ev.text:
                await s.send(TranscriptMsg(role="user", text=ev.text, final=ev.final))
        elif ev.kind == "interrupted":
            await s.send(CueMsg(cue="interrupted"))
        elif ev.kind == "turn_complete":
            if self._in_partial:  # the backend never marked the utterance final
                utterance, self._in_partial = self._in_partial, ""
                self._notify_listeners(utterance)
            if ev.more:  # the same answer goes on in another model turn
                return
            self._muted = False
            await self._end_turn()
        elif ev.kind == "resumption_handle":
            if ev.handle:
                self._handle = ev.handle
        # "go_away" is handled by _pump (reconnect with the last handle).

    def _notify_listeners(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        for cb in list(_listeners.get(self.session.session_id, ())):
            task = asyncio.create_task(self._run_listener(cb, text))
            self._listener_tasks.add(task)
            task.add_done_callback(self._listener_tasks.discard)

    @staticmethod
    async def _run_listener(cb: TranscriptListener, text: str) -> None:
        try:
            await cb(text)
        except Exception:
            log.exception("input-transcript listener failed")

    # ---------- turn end: number safety check ----------
    async def _end_turn(self) -> None:
        spoken = " ".join([*self._out_final, self._out_partial]).strip()
        self._out_final, self._out_partial = [], ""
        current = self._take_tool_results()
        if spoken and current:  # only turns where a tool ran; prices may also come from the catalog prompt
            recent = [r for turn in self._recent_results for r in turn]
            await self._safety_check(spoken, current + recent)
        if current:
            self._recent_results.append(current)

    def _take_tool_results(self) -> list[dict]:
        """This turn's tool returns (recorded by ``tool_runtime.wrap_tool``); clears them for the next turn."""
        entries = getattr(self.session, "tool_results", None)
        if not entries:
            return []
        taken = [e.get("result", e) for e in entries if isinstance(e, dict)]
        entries.clear()
        return [r for r in taken if isinstance(r, dict)]

    async def _safety_check(self, spoken: str, results: list[dict]) -> None:
        try:
            verdict = safety.check_numbers(spoken, results)
            if verdict.ok and verdict.needs_llm:
                verdict = await safety.llm_verify(spoken, results)
            if verdict.ok:
                return
            log.warning("AIRA said numbers no tool returned: %s", verdict.unexpected)
            await self._send(CueMsg(cue="warn"))
            await self.send_text(safety.correction_text(verdict.unexpected, self.session.lang))
        except Exception:
            log.exception("safety check failed")


def _in_progress(status: object) -> bool:
    """True when a Live ``interaction_status`` says the model is still working on the prompt."""
    if status is None:
        return False
    return str(getattr(status, "value", status)).upper().endswith("IN_PROGRESS")


def translate(e) -> list[LiveEvent]:
    """ADK live ``Event`` -> LiveEvents (field names checked against google-adk 2.11 ``Event``)."""
    out: list[LiveEvent] = []
    if getattr(e, "interrupted", False):
        out.append(LiveEvent("interrupted"))
    it = getattr(e, "input_transcription", None)
    if it is not None and getattr(it, "text", None):
        out.append(LiveEvent("input_transcript", text=it.text, final=bool(getattr(it, "finished", False))))
    ot = getattr(e, "output_transcription", None)
    if ot is not None and getattr(ot, "text", None):
        out.append(LiveEvent("output_transcript", text=ot.text, final=bool(getattr(ot, "finished", False))))
    content = getattr(e, "content", None)
    for part in (getattr(content, "parts", None) or []):
        blob = getattr(part, "inline_data", None)
        if blob is not None and str(getattr(blob, "mime_type", "")).startswith("audio/pcm") and blob.data:
            out.append(LiveEvent("audio", audio=blob.data))
    upd = getattr(e, "live_session_resumption_update", None)
    if upd is not None and getattr(upd, "new_handle", None):
        out.append(LiveEvent("resumption_handle", handle=upd.new_handle))
    if getattr(e, "go_away", None):
        out.append(LiveEvent("go_away"))
    if getattr(e, "turn_complete", False):
        out.append(LiveEvent("turn_complete", more=_in_progress(getattr(e, "interaction_status", None))))
    return out


class AdkLiveBackend:
    """Production backend: Google ADK ``run_live`` + ``LiveRequestQueue`` on ``model_for("live")``.

    ADK already resumes on GoAway or a dropped socket while it holds a resumption handle. LiveBridge's own
    reconnect covers the cases ADK gives up on.
    """

    APP_NAME = "aira-live"
    CLOSE_TIMEOUT_S = 5.0

    def __init__(self, session: Session):
        self._s = session
        self._queue = None
        self._gen = None

    async def start(self, system_prompt: str, tools: list, resume_handle: str | None) -> None:
        from google.adk.agents import Agent, LiveRequestQueue
        from google.adk.agents.run_config import RunConfig, StreamingMode
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        from aira_live.models import model_for

        # A provider (not a str) so ADK never treats {braces} in shop data as session-state templates.
        agent = Agent(name="aira", model=model_for("live"), instruction=lambda _ctx: system_prompt,
                      tools=list(tools))
        runner = InMemoryRunner(agent=agent, app_name=self.APP_NAME)
        adk_session = await runner.session_service.create_session(app_name=self.APP_NAME, user_id=self._s.uid)
        self._queue = LiveRequestQueue()
        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(handle=resume_handle),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=100_000, sliding_window=types.SlidingWindow(target_tokens=60_000)),
        )
        self._gen = runner.run_live(user_id=self._s.uid, session_id=adk_session.id,
                                    live_request_queue=self._queue, run_config=run_config)

    async def send_audio(self, pcm16: bytes) -> None:
        from google.genai import types
        self._queue.send_realtime(types.Blob(data=pcm16, mime_type="audio/pcm;rate=16000"))

    async def send_text(self, text: str) -> None:
        from google.genai import types
        self._queue.send_content(types.Content(role="user", parts=[types.Part(text=text)]))

    async def events(self):
        async for e in self._gen:
            if getattr(e, "error_code", None):
                log.warning("live model error %s: %s", e.error_code, getattr(e, "error_message", ""))
            for le in translate(e):
                yield le

    async def close(self) -> None:
        if self._queue is not None:
            self._queue.close()
        gen, self._gen = self._gen, None
        if gen is not None:
            with suppress(Exception):
                await asyncio.wait_for(gen.aclose(), self.CLOSE_TIMEOUT_S)
