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
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Literal, Protocol

from aira_live import monitor, safety
from aira_live.contracts import AudioOutMsg, CueMsg, ErrorMsg, TranscriptMsg
from aira_live.diag import diag, trunc
from aira_live.i18n import t
from aira_live.i18n.detect import detect_lang, latin_lang
from aira_live.prompt import LANG_NAME, last_catalog_facts, last_vocabulary, unclear_note
from aira_live.session import NOTICE_DEDUPE_S, notice_key

if TYPE_CHECKING:
    from aira_live.session import Session

log = logging.getLogger(__name__)

# A notice (vendor reply, alert, ambient line) goes to Live as a system note to say word for word; the server has
# already captioned it, so the model's re-speak is heard but not captioned a second time (``_echo_of``).
NOTICE_PREFIX = "[SYSTEM NOTE] Say exactly, word for word"
NOTICE_ECHO_S = 20.0      # a notice waits this long for the model's re-speak before it is forgotten
# Reply-language lock: an answer outside the session language and the shop's languages is corrected at once,
# and its caption is hidden; one correction per LANG_LOCK_GAP_S so a wordy model never gets a flood of notes.
LANG_LOCK_GAP_S = 5.0
CAPTION_HOLD_WORDS = 4    # aira partial captions are held until this many words can be judged
_monotonic = time.monotonic  # tests swap in a clock

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
    # input_transcript only: the diarization speaker label, when the SDK gives one ("speaker_1", ...).
    speaker: str | None = None


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
                 tools: list, resume_handle: str | None = None):
        self.session = session
        self._factory = backend_factory
        self.system_prompt = system_prompt
        self.tools = tools
        self._backend: LiveBackend | None = None
        # The Live resumption handle: updated by Live while connected, seeded from the previous bridge when a
        # phone reconnects with hello.resume (main.py), so the conversation continues instead of restarting.
        self._handle: str | None = resume_handle
        self._connected = False
        self._closed = False
        self._muted = False
        self._answering = False  # Live is speaking an answer that has not ended (turn_complete / interrupted)
        self._task: asyncio.Task | None = None
        self._pending_text: list[tuple[str, float, bool]] = []  # (text, queued at, is a notice)
        self._notices: dict[str, float] = {}  # notice key -> when AIRA was last told to say it
        self._in_partial = ""
        self._out_final: list[str] = []
        self._out_partial = ""
        # Captions of the answer being spoken: "hold" (a few words are not judged yet, or a notice echo is
        # pending), "stream" (judged fine: partials go out as they come) or "hide" (wrong language, or the
        # re-speak of a notice the server already captioned).
        self._caption: Literal["hold", "stream", "hide"] = "hold"
        self._echo: deque[tuple[str, float]] = deque()  # notices told to Live, awaiting the model's re-speak
        self._echo_hit = False  # a pending notice was recognised in the turn being spoken
        self._last_lock_at = float("-inf")
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
        diag("live", event="connected", session=self.session.session_id, resumed=bool(self._handle),
             tools=len(self.tools), pending_text=len(self._pending_text))
        pending, self._pending_text = self._pending_text, []
        now = _monotonic()
        for text, at, notice in pending:
            if notice and now - at > NOTICE_DEDUPE_S:
                continue  # a stale notice is not worth repeating after the reconnect
            await self.send_text(text, _notice=notice)

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

    async def send_text(self, text: str, _notice: bool = False) -> None:
        """Typed text, notices and corrections. Queued while reconnecting and sent once Live is back."""
        if self._closed:
            return
        if self._muted and not self._answering:
            # A long-press with nothing playing (e.g. to stop guidance) must not swallow the answer to this new
            # prompt. If the stopped answer is still streaming, the mute lifts when Live cuts or ends it.
            self._muted = False
        diag("live_text", session=self.session.session_id, text=trunc(text), notice=_notice,
             queued=not self._connected)
        if not self._connected:
            self._queue_text(text, _notice)
            return
        try:
            await self._backend.send_text(text)
        except Exception as exc:
            log.warning("text send failed, retrying after reconnect: %s", exc)
            self._queue_text(text, _notice)

    async def speak_notice(self, text: str) -> None:
        """Have AIRA speak a vendor reply or an alert in the live audio (used by ``sessions.notify``).

        The text is a fixed line (English for a language without a catalog): Live says it in the language the
        shopkeeper last spoke, so a notice is never dropped for want of a translation. The same notice within
        ``NOTICE_DEDUPE_S`` seconds is told once: AIRA never says one line twice because a loop raised it twice.
        """
        key = notice_key(text)
        if not key:
            return
        now = _monotonic()
        for stale in [k for k, at in self._notices.items() if now - at > NOTICE_DEDUPE_S]:
            del self._notices[stale]
        last = self._notices.get(key)
        if last is not None and now - last <= NOTICE_DEDUPE_S:
            diag("notice", session=self.session.session_id, text=trunc(text), deduped=True)
            return
        self._notices[key] = now
        diag("notice", session=self.session.session_id, text=trunc(text), deduped=False)
        self._echo.append((key, now))
        self._caption = "hold"  # the re-speak is captioned by the server already: hold until it is recognised
        await self.send_text(f"{NOTICE_PREFIX}, in {self._lang_name()}: «{text}»", _notice=True)

    def _lang_name(self) -> str:
        return LANG_NAME.get(str(self.session.lang or "en"), "English")

    def hard_stop(self) -> None:
        """Long-press: drop the rest of the current answer (audio + captions).

        The mute lifts when the next question starts (the owner speaks, or a new text prompt arrives while nothing
        is playing), when Live cuts or ends the stopped answer, or on a reconnect.
        """
        self._muted = True

    def _queue_text(self, text: str, notice: bool = False) -> None:
        self._pending_text.append((text, _monotonic(), notice))
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
                diag("live", severity="WARNING", event="lost", session=self.session.session_id,
                     error=type(exc).__name__, message=trunc(str(exc), 200))
            finally:
                await _aclose(stream)
            self._connected = False
            # A half-heard utterance may be cut short ("rupees 6" of "rupees 60"): never hand it to listeners.
            self._in_partial = self._out_partial = ""
            # A stopped answer died with this connection; what Live says after the reconnect is new.
            self._muted = self._answering = False
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
            self._answering = True
            if not self._muted and ev.audio:
                await s.send(AudioOutMsg(pcm24=base64.b64encode(ev.audio).decode()))
        elif ev.kind == "output_transcript":
            self._answering = True
            if self._muted:  # the owner stopped this answer: he never hears these words
                return
            await self._output_caption(ev)
        elif ev.kind == "input_transcript":
            self._muted = False  # a new question has started
            lang = unclear = None
            if ev.final:
                utterance, self._in_partial = ev.text or self._in_partial, ""
                # AIRA follows the speaker: the catalog and the phone's labels switch with him, but only when he
                # clearly speaks another language (sticky: "Amo" in Latin letters is still Tamil).
                lang, unclear = await self._judge(utterance)
                if ev.speaker:  # diarization: the monitor tells the owner from a customer by voice
                    monitor.note_speaker(s.session_id, ev.speaker)
                self._notify_listeners(utterance)
                diag("transcript", session=s.session_id, role="user", text=trunc(utterance), final=True,
                     lang=lang, unclear=unclear, speaker=ev.speaker)
            else:
                self._in_partial += ev.text
            if ev.text:
                await s.send(TranscriptMsg(role="user", text=ev.text, final=ev.final, lang=lang, unclear=unclear))
        elif ev.kind == "interrupted":
            # Live cut the answer (barge-in or a new prompt): the stopped answer is over, the next one is new.
            self._muted = self._answering = False
            self._caption, self._out_partial = "hold", ""
            diag("live", event="interrupted", session=s.session_id)
            await s.send(CueMsg(cue="interrupted"))
        elif ev.kind == "turn_complete":
            if self._in_partial:  # the backend never marked the utterance final
                utterance, self._in_partial = self._in_partial, ""
                step_lang, step_unclear = await self._judge(utterance)
                self._notify_listeners(utterance)
                diag("transcript", session=s.session_id, role="user", text=trunc(utterance), final=True,
                     lang=step_lang, unclear=step_unclear, source="turn_end")
            diag("live", event="turn_complete", session=s.session_id, more=ev.more, muted=self._muted,
                 spoken_parts=len(self._out_final) + (1 if self._out_partial else 0))
            if ev.more:  # the same answer goes on in another model turn
                return
            self._muted = self._answering = False
            self._caption = "hold"
            if self._echo_hit:  # the notice was said in this turn: nothing later is its echo
                self._echo.clear()
                self._echo_hit = False
            monitor.count_turn(s.session_id)
            await self._end_turn()
        elif ev.kind == "resumption_handle":
            if ev.handle:
                self._handle = ev.handle
        # "go_away" is handled by _pump (reconnect with the last handle).

    # ---------- aira captions: one line per notice, reply-language lock ----------
    async def _output_caption(self, ev: LiveEvent) -> None:
        """Live streams fragments, then the whole sentence with final=True. Captions are held until a few words
        can be judged (or until the final while a notice echo is pending), hidden for an answer in the wrong
        language (with a correction to Live) or for the re-speak of a notice the server captioned already, and
        streamed otherwise."""
        s = self.session
        if ev.final:
            text = ev.text or self._out_partial
            self._out_final.append(ev.text)
            self._out_partial = ""
            diag("transcript", session=s.session_id, role="aira", text=trunc(ev.text), final=True)
            mode = self._caption
            self._caption = "hold"  # the next sentence of this turn is judged afresh
            if mode == "hide" or not text:
                return
            if await self._lang_lock(text):
                self._caption = "hide"
                return
            if self._echo_of(text) is not None:
                diag("notice_echo", session=s.session_id, text=trunc(text))
                return
            await s.send(TranscriptMsg(role="aira", text=text, final=True))
            return
        self._out_partial += ev.text
        if self._caption == "hide" or not ev.text:
            return
        if self._caption == "stream":
            await s.send(TranscriptMsg(role="aira", text=ev.text, final=False))
            return
        if self._echo_pending() or len(self._out_partial.split()) < CAPTION_HOLD_WORDS:
            return  # keep holding: a notice echo may be coming, or too few words to judge
        if await self._lang_lock(self._out_partial):
            self._caption = "hide"
            return
        self._caption = "stream"
        await s.send(TranscriptMsg(role="aira", text=self._out_partial, final=False))

    def _echo_pending(self) -> bool:
        now = _monotonic()
        while self._echo and now - self._echo[0][1] > NOTICE_ECHO_S:
            self._echo.popleft()
        return bool(self._echo)

    def _echo_of(self, text: str) -> str | None:
        """The pending notice ``text`` is the model's re-speak of, or None. The model may split a notice into
        sentences or reword it a little, so a sentence contained in the notice or sharing most of its words
        counts. Matched notices stay pending until the turn ends or they expire."""
        if not self._echo_pending():
            return None
        key = notice_key(text)
        words = set(key.split())
        if not words:
            return None
        for notice, _at in self._echo:
            n_words = set(notice.split())
            shared = len(words & n_words)
            if key == notice or key in notice or notice in key or \
                    (shared >= 2 and shared / max(1, min(len(words), len(n_words))) >= 0.5):
                self._echo_hit = True  # the notice was said: forgotten once this turn ends
                return notice
        return None

    def _reply_lang(self, text: str) -> str:
        lang = detect_lang(text, str(self.session.lang or "en"))
        if lang == "en":
            lang = latin_lang(text) or "en"
        return lang

    async def _lang_lock(self, text: str) -> bool:
        """True when ``text`` is in a language that is neither the session's nor one of the shop's (English
        always passes: product names and mixed sentences are normal). Sends the correction, at most one per
        ``LANG_LOCK_GAP_S``, and records it."""
        s = self.session
        heard = self._reply_lang(text)
        expected = str(s.lang or "en")
        allowed = {expected, "en", *[str(c).lower().split("-")[0] for c in (getattr(s, "languages", None) or ())]}
        if heard in allowed:
            return False
        now = _monotonic()
        diag("lang_lock", severity="WARNING", session=s.session_id, heard=heard, expected=expected,
             text=trunc(text), corrected=now - self._last_lock_at > LANG_LOCK_GAP_S)
        monitor.event(s, "lang_lock", heard=heard, expected=expected)
        if now - self._last_lock_at > LANG_LOCK_GAP_S:
            self._last_lock_at = now
            name = self._lang_name()
            await self.send_text(f"[SYSTEM NOTE] Reply only in {name}; the owner speaks {name}.")
        return True

    async def _judge(self, utterance: str) -> tuple[str, bool | None]:
        """Follow the speaker's language. A transcript in a language the shop does not usually speak (shop doc
        ``languages``) is probably misheard: the session language stays, the model gets a note telling it to
        ask for a repeat instead of answering, and the transcript is flagged ``unclear`` for the caption."""
        step = self.session.judge(utterance)
        if not step.unclear:
            return step.lang, None
        log.info("transcript looks like %s, outside the shop's languages; asking to repeat", step.heard)
        await self.send_text(unclear_note(step.heard or "", step.lang))
        return step.lang, True

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
            facts = last_catalog_facts(getattr(self.session, "shop_id", ""))  # PRODUCTS list numbers in the prompt
            await self._safety_check(spoken, current + recent + ([facts] if facts else []))
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
        speaker = getattr(it, "speaker_label", None)  # diarization, when the SDK (and ADK) pass it on
        out.append(LiveEvent("input_transcript", text=it.text, final=bool(getattr(it, "finished", False)),
                             speaker=str(speaker) if speaker else None))
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


# Base language codes -> the BCP-47 tags the transcription expects (Indian variants for the shop's languages).
TRANSCRIPTION_LANG = {
    "ta": "ta-IN", "en": "en-IN", "hi": "hi-IN", "te": "te-IN", "kn": "kn-IN", "ml": "ml-IN", "bn": "bn-IN",
    "gu": "gu-IN", "pa": "pa-IN", "or": "or-IN", "mr": "mr-IN",
}


def transcription_language_codes(languages) -> list[str]:
    out: list[str] = []
    for code in languages or ():
        base = str(code or "").strip().lower().split("-")[0]
        tag = TRANSCRIPTION_LANG.get(base, str(code).strip())
        if tag and tag not in out:
            out.append(tag)
    return out


def transcription_config(types, *, languages=(), vocabulary=(), diarization: bool = True):
    """``types.AudioTranscriptionConfig`` for the input audio: language hints (``language_codes``, or the older
    ``language_hints``), the catalog as ``custom_vocabulary`` / ``adaptation_phrases`` and ``diarization``.

    Every field is set only when the installed SDK's config has it (``model_fields``), so an older google-genai
    and the test stubs get a plain config. Checked against google-genai 2.29: ``language_codes: list[str]``,
    ``language_hints: LanguageHints`` (deprecated), ``custom_vocabulary: list[str]``,
    ``adaptation_phrases: list[str]``, ``diarization: bool``.
    """
    cls = types.AudioTranscriptionConfig
    fields = getattr(cls, "model_fields", None) or {}
    kw: dict = {}
    codes = transcription_language_codes(languages)
    if codes:
        if "language_codes" in fields:
            kw["language_codes"] = codes
        elif "language_hints" in fields and hasattr(types, "LanguageHints"):
            kw["language_hints"] = types.LanguageHints(language_codes=codes)
    vocab = [str(v) for v in vocabulary if v]
    if vocab:
        if "custom_vocabulary" in fields:
            kw["custom_vocabulary"] = list(vocab)
        if "adaptation_phrases" in fields:
            kw["adaptation_phrases"] = list(vocab)
    if diarization and "diarization" in fields:
        kw["diarization"] = True
    return cls(**kw)


def input_transcription_config(types, session):
    """The input transcription config of ``session``: the shop's expected languages (else the session language)
    and the vocabulary of the last prompt built for its shop."""
    languages = list(getattr(session, "languages", None) or []) or [str(getattr(session, "lang", "") or "en")]
    return transcription_config(types, languages=languages,
                                vocabulary=last_vocabulary(str(getattr(session, "shop_id", "") or "")))


def live_extras(types) -> dict:
    """Extra Live RunConfig fields. Proactive audio (on unless AIRA_PROACTIVE_AUDIO=0): the model may stay
    silent when speech is not meant for it, e.g. Sam chatting with a customer, and answers only when needed."""
    import os

    if os.environ.get("AIRA_PROACTIVE_AUDIO", "1") == "0" or not hasattr(types, "ProactivityConfig"):
        return {}
    return {"proactivity": types.ProactivityConfig(proactive_audio=True)}


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
        from google.adk.agents import Agent, LiveRequestQueue  # noqa: I001
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
            input_audio_transcription=input_transcription_config(types, self._s),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(handle=resume_handle),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=100_000, sliding_window=types.SlidingWindow(target_tokens=60_000)),
            **live_extras(types),
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
