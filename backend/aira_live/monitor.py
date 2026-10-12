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
  ``MAX_WIDTH`` px / quality ``JPEG_QUALITY``, at most ``MAX_FPS`` a second per session, and only while someone is
  watching the shop (the image bytes are never decoded otherwise).
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

Keys: ``key_ok`` compares the auth message's key with ``AIRA_MONITOR_KEY`` in constant time; with the env unset
nobody gets in.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import io
import json
import logging
import re
import time
from collections import deque
from typing import Any

from pydantic import BaseModel, ValidationError

from aira_live import config, monitor_hooks
from aira_live.contracts import (
    CountMsg,
    CueMsg,
    ErrorMsg,
    RequestFrameMsg,
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
MAX_FPS = 4           # per session
MAX_QUEUE = 64        # lines a slow monitor may fall behind before it loses the oldest
MAX_MONITORS_PER_SHOP = 8
AUTH_TIMEOUT_S = 5.0  # a monitor that has not sent {t:"auth", key} by then is closed
STATS_INTERVAL_S = 2.0
_MIN_FRAME_GAP_S = 1.0 / MAX_FPS
_monotonic = time.monotonic  # tests swap in a clock
# A user line that names AIRA is the owner talking to it, even in the middle of a sale.
_ADDRESSES_AIRA = re.compile(r"(?<!\w)(aira|ஐரா|ஐர|आइरा|ऐरा)(?!\w)", re.IGNORECASE)
_CUE_STATE = {"thinking": "thinking", "done": "idle", "ack": "listening"}


class Subscriber:
    """One connected monitor: a bounded queue of serialised lines and a wake-up for the sender."""

    def __init__(self, shop_id: str):
        self.shop_id = shop_id
        self._lines: deque[str] = deque(maxlen=MAX_QUEUE)
        self._event = asyncio.Event()
        try:  # the loop that serves this monitor; a publisher on another loop or thread wakes it safely
            self._loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    def push(self, line: str) -> None:
        self._lines.append(line)  # a full deque drops its oldest line
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
        out = [json.loads(line) for line in self._lines]
        self._lines.clear()
        self._event.clear()
        return out

    async def next_line(self) -> str:
        while not self._lines:
            self._event.clear()
            await self._event.wait()
        return self._lines.popleft()


_HUB: dict[str, list[Subscriber]] = {}
_LAST_FRAME: dict[str, float] = {}   # session_id -> when its last frame went out
_SPEAKER: dict[str, str] = {}        # session_id -> the speaker a sale tool named ("owner" | "customer")
_STATS_TASKS: dict[str, asyncio.Task] = {}  # shop_id -> the stats ticker while the shop is watched


def reset() -> None:
    """Forget every monitor and per-session state (tests)."""
    for task in _STATS_TASKS.values():
        task.cancel()
    _STATS_TASKS.clear()
    _HUB.clear()
    _LAST_FRAME.clear()
    _SPEAKER.clear()
    _COUNTERS.clear()


# ---------- hub ----------
def subscribe(shop_id: str) -> Subscriber:
    sub = Subscriber(shop_id)
    subs = _HUB.setdefault(shop_id, [])
    subs.append(sub)
    if len(subs) == 1:
        _start_stats(shop_id)
    return sub


def unsubscribe(sub: Subscriber) -> None:
    subs = _HUB.get(sub.shop_id)
    if subs and sub in subs:
        subs.remove(sub)
        if not subs:
            del _HUB[sub.shop_id]
            _stop_stats(sub.shop_id)


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
    for sub in list(subs):
        sub.push(line)
    return len(subs)


# ---------- auth ----------
def key_ok(key: str | None) -> bool:
    """Constant-time check of a key against env ``AIRA_MONITOR_KEY``; False when either is missing."""
    expected = config.load().monitor_key
    if not expected or not key:
        return False
    return hmac.compare_digest(key.encode(), expected.encode())


async def authenticate(ws) -> bool:
    """Wait at most ``AUTH_TIMEOUT_S`` for the monitor's first text frame, ``{t:"auth", key}``, and check the key.
    False on a wrong or malformed message, a timeout, or a socket that went away. Nothing is published before."""
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_S)
    except asyncio.TimeoutError:
        log.warning("monitor refused: no auth within %.0f s", AUTH_TIMEOUT_S)
        return False
    except Exception:  # noqa: BLE001 - disconnected, or a binary frame
        return False
    msg = parse_monitor_client_msg(raw)
    return msg is not None and key_ok(msg.key)


# ---------- the socket ----------
async def serve(ws, sub: Subscriber) -> None:
    """Pump ``sub``'s lines to ``ws`` until the monitor goes away. Inbound text is ignored (read-only)."""
    async def pump() -> None:
        while True:
            await ws.send_text(await sub.next_line())

    task = asyncio.create_task(pump(), name=f"monitor-{sub.shop_id}")
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
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
    _LAST_FRAME.pop(session_id, None)
    _COUNTERS.pop(session_id, None)


def _sale_open(session_id: str) -> bool:
    try:
        from aira_live.tools import sales  # lazy: tools import the world
    except Exception:  # noqa: BLE001 - the sales lane is optional
        return False
    return session_id in getattr(sales, "active_sale", {})


def speaker_role(session, text: str) -> str:
    """"owner" or "customer" for a user transcript of ``session``."""
    sid = session.session_id
    if not _sale_open(sid):
        return "owner"
    named = _SPEAKER.get(sid)
    if named:
        return named
    return "owner" if _ADDRESSES_AIRA.search(text or "") else "customer"


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
    session, downscaled off the event loop. True when a frame went out. Every frame counts for the stats."""
    sid = session.session_id
    count_frame(sid)
    if not monitored(session.shop_id):
        return False
    now = _monotonic()
    last = _LAST_FRAME.get(sid)
    if last is not None and now - last < _MIN_FRAME_GAP_S:
        return False
    _LAST_FRAME[sid] = now
    try:
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
