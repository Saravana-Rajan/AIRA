"""Live monitor: a read-only fan-out, per shop, of what a session hears, sees and does (the dashboard's Live page).

``GET /monitor[?shop=<id>]`` (``main.py``) subscribes a browser to the one demo shop once ``authenticate`` has
verified its signed-in viewer (see Access below). The hub here keeps a bounded queue per subscriber;
``publish`` builds a ``contracts.MonitorMsg``, serialises it once and hands the same text to every monitor of
the shop. Nothing is stored: a monitor sees what happens while it is connected, and a slow one loses its oldest
lines, never the service's time.

Publishers (all fire-and-forget, none may break the thing it watches):
- ``mirror`` turns the server messages a session sends to its phone into transcripts (owner / customer / aira),
  status and events. ``main.py`` calls it from the socket's ``send``.
- ``publish_frame`` copies a phone frame (the owner's chest camera or a ``camera:<name>`` room camera) down to
  ``MAX_WIDTH`` px / quality ``JPEG_QUALITY``, at most ``MAX_FPS`` a second per session (the phone's small
  ``preview`` frames pass as they are, up to ``PREVIEW_MAX_FPS``), and only while someone is watching the shop
  (the image bytes are never decoded otherwise). A monitor's queue keeps one frame per session: the newest.
- ``_from_hooks`` is registered with ``monitor_hooks`` (the box producers): every ER 2 result (source ``er2``,
  ``context`` names the prompt) and the phone's own ``boxes`` messages (``mediapipe`` / ``bump``, with
  ``severity: "danger"`` for an obstacle in the walking path) become kind ``vision`` here.
- ``tool_runtime``, ``confirm`` and ``sessions.notify`` publish events; the guidance loop publishes found / lost.
- ``publish_stats`` sends kind ``stats`` for every live session of a watched shop every ``STATS_INTERVAL_S``:
  Live turns (``count_turn``, from live_bridge), ER 2 / Flash calls and their latency (``count_vision``, from
  vision.gemini), and the phone's frame rate (``count_frame``, from ``publish_frame``).

Who is talking: a user transcript is the owner's, unless a sale is open for that session (``tools.sales.
active_sale``) and the line does not address AIRA by name; a sale tool may also say ``speaker="customer"``
(``set_speaker``) and that word wins while the sale is open.

Hearing a session (``AIRA_MONITOR_AUDIO=1``, ``config.monitor_audio``; off by default): a monitor that sends
``{t:"listen", session}`` (``listen``) gets that one session's audio as kind ``audio``: the phone's microphone
chunks as they arrive (``audio_in``, ``who: "owner"``, 16 kHz) and AIRA's voice on its way to the phone
(``mirror`` of ``AudioOutMsg``, ``who: "aira"``, 24 kHz), base64 PCM16 untouched. Nothing is relayed to a monitor
that did not ask, a ``listen`` for another session replaces the first, ``session: null``, the monitor leaving or
the session ending stops it, and a room camera's session never carries audio. Audio has its own bounded queue per
monitor (``MAX_AUDIO_QUEUE`` chunks): a monitor whose socket is backed up loses chunks (``audio_dropped``, logged
every ``AUDIO_DROP_LOG_EVERY``), never the service's memory. With the flag off ``listen`` is ignored, and the
``ready`` line every monitor gets after the session list (``ready_line``; its ``session`` is the monitor's own id)
says ``audio: false`` so the dashboard hides its button.

Access (shared/docs/ops/security.md §2a): by default (``AIRA_MONITOR_AUTH`` unset, the owner's decision) there
is NO authentication: ``authenticate`` returns None without reading a message and the socket is subscribed at
once; anyone who can open the dashboard address watches. What still applies: the browser Origin allowlist,
``origin_allowed`` (config ``monitor_origins`` / env ``AIRA_MONITOR_ORIGINS``; defence in depth only, a script
can send any Origin), the shop check (in tokenless / demo mode only the configured demo shop may be watched,
``shop_allowed``) and the per-shop cap ``MAX_MONITORS_PER_SHOP``. An ``{t:"auth", idToken}`` that arrives
anyway is ignored.

``AIRA_MONITOR_AUTH=1`` restores the Firebase sign-in gate: a viewer is a Google account the owner listed. After
the upgrade the monitor's first message, within ``AUTH_TIMEOUT_S``, must be ``{t:"auth", idToken}`` with a
Firebase Authentication ID token; ``authenticate`` verifies it server-side (``auth.verify_firebase_user``:
signature, issuer, audience, expiry) and checks the account against ``AIRA_MONITOR_VIEWERS``
(``auth.monitor_viewer_allowed``). Nothing is subscribed, sent or relayed before that; a failure closes the
socket with ``CLOSE_UNAUTHENTICATED`` (4401: missing, late, malformed, invalid or expired token) or
``CLOSE_UNAUTHORIZED`` (4403: a valid token for an account that is not listed). The token expires within an
hour: ``serve`` closes the socket with 4401 at ``exp`` (plus ``EXPIRY_GRACE_S``) unless a fresh
``{t:"auth", idToken}`` for the same account arrived first; a refresh naming another account closes it too.
Every reconnect authenticates from scratch. Logs carry a uid hash and the outcome, never the token or the email.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import logging
import re
import time
from collections import deque
from typing import Any

from pydantic import BaseModel, ValidationError

from aira_live import auth, config, monitor_hooks, rtc
from aira_live.auth import FirebaseUser, uid_hash
from aira_live.contracts import (
    MONITOR_CLOSE_UNAUTHENTICATED,
    MONITOR_CLOSE_UNAUTHORIZED,
    AudioOutMsg,
    CountMsg,
    CueMsg,
    ErrorMsg,
    MonAudioMsg,
    MonAuthMsg,
    MonListenMsg,
    MonReadyMsg,
    RequestFrameMsg,
    RtcMsg,
    StateMsg,
    TargetMsg,
    TranscriptMsg,
    VisionObject,
    dump_monitor_msg,
    monitor_adapter,
    parse_monitor_client_msg,
)
from aira_live.diag import diag

log = logging.getLogger(__name__)

AUTH_TIMEOUT_S = 5.0  # the auth message must arrive this soon after the upgrade
EXPIRY_GRACE_S = 30.0  # a socket whose token expired is closed this long after exp unless refreshed
CLOSE_UNAUTHENTICATED = MONITOR_CLOSE_UNAUTHENTICATED  # 4401
CLOSE_UNAUTHORIZED = MONITOR_CLOSE_UNAUTHORIZED  # 4403
# The token verifier (tests swap in a fake that never touches the network). Called off the event loop.
verify_user = auth.verify_firebase_user
_time = time.time  # tests swap in a clock for the expiry watchdog

MAX_WIDTH = 480       # a monitor frame is at most this wide
JPEG_QUALITY = 60     # 0.6
MAX_FPS = 4           # per session, frames of any purpose but the preview
PREVIEW_MAX_FPS = 10  # per session, the phone's own ``preview`` stream (already small: relayed as it is)
MAX_QUEUE = 64        # lines a slow monitor may fall behind before it loses the oldest
MAX_AUDIO_QUEUE = 32  # audio chunks a listening monitor may fall behind (the phone sends 32 ms ones: about 1 s)
AUDIO_DROP_LOG_EVERY = 100  # one log line per this many chunks a backed-up monitor lost
MAX_MONITORS_PER_SHOP = 8
STATS_INTERVAL_S = 2.0
_MIN_FRAME_GAP_S = 1.0 / MAX_FPS
_MIN_PREVIEW_GAP_S = 1.0 / PREVIEW_MAX_FPS
_monotonic = time.monotonic  # tests swap in a clock
# A user line that names AIRA is the owner talking to it, even in the middle of a sale.
_ADDRESSES_AIRA = re.compile(r"(?<!\w)(aira|ஐரா|ஐர|आइरा|ऐरा)(?!\w)", re.IGNORECASE)
_CUE_STATE = {"thinking": "thinking", "done": "idle", "ack": "listening"}


class Subscriber:
    """One connected monitor: a bounded queue of serialised lines and a wake-up for the sender.

    A line pushed with a ``key`` replaces any queued line with the same key: a frame of a session supersedes
    the frame of that session still waiting, so a monitor that fell behind gets the newest picture, never a
    backlog of old ones.

    Audio (``push_audio``) waits in its own bounded queue so a burst of chunks never pushes transcripts or
    status lines out of the line queue; the sender drains lines first. A full audio queue loses its oldest
    chunk (the monitor hears the newest sound after a stall, never a growing delay) and counts it in
    ``audio_dropped``. ``listening`` is the session this monitor asked to hear, or None.
    """

    def __init__(self, shop_id: str, viewer: FirebaseUser | None = None):
        self.shop_id = shop_id
        self.viewer = viewer  # the verified account behind a /monitor socket (None for an in-process subscriber)
        self.listening: str | None = None
        self.audio_dropped = 0
        self._lines: deque[tuple[str | None, str]] = deque(maxlen=MAX_QUEUE)
        self._audio: deque[str] = deque(maxlen=MAX_AUDIO_QUEUE)
        self._event = asyncio.Event()
        try:  # the loop that serves this monitor; a publisher on another loop or thread wakes it safely
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def push(self, line: str, key: str | None = None) -> None:
        if key is not None and any(k == key for k, _ in self._lines):
            self._lines = deque((item for item in self._lines if item[0] != key), maxlen=MAX_QUEUE)
        self._lines.append((key, line))  # a full deque drops its oldest line
        self._wake()

    def push_audio(self, line: str) -> bool:
        """Queue one serialised audio chunk; False (and one more in ``audio_dropped``) when the queue was full
        and its oldest chunk was lost to make room."""
        dropped = len(self._audio) >= MAX_AUDIO_QUEUE
        if dropped:
            self.audio_dropped += 1
            if self.audio_dropped % AUDIO_DROP_LOG_EVERY == 0:
                log.warning("monitor: a backed-up monitor of %s has lost %d audio chunks", self.shop_id, self.audio_dropped)
        self._audio.append(line)
        self._wake()
        return not dropped

    def _wake(self) -> None:
        try:
            same_loop = self._loop is None or asyncio.get_running_loop() is self._loop
        except RuntimeError:
            same_loop = self._loop is None
        if same_loop:
            self._event.set()
        elif not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._event.set)

    def take(self) -> list[dict]:
        """Every pending line, parsed (tests); empties the line queue (audio waits in ``take_audio``)."""
        out = [json.loads(line) for _, line in self._lines]
        self._lines.clear()
        self._event.clear()
        return out

    def take_audio(self) -> list[dict]:
        """Every pending audio chunk, parsed (tests); empties the audio queue."""
        out = [json.loads(line) for line in self._audio]
        self._audio.clear()
        return out

    async def next_line(self) -> str:
        """The next text frame to send: lines first (few, and what the page is built on), then audio."""
        while not self._lines and not self._audio:
            self._event.clear()
            await self._event.wait()
        if self._lines:
            return self._lines.popleft()[1]
        return self._audio.popleft()


_HUB: dict[str, list[Subscriber]] = {}
_LAST_FRAME: dict[str, float] = {}   # session_id -> when its last frame went out
_SPEAKER: dict[str, str] = {}        # session_id -> the speaker a sale tool named ("owner" | "customer")
_ORIENTATION: dict[str, int] = {}    # session_id -> the phone's last-known screen orientation angle
_STATS_TASKS: dict[str, asyncio.Task] = {}  # shop_id -> the stats ticker while the shop is watched
_PREVIEW_TASKS: set[asyncio.Task] = set()   # preview start/stop requests on their way to the phones


def reset() -> None:
    """Forget every monitor and per-session state (tests)."""
    for task in _STATS_TASKS.values():
        task.cancel()
    for task in list(_PREVIEW_TASKS):
        task.cancel()
    _STATS_TASKS.clear()
    _PREVIEW_TASKS.clear()
    _HUB.clear()
    _LAST_FRAME.clear()
    _SPEAKER.clear()
    _VOICES.clear()
    _COUNTERS.clear()
    rtc.reset()


# ---------- hub ----------
def subscribe(shop_id: str, viewer: FirebaseUser | None = None) -> Subscriber:
    sub = Subscriber(shop_id, viewer)
    subs = _HUB.setdefault(shop_id, [])
    subs.append(sub)
    if len(subs) == 1:
        _start_stats(shop_id)
        _schedule_preview(shop_id, start=True)
    rtc.monitor_joined(sub)  # its id for WebRTC signalling; a paused preview resumes for this monitor
    return sub


def unsubscribe(sub: Subscriber) -> None:
    subs = _HUB.get(sub.shop_id)
    if subs and sub in subs:
        subs.remove(sub)
        if not subs:
            del _HUB[sub.shop_id]
            _stop_stats(sub.shop_id)
            _schedule_preview(sub.shop_id, start=False)
        rtc.monitor_left(sub)  # bye to the phones it had video from; the preview resumes for the others


# ---------- the preview stream ----------
# While at least one monitor watches a shop, each of its phone sessions streams small ``preview`` frames
# (the phone: 8 fps, 360 px wide, JPEG quality 0.45, each sent at once and dropped when the socket is busy). The server relays them to the monitors only (``main.py``
# hands them to ``publish_frame`` and nowhere else: never stored, never seen by Gemini). The last monitor
# leaving stops the stream; a session that joins a watched shop starts it (``preview_on_join``).
PREVIEW_PURPOSE = "preview"


def _preview_msg(start: bool) -> RequestFrameMsg:
    return RequestFrameMsg(purpose=PREVIEW_PURPOSE, continuous=True) if start \
        else RequestFrameMsg(purpose=PREVIEW_PURPOSE, stop=True)


def _streams_frames(session) -> bool:
    """A phone session; a shop camera (uid ``camera:<name>``) only sends frames and is never asked."""
    try:
        from aira_live.cameras.sources import is_camera_session  # lazy: cameras import the session machinery
        return not is_camera_session(session)
    except Exception:  # noqa: BLE001
        return not str(getattr(session, "uid", "") or "").startswith("camera:")


async def set_preview(session, start: bool) -> bool:
    """Ask one phone session to start or stop its preview stream (``aira_live.rtc`` pauses it while the
    dashboards have WebRTC video instead). Never raises; True when the request went out."""
    try:
        await session.send(_preview_msg(start))
    except Exception as exc:  # noqa: BLE001 - a phone that just left
        log.debug("monitor: preview %s not sent to %s: %s", "start" if start else "stop", session.session_id, exc)
        return False
    diag("monitor_preview", session=session.session_id, shop=session.shop_id, start=start)
    return True


async def _preview_shop(shop_id: str, start: bool) -> int:
    """Ask every phone session of ``shop_id`` to start or stop its preview stream; returns how many were asked."""
    try:
        from aira_live import sessions  # lazy: sessions imports this module
        live = sessions.by_shop(shop_id)
    except Exception:  # noqa: BLE001
        log.exception("monitor: preview %s failed for %s", "start" if start else "stop", shop_id)
        return 0
    n = 0
    for s, _bridge in live:
        if _streams_frames(s) and not (start and rtc.preview_paused(s.session_id)) and await set_preview(s, start):
            n += 1
    return n


def _schedule_preview(shop_id: str, start: bool) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (sync tests): nothing to ask
    task = loop.create_task(_preview_shop(shop_id, start), name=f"monitor-preview-{shop_id}")
    _PREVIEW_TASKS.add(task)
    task.add_done_callback(_PREVIEW_TASKS.discard)


async def preview_on_join(session) -> bool:
    """A session said hello: start its preview stream when its shop is already watched. Never raises."""
    if not monitored(session.shop_id) or not _streams_frames(session):
        return False
    return await set_preview(session, True)


def monitored(shop_id: str) -> bool:
    return bool(_HUB.get(shop_id))


def monitors(shop_id: str) -> int:
    return len(_HUB.get(shop_id, ()))


def publish(shop_id: str, kind: str, session_id: str, **fields: Any) -> int:
    """Send ``{t:"mon", kind, session, ts, **fields}`` to every monitor of ``shop_id``; returns how many got it.

    Sync and non-blocking, so sync code (the tool runtime) can call it. An invalid message is logged and dropped:
    a bad publisher never raises into the thing it watches.
    """
    subs = _HUB.get(shop_id)
    if not subs:
        return 0
    try:
        msg = monitor_adapter.validate_python({"t": "mon", "kind": kind, "session": session_id,
                                               "ts": int(time.time() * 1000), **fields})
        line = dump_monitor_msg(msg)
    except ValidationError as exc:
        log.warning("monitor: dropped an invalid %s message: %s", kind, exc.errors()[:1])
        return 0
    except Exception:
        log.exception("monitor: dropped a %s message", kind)
        return 0
    key = f"frame:{session_id}" if kind == "frame" else None
    for sub in list(subs):
        sub.push(line, key)
    return len(subs)


# ---------- access ----------
def origin_allowed(origin: str | None, allowed: tuple[str, ...] | list[str] | None = None) -> bool:
    """Defence in depth on ``/monitor`` (the viewer check in ``authenticate`` is the gate only with
    ``AIRA_MONITOR_AUTH=1``; by default there is no gate at all): True when ``origin``
    (the upgrade's Origin header, as sent) is on the allowlist (``config.monitor_origins`` unless ``allowed`` is
    given). An entry ending in ``:*`` matches that scheme and host on any port (``http://localhost:*``); every
    other entry is an exact match (scheme, host and port, a trailing slash ignored). A missing or empty Origin is
    refused: a browser always sends one on a WebSocket upgrade, and the dashboard is the only intended client.
    This is not authentication: a script can send any Origin it likes, and passing it admits nobody."""
    if not origin:
        return False
    origin = origin.strip().rstrip("/")
    if not origin:
        return False
    entries = config.load().monitor_origins if allowed is None else allowed
    for entry in entries:
        entry = entry.strip().rstrip("/")
        if not entry:
            continue
        if entry.endswith(":*"):
            base = entry[:-2]
            if origin == base or (origin.startswith(base + ":") and origin[len(base) + 1:].isdigit()):
                return True
        elif origin == entry:
            return True
    return False


def shop_allowed(shop_id: str) -> bool:
    """In tokenless / demo mode (mock, or ``AIRA_ALLOW_NO_TOKEN=1``: there is no Firebase Auth and every phone
    lands in the demo shop) only the configured demo shop may be watched."""
    settings = config.load()
    if settings.mock or settings.allow_no_token:
        return shop_id == settings.demo_shop_id
    return bool(shop_id)


# ---------- the viewer check ----------
class Refused(Exception):
    """``authenticate`` refused the socket: ``code`` is the WebSocket close code to send, ``reason`` the log line."""

    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


async def _verify(id_token: str) -> FirebaseUser | None:
    """``verify_user`` off the event loop (it may fetch Google's certificates once in a while)."""
    return await asyncio.to_thread(verify_user, id_token)


async def authenticate(ws, shop_id: str) -> FirebaseUser | None:
    """The shop check first (``Refused`` 4403 for a shop that may not be watched). Then, without
    ``config.monitor_auth`` (the default): return None at once, no message read, no viewer. With it: wait at most
    ``AUTH_TIMEOUT_S`` for the monitor's first text frame, ``{t:"auth", idToken}``, verify the token and the
    viewer allowlist, and return the viewer. Raises ``Refused`` (4401 for no / late / malformed / invalid /
    expired token, 4403 for an account that is not listed). Nothing is subscribed or sent before this returns."""
    if not shop_allowed(shop_id):
        raise Refused(CLOSE_UNAUTHORIZED, f"shop {shop_id!r} is not the demo shop")
    if not config.load().monitor_auth:
        return None
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise Refused(CLOSE_UNAUTHENTICATED, f"no auth within {AUTH_TIMEOUT_S:.0f} s") from None
    except Exception:  # noqa: BLE001 - disconnected, or a binary frame
        raise Refused(CLOSE_UNAUTHENTICATED, "socket closed before auth") from None
    msg = parse_monitor_client_msg(raw)
    if not isinstance(msg, MonAuthMsg):
        raise Refused(CLOSE_UNAUTHENTICATED, "first message is not an auth message")
    user = await _verify(msg.id_token)
    if user is None:
        raise Refused(CLOSE_UNAUTHENTICATED, "token not verified")
    if not auth.monitor_viewer_allowed(user):
        raise Refused(CLOSE_UNAUTHORIZED, f"uid={uid_hash(user.uid)} is not in AIRA_MONITOR_VIEWERS")
    return user


# ---------- the socket ----------
async def serve(ws, sub: Subscriber, viewer: FirebaseUser | None) -> None:
    """Pump ``sub``'s lines to ``ws`` until the monitor goes away, its token expires unrefreshed, or a refresh
    fails. Inbound text acted on: ``{t:"auth", idToken}`` (a refresh: verified again, same account required,
    extends the expiry deadline), WebRTC signalling (``{t:"rtc", ...}``, relayed by ``aira_live.rtc`` to a
    phone of the shop, rate-limited) and ``{t:"listen", session}`` (``listen``: hear that session's audio, only
    with ``AIRA_MONITOR_AUDIO``). The feed itself stays read-only and anything else is ignored.

    ``viewer`` is None without ``AIRA_MONITOR_AUTH``: then there is no expiry watchdog and an auth message is
    ignored like anything else (not verified; the socket stays open)."""
    deadline = [float(viewer.exp) + EXPIRY_GRACE_S if viewer is not None else float("inf")]
    closing: list[int] = []  # the close code chosen by the watchdog or a failed refresh

    async def pump() -> None:
        while True:
            await ws.send_text(await sub.next_line())

    async def watchdog() -> None:
        if viewer is None:
            return
        while True:
            wait = deadline[0] - _time()
            if wait <= 0:
                break
            await asyncio.sleep(min(wait, 1.0))
        closing.append(CLOSE_UNAUTHENTICATED)
        log.info("monitor closed: token of uid=%s expired without a refresh", uid_hash(viewer.uid))
        diag("monitor", event="expired", shop=sub.shop_id, uid=uid_hash(viewer.uid))
        await ws.close(code=CLOSE_UNAUTHENTICATED)

    task = asyncio.create_task(pump(), name=f"monitor-{sub.shop_id}")
    expiry = asyncio.create_task(watchdog(), name=f"monitor-expiry-{sub.shop_id}")
    rtc_limit = rtc.limiter()
    try:
        while not closing:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if not isinstance(text, str):
                continue
            msg = parse_monitor_client_msg(text)
            if isinstance(msg, MonAuthMsg) and viewer is not None:
                fresh = await _verify(msg.id_token)
                if fresh is None or fresh.uid != viewer.uid or not auth.monitor_viewer_allowed(fresh):
                    why = "not verified" if fresh is None else "another account" if fresh.uid != viewer.uid else "no longer allowed"
                    log.info("monitor closed: refresh for uid=%s %s", uid_hash(viewer.uid), why)
                    diag("monitor", event="refresh", ok=False, shop=sub.shop_id, uid=uid_hash(viewer.uid), why=why)
                    closing.append(CLOSE_UNAUTHENTICATED if fresh is None or fresh.uid != viewer.uid else CLOSE_UNAUTHORIZED)
                    await ws.close(code=closing[-1])
                    break
                deadline[0] = float(fresh.exp) + EXPIRY_GRACE_S
                diag("monitor", event="refresh", ok=True, shop=sub.shop_id, uid=uid_hash(viewer.uid), exp=fresh.exp)
            elif isinstance(msg, RtcMsg) and rtc_limit.allow():
                await rtc.from_monitor(sub, msg)
            elif isinstance(msg, MonListenMsg):
                listen(sub, msg.session)
    finally:
        for t in (task, expiry):
            t.cancel()
        for t in (task, expiry):
            try:
                await t
            except BaseException:  # noqa: BLE001 - cancelled, or the socket died under the pump
                pass


# ---------- who is talking ----------
def set_speaker(session_id: str, speaker: str | None) -> None:
    """A sale tool said who is speaking in this turn ("customer" or "owner"); it holds while the sale is open."""
    if speaker in ("customer", "owner"):
        _SPEAKER[session_id] = speaker
    else:
        _SPEAKER.pop(session_id, None)


def forget(session_id: str) -> None:
    _SPEAKER.pop(session_id, None)
    _VOICES.pop(session_id, None)
    _LAST_FRAME.pop(session_id, None)
    _COUNTERS.pop(session_id, None)
    _ORIENTATION.pop(session_id, None)


class _Voices:
    """Diarization labels heard in one session: how often each spoke, in order of first hearing, and who spoke
    last. The owner is the voice heard most (the first one on a tie): Sam talks to AIRA all day."""
    __slots__ = ("counts", "last")

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.last: str | None = None

    def owner(self) -> str | None:
        if not self.counts:
            return None
        return max(self.counts, key=lambda k: (self.counts[k], -list(self.counts).index(k)))


_VOICES: dict[str, _Voices] = {}


def note_speaker(session_id: str, label: str | None) -> None:
    """The transcription's diarization named the voice of the last finished utterance (live_bridge)."""
    if not isinstance(label, str) or not label.strip():
        return
    label = label.strip()
    v = _VOICES.get(session_id)
    if v is None:
        v = _VOICES[session_id] = _Voices()
    v.counts[label] = v.counts.get(label, 0) + 1
    v.last = label


def _sale_open(session_id: str) -> bool:
    try:
        from aira_live.tools import sales  # lazy: tools import the world
    except Exception:  # noqa: BLE001 - the sales lane is optional
        return False
    return session_id in getattr(sales, "active_sale", {})


def speaker_role(session, text: str) -> str:
    """"owner" or "customer" for a user transcript of ``session``: nobody is a customer outside a sale; during
    one, a sale tool's word wins, then a line naming AIRA is the owner's, then the diarization (a voice other
    than the owner's is the customer), else the line is the customer's."""
    sid = session.session_id
    if not _sale_open(sid):
        return "owner"
    named = _SPEAKER.get(sid)
    if named:
        return named
    if _ADDRESSES_AIRA.search(text or ""):
        return "owner"
    voices = _VOICES.get(sid)
    if voices is not None and voices.last is not None:
        return "owner" if voices.last == voices.owner() else "customer"
    return "customer"


# ---------- publishers ----------
def _lang(session) -> str:
    return str(getattr(session, "lang", "") or "en")


def session_fields(session) -> dict[str, Any]:
    """The ``session`` message's fields for ``session`` (``uid`` tells a room camera from the owner's phone;
    ``started_at`` -> ``startedAt``, epoch ms, when the session object was made, so a late monitor knows how
    long it has been running)."""
    fields: dict[str, Any] = {"lang": _lang(session)}
    uid = getattr(session, "uid", None)
    if isinstance(uid, str) and uid:
        fields["uid"] = uid
    started_at = getattr(session, "started_at", None)
    if isinstance(started_at, (int, float)) and not isinstance(started_at, bool):
        fields["started_at"] = int(started_at * 1000)
    sid = getattr(session, "session_id", None)
    angle = _ORIENTATION.get(sid) if sid is not None else None
    if angle is not None:
        fields["orientation"] = angle
    return fields


def session_started(session) -> None:
    publish(session.shop_id, "session", session.session_id, state="start", **session_fields(session))
    publish(session.shop_id, "status", session.session_id, state="idle")


def session_ended(session) -> None:
    rtc.session_ended(session)  # its monitors hear bye first, then the session end
    for sub in _HUB.get(session.shop_id, ()):  # nobody keeps listening to a session that is over
        if sub.listening == session.session_id:
            sub.listening = None
    publish(session.shop_id, "session", session.session_id, state="end", **session_fields(session))
    forget(session.session_id)


def transcript(session, role: str, text: str, final: bool, lang: str | None = None) -> None:
    if text:
        publish(session.shop_id, "transcript", session.session_id, role=role, text=text,
                lang=lang or _lang(session), final=final)


def status(session, state: str) -> None:
    publish(session.shop_id, "status", session.session_id, state=state)


def event(session, name: str, /, **detail: Any) -> None:
    """``name`` is positional-only, so a detail may be called ``name`` too (``event(s, "client_event", name=...)``)."""
    publish(session.shop_id, "event", session.session_id, name=name, detail=_jsonable(detail))


def orientation(session, angle: int) -> None:
    """The phone turned: remember its screen orientation and tell the shop's monitors, so a dashboard watching on
    a laptop can turn the live picture to match (it cannot see the phone physically rotate)."""
    _ORIENTATION[session.session_id] = angle
    publish(session.shop_id, "orientation", session.session_id, angle=angle)


def confirm_proposed(session, confirm_id: str, kind: str, prompt: str) -> None:
    """``confirm.propose`` made a proposal (the phone's ConfirmMsg is not mirrored, so this is said once)."""
    event(session, "confirm_proposed", id=confirm_id, kind=kind, prompt=prompt)


def notified(session, text: str, cue: str) -> None:
    """``sessions.notify`` spoke a vendor reply or an alert (its transcript is mirrored separately)."""
    event(session, "notify", text=text, cue=cue)


def guidance_found(session, target: str, point=None, box=None) -> None:
    """The hand guidance verified a pick: the target is in the owner's hand."""
    objects = [{"label": target, "point": point, "box": box}]
    vision(session, objects, target=target, found=True, context="guidance")
    event(session, "guidance_found", target=target)
    status(session, "found")


def guidance_lost(session, target: str) -> None:
    event(session, "guidance_lost", target=target)
    status(session, "guiding")


def mirror(session, msg: BaseModel) -> None:
    """A server message on its way to the phone -> what the monitor should see of it. Never raises."""
    if not monitored(session.shop_id):
        return
    try:
        _mirror(session, msg)
    except Exception:  # noqa: BLE001
        log.exception("monitor: mirror failed for %s", getattr(msg, "t", msg))


def _mirror(session, msg: BaseModel) -> None:
    if isinstance(msg, TranscriptMsg):
        role = "aira" if msg.role == "aira" else speaker_role(session, msg.text)
        transcript(session, role, msg.text, msg.final, msg.lang)
        if role != "aira":
            status(session, "listening")
    elif isinstance(msg, TargetMsg):  # the box itself came through the guidance loop's publish_boxes (er2)
        event(session, "target", label=msg.label, confidence=msg.confidence)
    elif isinstance(msg, RequestFrameMsg) and msg.purpose == "guide":
        if msg.stop:
            event(session, "guidance_stop")
            status(session, "idle")
        elif msg.continuous:
            event(session, "guidance_start")
            status(session, "guiding")
    elif isinstance(msg, StateMsg):
        event(session, "state", entity=msg.entity, id=msg.id, state=msg.state, summary=msg.summary)
    elif isinstance(msg, CountMsg):
        event(session, "count", sku=msg.sku, counted=msg.counted, expected=msg.expected, final=msg.final,
              confidence=msg.confidence)
    elif isinstance(msg, CueMsg):  # ConfirmMsg is not mirrored: confirm.propose publishes it once
        state = _CUE_STATE.get(msg.cue)
        if state:
            status(session, state)
    elif isinstance(msg, ErrorMsg):
        event(session, "error", code=msg.code, say=msg.say)
    elif isinstance(msg, AudioOutMsg):  # AIRA's voice: only to a monitor listening to this session
        _relay_audio(session, "aira", 24000, msg.pcm24)


# ---------- hearing a session ----------
def audio_enabled() -> bool:
    return config.load().monitor_audio


def ready_line(sub: Subscriber) -> str:
    """The ``ready`` line a monitor gets once, after the session list: whether it may ask to listen. Its
    ``session`` is the monitor's own id (the ``from`` of its rtc signalling), not a phone session."""
    return dump_monitor_msg(MonReadyMsg(session=rtc.monitor_id(sub) or "", ts=int(time.time() * 1000),
                                        audio=audio_enabled()))


def listen(sub: Subscriber, session_id: str | None) -> bool:
    """``{t:"listen", session}`` from a monitor: hear that session (None = stop); the previous choice is replaced.
    Ignored (False) without ``AIRA_MONITOR_AUDIO``. The session is only ever matched by id inside the monitor's
    own shop (``_relay_audio`` runs from the session's side), so naming a session of another shop hears nothing."""
    if not audio_enabled():
        if session_id is not None:
            log.info("monitor: listen ignored for %s (AIRA_MONITOR_AUDIO is off)", sub.shop_id)
        sub.listening = None
        return False
    sub.listening = session_id
    diag("monitor", event="listen", shop=sub.shop_id, session=session_id, dropped=sub.audio_dropped)
    return True


def listeners(session) -> list[Subscriber]:
    return [sub for sub in _HUB.get(session.shop_id, ()) if sub.listening == session.session_id]


def _relay_audio(session, who: str, rate: int, pcm_b64: str) -> int:
    """One chunk to every monitor listening to ``session``; serialised once. Returns how many got it (a monitor
    whose audio queue was full lost its oldest chunk instead and is counted too). Never raises."""
    subs = listeners(session)
    if not subs or not pcm_b64:
        return 0
    try:
        line = dump_monitor_msg(MonAudioMsg(session=session.session_id, ts=int(time.time() * 1000), who=who,
                                            rate=rate, pcm=pcm_b64))
    except Exception:  # noqa: BLE001 - a chunk never breaks the phone's socket
        log.exception("monitor: audio chunk dropped")
        return 0
    for sub in subs:
        sub.push_audio(line)
    return len(subs)


def audio_in(session, pcm16_b64: str) -> int:
    """The phone's microphone chunk (16 kHz PCM16 base64, as sent): to the monitors listening to this session.
    A room camera's session (uid ``camera:<name>``) is never relayed, whatever it sends."""
    if not monitored(session.shop_id) or not _streams_frames(session):
        return 0
    return _relay_audio(session, "owner", 16000, pcm16_b64)


def hand(session, data: dict | None, near: bool) -> bool:
    """A client event with a fingertip: ``{point: [y, x]}`` -> kind "hand". False when there is no point."""
    point = _point((data or {}).get("point"))
    if point is None:
        return False
    return publish(session.shop_id, "hand", session.session_id, point=point, near=near) > 0


def client_event(session, name: str, data: dict | None) -> None:
    if not monitored(session.shop_id):
        return
    near = name == "fingertip_in_target"
    if isinstance(data, dict) and isinstance(data.get("near"), bool):
        near = data["near"]
    hand(session, data, near)
    event(session, "client_event", name=name, data=_jsonable(data or {}))


# ---------- frames ----------
def downscale(jpeg_b64: str) -> tuple[str, int, int]:
    """``(jpeg_b64, w, h)`` of the frame at most ``MAX_WIDTH`` wide, re-encoded at ``JPEG_QUALITY``.
    Raises on bytes that are not an image."""
    from PIL import Image  # lazy: keeps import light for callers that never see a monitor

    raw = base64.b64decode(jpeg_b64, validate=True)
    with Image.open(io.BytesIO(raw)) as img:
        img = img.convert("RGB")
        w, h = img.size
        if w > MAX_WIDTH:
            nh = max(1, round(h * MAX_WIDTH / w))
            img = img.resize((MAX_WIDTH, nh), Image.LANCZOS)
            w, h = img.size
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return base64.b64encode(buf.getvalue()).decode(), w, h


async def publish_frame(session, frame) -> bool:
    """Copy a phone frame to the shop's monitors: only while one is connected, at most ``MAX_FPS`` a second per
    session (``PREVIEW_MAX_FPS`` for the phone's own preview stream, relayed without re-encoding), downscaled
    off the event loop otherwise. True when a frame went out. Every frame counts for the stats."""
    sid = session.session_id
    count_frame(sid)
    if not monitored(session.shop_id):
        return False
    preview = str(frame.purpose) == PREVIEW_PURPOSE
    now = _monotonic()
    last = _LAST_FRAME.get(sid)
    if last is not None and now - last < (_MIN_PREVIEW_GAP_S if preview else _MIN_FRAME_GAP_S):
        return False
    _LAST_FRAME[sid] = now
    try:
        if preview and 0 < frame.w <= MAX_WIDTH:
            b64, w, h = frame.jpeg_b64, frame.w, frame.h  # the phone already made it small: relay as it is
        else:
            b64, w, h = await asyncio.to_thread(downscale, frame.jpeg_b64)
    except (binascii.Error, ValueError, OSError) as exc:
        log.debug("monitor: frame %s skipped: %s", getattr(frame, "id", "?"), exc)
        return False
    except Exception:  # noqa: BLE001
        log.exception("monitor: frame %s failed", getattr(frame, "id", "?"))
        return False
    n = publish(session.shop_id, "frame", sid, jpeg_b64=b64, w=w, h=h, purpose=str(frame.purpose))
    if n:
        diag("monitor_frame", session=sid, shop=session.shop_id, w=w, h=h, bytes=len(b64), monitors=n)
    return n > 0


# ---------- vision ----------
_DISTANCES = ("near", "arm", "far")


def _norm(v: Any) -> int | None:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return min(1000, max(0, n))


def _point(p: Any) -> list[int] | None:
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        return None
    vals = [_norm(v) for v in p]
    return None if any(v is None for v in vals) else vals


def _box(b: Any) -> list[int] | None:
    if not isinstance(b, (list, tuple)) or len(b) != 4:
        return None
    vals = [_norm(v) for v in b]
    return None if any(v is None for v in vals) else vals


def _score(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return max(0.0, min(1.0, float(v)))


def objects_of(objects: list | None) -> list[dict]:
    """Producer objects -> monitor objects ``{label, box|None, point|None[, score, severity, distance]}``;
    unlabeled or junk entries dropped."""
    out = []
    for o in objects or []:
        if hasattr(o, "model_dump"):
            o = o.model_dump()
        if not isinstance(o, dict):
            continue
        label = o.get("label") or o.get("name")
        if not isinstance(label, str) or not label.strip():
            continue
        extra: dict[str, Any] = {}
        score = _score(o.get("score", o.get("confidence")))
        if score is not None:
            extra["score"] = round(score, 3)
        if o.get("severity") == "danger":
            extra["severity"] = "danger"
        if o.get("distance") in _DISTANCES:
            extra["distance"] = o["distance"]
        box = _box(o.get("box") if o.get("box") is not None else o.get("box_2d"))
        out.append(VisionObject(label=label.strip(), box=box, point=_point(o.get("point")), **extra)
                   .model_dump(exclude_none=True))
    return out


def vision(session, objects: list | None, *, source: str = "er2", target: str | None = None,
           found: bool | None = None, context: str | None = None, frame_id: str | None = None) -> int:
    """Publish one kind-``vision`` message; ``severity`` is "danger" when any object is."""
    objs = objects_of(objects)
    fields: dict[str, Any] = {"source": source, "objects": objs}
    if target:
        fields["target"] = target
    if found is not None:
        fields["found"] = found
    if context:
        fields["context"] = context
    if frame_id:
        fields["frame_id"] = frame_id
    if any(o.get("severity") == "danger" for o in objs):
        fields["severity"] = "danger"
    return publish(session.shop_id, "vision", session.session_id, **fields)


def _from_hooks(session, msg: dict) -> None:
    """``monitor_hooks`` callback: a producer's message (``vision`` from ER 2 or the phone's boxes, or a ``frame``
    a producer built itself) -> the hub. Never raises into the producer."""
    shop = getattr(session, "shop_id", None)
    if not shop or not monitored(shop):
        return
    kind = msg.get("kind")
    if kind == "vision":
        vision(session, msg.get("objects"), source=str(msg.get("source") or "er2"), target=msg.get("target"),
               found=msg.get("found"), context=msg.get("context"), frame_id=msg.get("frameId"))
    elif kind == "frame" and msg.get("w") and msg.get("h"):
        publish(shop, "frame", session.session_id, jpeg_b64=msg.get("jpeg"), w=msg["w"], h=msg["h"],
                purpose=str(msg.get("purpose") or "ask"))


# ---------- stats ----------
class _Counters:
    __slots__ = ("turns", "er2", "flash", "vision_ms", "vision_n", "frames")

    def __init__(self) -> None:
        self.turns = self.er2 = self.flash = self.vision_n = 0
        self.vision_ms = 0.0
        self.frames: deque[float] = deque(maxlen=64)


_COUNTERS: dict[str, _Counters] = {}


def _counters(session_id: str) -> _Counters:
    c = _COUNTERS.get(session_id)
    if c is None:
        c = _COUNTERS[session_id] = _Counters()
    return c


def _current_session_id() -> str | None:
    try:
        from aira_live.session import current_session
        return current_session.get().session_id
    except Exception:  # noqa: BLE001 - no session in this context (tests, jobs)
        return None


def count_turn(session_id: str) -> None:
    """One Live API turn ended (live_bridge: ``turn_complete`` without ``more``)."""
    _counters(session_id).turns += 1


def count_vision(model: str, ms: float, session_id: str | None = None) -> None:
    """One vision call answered: Robotics-ER 2 when the model id says so, Flash otherwise. Attributed to
    ``session_id`` or the current session; dropped when neither is known."""
    sid = session_id or _current_session_id()
    if not sid:
        return
    c = _counters(sid)
    if "robotics" in (model or "").lower():
        c.er2 += 1
    else:
        c.flash += 1
    c.vision_ms += max(0.0, float(ms))
    c.vision_n += 1


def count_frame(session_id: str) -> None:
    _counters(session_id).frames.append(_monotonic())


def stats_of(session_id: str) -> dict[str, Any]:
    c = _COUNTERS.get(session_id) or _Counters()
    now = _monotonic()
    recent = sum(1 for t in c.frames if now - t <= STATS_INTERVAL_S)
    return {"turns": c.turns, "er2_calls": c.er2, "flash_calls": c.flash,
            "vision_ms": round(c.vision_ms / c.vision_n) if c.vision_n else None,
            "fps": round(recent / STATS_INTERVAL_S, 1)}


def publish_stats(shop_id: str) -> int:
    """Kind ``stats`` for every live session of ``shop_id``; returns how many sessions were reported."""
    if not monitored(shop_id):
        return 0
    try:
        from aira_live import sessions  # lazy: sessions imports this module
        live = sessions.by_shop(shop_id)
    except Exception:  # noqa: BLE001
        log.exception("monitor: stats failed for %s", shop_id)
        return 0
    n = 0
    for s, _bridge in live:
        if publish(shop_id, "stats", s.session_id, **stats_of(s.session_id)):
            n += 1
    return n


async def _stats_loop(shop_id: str) -> None:
    while True:
        await asyncio.sleep(STATS_INTERVAL_S)
        try:
            publish_stats(shop_id)
        except Exception:  # noqa: BLE001
            log.exception("monitor: stats tick failed for %s", shop_id)


def _start_stats(shop_id: str) -> None:
    if shop_id in _STATS_TASKS:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (sync tests): stats are published on demand
    _STATS_TASKS[shop_id] = loop.create_task(_stats_loop(shop_id), name=f"monitor-stats-{shop_id}")


def _stop_stats(shop_id: str) -> None:
    task = _STATS_TASKS.pop(shop_id, None)
    if task is not None:
        task.cancel()


def _jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v, depth + 1) for v in list(value)[:40]]
    return str(value)[:200]


def _install() -> None:
    """Hear every box producer (ER 2 results, the phone's boxes). Registering twice is once."""
    monitor_hooks.register(_from_hooks)


_install()
