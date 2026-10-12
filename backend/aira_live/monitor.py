"""Live monitor: a read-only fan-out, per shop, of what a session hears, sees and does (the dashboard's Live page).

``GET /monitor?shop=<id>`` (``main.py``) subscribes a browser to one shop once it has sent ``{t:"auth", key}``
(``authenticate``: the key never travels in the URL). The hub here keeps a bounded queue per subscriber;
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

Keys: the credential is bound to the shop. ``shop_key(shop)`` = ``hex(HMAC-SHA256(AIRA_MONITOR_KEY, shop_id))[:32]``
and ``key_ok`` compares the auth message's key with it in constant time, so a key for shop A opens nothing on shop
B; with the env unset nobody gets in. In tokenless / demo mode (no Firebase Auth) only the configured demo shop may
be watched at all. ``python -m aira_live.monitor key <shop>`` prints a shop's key for the dashboard's ``#key=``.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import io
import json
import logging
import re
import sys
import time
from collections import deque
from typing import Any

from pydantic import BaseModel, ValidationError

from aira_live import config, monitor_hooks, rtc
from aira_live.contracts import (
    CountMsg,
    CueMsg,
    ErrorMsg,
    MonAuthMsg,
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

MAX_WIDTH = 480       # a monitor frame is at most this wide
JPEG_QUALITY = 60     # 0.6
MAX_FPS = 4           # per session, frames of any purpose but the preview
PREVIEW_MAX_FPS = 10  # per session, the phone's own ``preview`` stream (already small: relayed as it is)
MAX_QUEUE = 64        # lines a slow monitor may fall behind before it loses the oldest
MAX_MONITORS_PER_SHOP = 8
AUTH_TIMEOUT_S = 5.0  # a monitor that has not sent {t:"auth", key} by then is closed
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
    """

    def __init__(self, shop_id: str):
        self.shop_id = shop_id
        self._lines: deque[tuple[str | None, str]] = deque(maxlen=MAX_QUEUE)
        self._event = asyncio.Event()
        try:  # the loop that serves this monitor; a publisher on another loop or thread wakes it safely
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def push(self, line: str, key: str | None = None) -> None:
        if key is not None and any(k == key for k, _ in self._lines):
            self._lines = deque((item for item in self._lines if item[0] != key), maxlen=MAX_QUEUE)
        self._lines.append((key, line))  # a full deque drops its oldest line
        try:
            same_loop = self._loop is None or asyncio.get_running_loop() is self._loop
        except RuntimeError:
            same_loop = self._loop is None
        if same_loop:
            self._event.set()
        elif not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._event.set)

    def take(self) -> list[dict]:
        """Every pending line, parsed (tests); empties the queue."""
        out = [json.loads(line) for _, line in self._lines]
        self._lines.clear()
        self._event.clear()
        return out

    async def next_line(self) -> str:
        while not self._lines:
            self._event.clear()
            await self._event.wait()
        return self._lines.popleft()[1]


_HUB: dict[str, list[Subscriber]] = {}
_LAST_FRAME: dict[str, float] = {}   # session_id -> when its last frame went out
_SPEAKER: dict[str, str] = {}        # session_id -> the speaker a sale tool named ("owner" | "customer")
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
def subscribe(shop_id: str) -> Subscriber:
    sub = Subscriber(shop_id)
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


# ---------- auth ----------
SHOP_KEY_LEN = 32


def shop_key(shop_id: str) -> str | None:
    """The monitor key of one shop: ``hex(HMAC-SHA256(AIRA_MONITOR_KEY, shop_id))[:32]``. The master secret
    never leaves the server; a shop's key opens that shop only. None with the env unset or no shop."""
    secret = config.load().monitor_key
    if not secret or not shop_id:
        return None
    return hmac.new(secret.encode(), shop_id.encode(), hashlib.sha256).hexdigest()[:SHOP_KEY_LEN]


def key_ok(key: str | None, shop_id: str) -> bool:
    """Constant-time check of ``key`` against ``shop_key(shop_id)``; False when either is missing."""
    expected = shop_key(shop_id)
    if not expected or not key:
        return False
    return hmac.compare_digest(key.encode(), expected.encode())


def shop_allowed(shop_id: str) -> bool:
    """In tokenless / demo mode (mock, or ``AIRA_ALLOW_NO_TOKEN=1``: there is no Firebase Auth and every phone
    lands in the demo shop) only the configured demo shop may be watched."""
    settings = config.load()
    if settings.mock or settings.allow_no_token:
        return shop_id == settings.demo_shop_id
    return bool(shop_id)


async def authenticate(ws, shop_id: str) -> bool:
    """Wait at most ``AUTH_TIMEOUT_S`` for the monitor's first text frame, ``{t:"auth", key}``, and check the key
    against ``shop_id``'s. False for a shop that may not be watched, a wrong or malformed message, a timeout, or a
    socket that went away. Nothing is published before."""
    if not shop_allowed(shop_id):
        log.warning("monitor refused: shop %r is not the demo shop", shop_id)
        return False
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.warning("monitor refused: no auth within %.0f s", AUTH_TIMEOUT_S)
        return False
    except Exception:  # noqa: BLE001 - disconnected, or a binary frame
        return False
    msg = parse_monitor_client_msg(raw)
    return isinstance(msg, MonAuthMsg) and key_ok(msg.key, shop_id)


def main(argv: list[str] | None = None) -> int:
    """``python -m aira_live.monitor key <shop>`` prints that shop's monitor key (from env ``AIRA_MONITOR_KEY``)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "key" or not args[1].strip():
        print("usage: python -m aira_live.monitor key <shop_id>", file=sys.stderr)
        return 2
    key = shop_key(args[1].strip())
    if key is None:
        print("AIRA_MONITOR_KEY is not set: there is no monitor key for any shop", file=sys.stderr)
        return 2
    print(key)
    return 0


# ---------- the socket ----------
async def serve(ws, sub: Subscriber) -> None:
    """Pump ``sub``'s lines to ``ws`` until the monitor goes away. The only inbound text acted on is WebRTC
    signalling (``{t:"rtc", ...}``, relayed by ``aira_live.rtc`` to a phone of the shop, rate-limited); the
    feed itself stays read-only and anything else is ignored."""
    async def pump() -> None:
        while True:
            await ws.send_text(await sub.next_line())

    task = asyncio.create_task(pump(), name=f"monitor-{sub.shop_id}")
    rtc_limit = rtc.limiter()
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if isinstance(text, str):
                msg = parse_monitor_client_msg(text)
                if isinstance(msg, RtcMsg) and rtc_limit.allow():
                    await rtc.from_monitor(sub, msg)
    finally:
        task.cancel()
        try:
            await task
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
    """The ``session`` message's fields for ``session`` (``uid`` tells a room camera from the owner's phone)."""
    fields: dict[str, Any] = {"lang": _lang(session)}
    uid = getattr(session, "uid", None)
    if isinstance(uid, str) and uid:
        fields["uid"] = uid
    return fields


def session_started(session) -> None:
    publish(session.shop_id, "session", session.session_id, state="start", **session_fields(session))
    publish(session.shop_id, "status", session.session_id, state="idle")


def session_ended(session) -> None:
    rtc.session_ended(session)  # its monitors hear bye first, then the session end
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

if __name__ == "__main__":  # pragma: no cover - the CLI entry; ``main`` is tested directly
    sys.exit(main())
