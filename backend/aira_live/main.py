"""aira-live: the Cloud Run service the phone app talks to.

- ``GET /health`` (alias ``/healthz``) says the service is up and whether it runs in mock mode (no
  ``GEMINI_API_KEY``). Cloud Run's frontend reserves some paths that end in "z", so probes use ``/health``.
- ``WS /ws`` speaks the final protocol (``aira_live.contracts``):
  - ``hello`` is checked by ``auth.verify_hello`` (no Firebase Auth): tokenless phones always land in the demo
    shop, and with tokenless mode off the socket gets ``error/permission`` and is closed.
  - Guards (shared/docs/ops/security.md): an upgrade whose Origin is missing or outside ``AIRA_ALLOWED_ORIGINS`` is
    refused before the handshake, a hello whose ``idToken`` is not ``AIRA_APP_KEY`` gets ``error/permission``
    (``auth.verify_hello``), and so does a hello over ``MAX_SESSIONS_PER_IP`` (or ``MAX_SESSIONS``).
  - Real mode: each session gets a ``LiveBridge`` to Gemini Live (``model_for("live")``). Mic audio and typed
    text go to Live; Live's audio, captions and cues come back through the bridge.
  - Mock mode: text is echoed, and audio is accepted and dropped.
  - Both modes store frames, resolve confirmations, answer ``stop`` and pass client ``event``s and ``stop`` to
    the guidance hooks (``aira_live.tools.guidance``, imported lazily).
  - ``boxes`` (the phone's MediaPipe / bump-guard boxes) go to the live monitor through
    ``aira_live.monitor_hooks`` (rate-limited per socket, never answered). Every session's frames, the owner's
    chest camera and a shop camera's (uid ``camera:<name>``) alike, go to ``monitor.publish_frame``, which
    downscales and throttles them only while a monitor watches the shop.
- Other lanes' HTTP routes are ``APIRouter``s in ``OPTIONAL_ROUTERS``, mounted when their module is present.

Review focus: an unknown or invalid message gets ``error/unsupported`` and the socket stays open; a frame
over ``max_frame_b64`` is refused and never stored; the client's ``shopId`` never picks the shop.
"""
import importlib
import inspect
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

from aira_live import config, confirm, live_video, monitor, monitor_hooks, rtc, sessions, tool_runtime
from aira_live.auth import AuthError, verify_hello
from aira_live.cameras.sources import is_camera_session
from aira_live.contracts import (
    AudioInMsg,
    BoxesMsg,
    ConfirmReplyMsg,
    CueMsg,
    ErrorMsg,
    EventMsg,
    FrameMsg,
    HelloMsg,
    LogMsg,
    ReadyMsg,
    RtcMsg,
    StopMsg,
    TextMsg,
    TranscriptMsg,
    client_adapter,
    dump_monitor_msg,
    dump_server_msg,
    monitor_adapter,
)
from aira_live.diag import RateLimiter, cap_json, diag, trunc
from aira_live.frames import Frame
from aira_live.vision import precount
from aira_live.system_lines import line
from aira_live.i18n.expected import shop_languages
from aira_live.live_bridge import AdkLiveBackend, LiveBridge
from aira_live.prompt import build_system_prompt
from aira_live.session import Session, current_session
from aira_live.tool_runtime import live_tools
from aira_live.tools import NON_BLOCKING_TOOLS, SILENT_TOOLS, active_tools

log = logging.getLogger(__name__)

# APIRouter modules of other lanes, mounted when present: jobs (saravana/02), WhatsApp/Telegram webhooks (kanish/02),
# the dashboard's read-only HTTP API (api), and the app's start-up camera check (device_check).
OPTIONAL_ROUTERS: tuple[str, ...] = ("aira_live.jobs", "aira_live.messaging.webhooks", "aira_live.api",
                                     "aira_live.device_check")

# Guards (shared/docs/ops/security.md). The service is public (--allow-unauthenticated), there is no login, and it
# runs on one instance. Three layers bound what a stranger with the URL can do:
# - /ws (and the HTTP API) may be opened only from an allowed browser origin; a request without Origin is refused;
# - the hello must carry the shared demo key AIRA_APP_KEY as idToken (auth.verify_hello);
# - one address gets at most MAX_SESSIONS_PER_IP live sessions, and the service MAX_SESSIONS in all,
#   which caps what can be spent on Gemini Live. The owner's one phone never reaches either limit.
MAX_SESSIONS = 6
MAX_SESSIONS_PER_IP = 3
# Client diagnostic ``log`` lines kept per socket per second (aira_live.diag); the rest are dropped.
LOG_MAX_PER_S = 20
# Client ``boxes`` messages relayed to the monitor per socket per second. The phone sends at most 5 a second;
# the headroom absorbs wall-clock bucket edges, so the limit only bites a misbehaving client.
BOXES_MAX_PER_S = 8


def _log_limiter() -> RateLimiter:
    return RateLimiter(LOG_MAX_PER_S)


def _boxes_limiter() -> RateLimiter:
    return RateLimiter(BOXES_MAX_PER_S)

# Browser origins allowed on /ws (the Origin header) and on the HTTP API (CORS, the dashboard's forecast card):
# the Firebase Hosting site and the local Vite servers. Env AIRA_ALLOWED_ORIGINS (comma-separated) replaces it.
DEFAULT_ALLOWED_ORIGINS = ("https://aira-shop.web.app", "https://aira-shop.firebaseapp.com",
                           "http://localhost:5173", "http://localhost:4173")


def allowed_origins() -> list[str]:
    raw = os.getenv("AIRA_ALLOWED_ORIGINS") or ",".join(DEFAULT_ALLOWED_ORIGINS)
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def origin_allowed(origin: str | None) -> bool:
    """A browser always sends Origin on a WebSocket upgrade, and the app (a TWA on the hosting site) is a
    browser, so an upgrade without an Origin, or with one off the list, is refused before the handshake."""
    return bool(origin) and origin.rstrip("/") in allowed_origins()


def client_ip(ws) -> str:
    """The phone's address: the LAST X-Forwarded-For hop, else the peer.

    Cloud Run's frontend appends the address it saw the connection come from; anything before it is whatever
    the client chose to send, so the first entry can be forged and must never be the one that is capped."""
    forwarded = ws.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if hops:
        return hops[-1]
    return ws.client.host if ws.client else "unknown"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Shop cameras (shared/docs/ops/cctv.md): watchers start only when config/runtime lists cameras (default: none).
    A failure here is logged and never stops the service."""
    from aira_live.cameras import watch as shop_cameras  # lazy: keeps the import graph of main small

    tasks: list = []
    try:
        tasks = await shop_cameras.start_if_configured()
    except Exception:
        log.exception("shop cameras: start failed; running without them")
    try:
        yield
    finally:
        if tasks:
            await shop_cameras.stop(tasks)


app = FastAPI(title="aira-live", lifespan=_lifespan)
CORS_ORIGINS = allowed_origins()  # read once at startup, like the deployed env
# POST + Content-Type only for the app's start-up camera check (aira_live.device_check); everything else is GET.
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST"],
                   allow_headers=["content-type"])
# Session -> LiveBackend for each Live connection (tests swap in a fake).
app.state.backend_factory = lambda session: AdkLiveBackend(session)


@app.get("/health")
@app.get("/healthz")
def health() -> dict:
    return {"ok": True, "mock": config.load().mock}


def _is_missing(exc: ModuleNotFoundError, module: str) -> bool:
    """True when ``module`` itself (or a parent package) is absent, not a dependency it imports."""
    return bool(exc.name) and (module == exc.name or module.startswith(exc.name + "."))


def include_optional_routers(target: FastAPI, modules: tuple[str, ...] = OPTIONAL_ROUTERS) -> list[str]:
    """Mount ``module.router`` for each module that exists; return the mounted names.

    A module that is not in the tree yet is skipped. A module that exists but fails to import raises, so a
    broken router fails the deploy instead of silently disappearing.
    """
    mounted = []
    for name in modules:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if not _is_missing(exc, name):
                raise
            log.info("optional router %s is not present", name)
            continue
        target.include_router(module.router)
        mounted.append(name)
    return mounted


# The optional routers that are actually serving (logged at startup, checked by tests).
MOUNTED_ROUTERS: list[str] = include_optional_routers(app)
log.info("mounted routers: %s", MOUNTED_ROUTERS or "none")


async def _lane_hook(module: str, name: str, *args) -> None:
    """Call ``<module>.<name>(*args)`` when that lane's module is present. Never raises."""
    try:
        mod = importlib.import_module(module)
        result = getattr(mod, name)(*args)
        if inspect.isawaitable(result):
            await result
    except ModuleNotFoundError as exc:
        if not _is_missing(exc, module):
            log.exception("%s hook %s failed", module, name)
    except Exception:
        log.exception("%s hook %s failed", module, name)


async def _guidance_hook(name: str, *args) -> None:
    """Call ``aira_live.tools.guidance.<name>(*args)`` when Sarmitha's module is present. Never raises."""
    await _lane_hook("aira_live.tools.guidance", name, *args)


async def _ambient_hook(name: str, *args) -> None:
    """Call ``aira_live.tools.ambient.<name>(*args)``: ambient mode (hold-up identification and scene
    announcements) starts with the Live session and ends with it. Never raises."""
    await _lane_hook("aira_live.tools.ambient", name, *args)


async def live_frame(session: Session, bridge: LiveBridge | None, frame: Frame, *, video: bool,
                     changed: bool = False, labels: list[str] | None = None, precount_on: bool = False) -> bool:
    """A ``live`` frame (the 1 fps camera video): cached as the session's newest frame for the tools
    (``Session.latest_frame``), then forwarded into Gemini Live when ``video`` (AIRA_LIVE_VIDEO) is on and a
    bridge runs. Never stored in the tool ring buffer and never relayed to the monitor: the dashboard has its
    own preview stream, so the same picture is never relayed twice. With ``precount_on`` (AIRA_PRECOUNT) a
    settled scene change also starts a background count (``vision.precount``). True when it reached Live."""
    session.frames.remember_recent(frame)  # look_frame / latest_frame reuse it (768 px)
    if changed:  # a new scene: earlier counts no longer describe the view (aira_live.scene_facts)
        session.scene_facts.camera_moved(frame.ts)
    if not video or bridge is None:
        return False
    if precount_on and not session.walking:  # a walk guidance owns the camera: no background count
        if changed:
            precount.scene_changed(session, frame.ts)
        else:
            precount.tick(session)
    sent = await bridge.video.offer(bridge, frame.jpeg_b64, changed=changed, labels=labels)
    if sent and session.walking:  # walk with me: Live just got a new picture (a "changed" one asks for a check)
        await _lane_hook("aira_live.tools.walk", "on_live_frame", session, changed, frame.jpeg_b64)
    return sent


def _live_nudge_on() -> bool:
    """AIRA_LIVE_VIDEO and AIRA_LIVE_NUDGE both on: Live speaks up when the camera view changes."""
    s = config.load()
    return s.live_video and s.live_nudge


async def _start_live(session: Session, resume_handle: str | None = None) -> LiveBridge | None:
    """Real mode: connect ``session`` to Gemini Live (resuming the previous Live conversation when a reconnecting
    phone brought its handle). On failure, tell the phone and return ``None``."""
    bridge: LiveBridge | None = None
    try:
        settings = config.load()
        er2 = settings.er2  # AIRA_ER2=0: Live-only test mode, no Robotics-ER tools
        walk = settings.walk  # AIRA_WALK=1: walk with me (Live-only perception), with or without ER2
        prompt = await build_system_prompt(session.shop_id, session.lang, session.verbosity,
                                           **({} if er2 else {"er2": False}), **({"walk": True} if walk else {}))
        tools = live_tools(active_tools(er2, walk), session, NON_BLOCKING_TOOLS, SILENT_TOOLS)
        bridge = LiveBridge(session, lambda: app.state.backend_factory(session), prompt, tools,
                            resume_handle=resume_handle, live_nudge=_live_nudge_on())
        await bridge.start()
    except Exception:
        log.exception("live start failed for session %s", session.session_id)
        if bridge is not None:
            await bridge.close()
        await session.send(ErrorMsg(code="network", say=line("live.unavailable", session.lang)))
        return None
    sessions.attach_bridge(session.session_id, bridge)
    return bridge


async def _end_session(session: Session, bridge: LiveBridge | None) -> None:
    """Disconnect or a replacing hello: stop alerts, guidance and Live for this session.

    The session stays resumable for ``sessions.RESUME_WINDOW_S``: a hello with ``resume=<its id>`` from the same
    phone (a network blip, a locked screen) gets the same session id and the last Live resumption handle.
    """
    sessions.unregister(session.session_id)
    sessions.remember_ended(session.session_id, bridge._handle if bridge is not None else None)
    await _guidance_hook("stop_guidance", session)
    await _ambient_hook("stop_session", session)
    precount.forget(session.session_id)
    tool_runtime.end_session(session.session_id)  # running tool calls cancelled, their results dropped
    await _lane_hook("aira_live.tools.walk", "stop_session", session)
    if bridge is not None:
        await bridge.close()
    monitor.session_ended(session)


def parse(raw: str | None, max_frame_b64: int):
    """One WebSocket message -> ``(client message, None)`` or ``(None, system_lines key of the error to speak)``."""
    if not isinstance(raw, str):  # binary frames are not part of the protocol
        return None, "error.unsupported"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, "error.unsupported"
    if not isinstance(data, dict):
        return None, "error.unsupported"
    jpeg = data.get("jpeg")
    if data.get("t") == "frame" and isinstance(jpeg, str) and len(jpeg) > max_frame_b64:
        return None, "error.too_large"  # checked before validation, so it is never stored
    try:
        return client_adapter.validate_python(data), None
    except ValidationError:
        return None, "error.unsupported"


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    origin = ws.headers.get("origin")
    if not origin_allowed(origin):  # not the AIRA site (or no Origin at all): refused before the upgrade
        log.warning("ws refused: origin %r is missing or not in AIRA_ALLOWED_ORIGINS", origin)
        await ws.close(code=4403)
        return
    await ws.accept()
    ip = client_ip(ws)
    settings = config.load()
    diag("ws", event="open", origin=origin, mock=settings.mock)
    log_limit = _log_limiter()
    boxes_limit = _boxes_limiter()
    rtc_limit = rtc.limiter()

    async def send(msg: BaseModel) -> None:
        await ws.send_text(dump_server_msg(msg))
        if session is not None:  # the dashboard's Live page sees what the phone sees (aira_live.monitor)
            monitor.mirror(session, msg)

    session: Session | None = None
    bridge: LiveBridge | None = None  # real mode only
    # The catalog for error lines before any hello: English, like every language without a catalog. Once the
    # phone says hello this is its locale hint, and after that it follows whatever the speaker last used.
    lang = "en"
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            msg, error_key = parse(message.get("text"), settings.max_frame_b64)
            if msg is None:
                await send(ErrorMsg(code="unsupported", say=line(error_key, lang)))
                continue

            if isinstance(msg, HelloMsg):
                try:  # the client's shopId is never trusted: verify_hello picks the shop
                    who = await verify_hello(msg.id_token, msg.uid, msg.shop_id, settings.mock)
                except AuthError as exc:
                    log.warning("hello refused: %s", exc)
                    await send(ErrorMsg(code="permission", say=line("auth.denied", msg.lang)))
                    await ws.close(code=4403)
                    return
                if session:  # a second hello replaces the session
                    await _end_session(session, bridge)
                    bridge = None
                elif sessions.count() >= MAX_SESSIONS or sessions.count(ip) >= MAX_SESSIONS_PER_IP:
                    log.warning("hello refused: %d live sessions, %d from %s", sessions.count(),
                                sessions.count(ip), ip)  # bounds Gemini spend on the public URL
                    await send(ErrorMsg(code="permission", say=line("auth.busy", msg.lang)))
                    await ws.close(code=4403)
                    return
                resumed, handle = sessions.take_ended(msg.resume)
                session = Session(who.uid, who.shop_id, msg.lang, msg.verbosity, send,
                                  session_id=msg.resume if resumed else None,
                                  languages=await shop_languages(who.shop_id))
                lang = msg.lang
                current_session.set(session)
                sessions.register(session, ip=ip)
                diag("hello", session=session.session_id, uid=who.uid, shop=who.shop_id, lang=msg.lang,
                     verbosity=msg.verbosity, app_version=msg.app_version, resume=bool(msg.resume),
                     resumed=resumed, mock=settings.mock, camera=is_camera_session(session))
                monitor.session_started(session)
                if not settings.mock and not is_camera_session(session):  # a shop camera only sends frames
                    bridge = await _start_live(session, handle)
                await send(ReadyMsg(session_id=session.session_id, resume=session.session_id, mock=settings.mock))
                await monitor.preview_on_join(session)  # the dashboard is watching: stream the small preview
                if bridge is not None and settings.er2:  # ambient mode (ER-based) starts after the greeting
                    await _ambient_hook("on_session_start", session)
            elif isinstance(msg, StopMsg):  # long-press: stop guidance, tool calls, proposals and the answer
                if bridge:  # first: nothing more reaches the phone, Live is told to stop
                    await bridge.hard_stop()
                if session:
                    tool_runtime.stop(session.session_id)  # in-flight tools cancelled, late results dropped
                    try:
                        await confirm.cancel_pending(session.session_id)  # no commit without a fresh yes
                    except Exception:
                        log.exception("cancelling proposals on stop failed")
                    await _guidance_hook("stop_guidance", session)
                    await _lane_hook("aira_live.tools.walk", "stop_session", session)
                await send(CueMsg(cue="done"))
            elif isinstance(msg, LogMsg):  # a phone diagnostic: logged, never answered, capped per socket
                if log_limit.allow():
                    diag("client", tag=msg.tag, data=cap_json(msg.data),
                         session=session.session_id if session else None,
                         shop=session.shop_id if session else None, dropped=log_limit.dropped)
            elif isinstance(msg, TextMsg):
                if session:  # typed text is a user turn too: AIRA follows its language
                    lang = session.follow_lang(msg.text, typed=True)  # sticky: only a clear sentence switches
                diag("transcript", session=session.session_id if session else None, role="user", typed=True,
                     lang=lang, text=trunc(msg.text), final=True)
                if session:
                    monitor.transcript(session, "owner", msg.text, final=True, lang=lang)
                if bridge:
                    await bridge.send_text(msg.text, user_turn=True)
                else:
                    await send(TranscriptMsg(role="aira", text=line("mock.echo", lang, text=msg.text), final=True,
                                             lang=session.lang if session else None))
            elif session is None:  # frames, confirmations, audio and events need a hello first
                await send(ErrorMsg(code="unsupported", say=line("error.unsupported", lang)))
            elif isinstance(msg, AudioInMsg):
                if bridge:  # mock mode has no Live session: audio is accepted and dropped
                    await bridge.send_audio(msg.pcm16)
            elif isinstance(msg, FrameMsg):
                frame = Frame(id=msg.id, jpeg_b64=msg.jpeg, w=msg.w, h=msg.h, ts=msg.ts, purpose=msg.purpose,
                              request_id=msg.request_id, capture_ts=msg.capture_ts)
                if msg.purpose == monitor.PREVIEW_PURPOSE:
                    # The monitor's own stream: relayed to the dashboard only, never stored for the tools and
                    # never shown to Gemini.
                    await monitor.publish_frame(session, frame)
                    continue
                if msg.purpose == live_video.PURPOSE:
                    await live_frame(session, bridge, frame, video=settings.live_video,
                                     changed=bool(msg.changed), labels=msg.labels, precount_on=settings.precount and settings.er2)
                    continue
                stored = session.frames.add(frame)  # "stale"/"too_old"/"duplicate": never given to a tool
                diag("frame", session=session.session_id, purpose=msg.purpose, id=msg.id, bytes=len(msg.jpeg),
                     w=msg.w, h=msg.h, request_id=msg.request_id, stored=stored)
                if stored == "duplicate":
                    continue
                await monitor.publish_frame(session, frame)  # a no-op unless a monitor watches this shop
            elif isinstance(msg, BoxesMsg):  # the phone's own boxes: relayed to the monitor, never answered
                if session:  # a walk with me in progress: the phone's detector can say "Stop." before ER does
                    await _lane_hook("aira_live.tools.walk", "on_phone_boxes", session, msg)
                if boxes_limit.allow():
                    await monitor_hooks.monitor_publish(session, monitor_hooks.vision_message(
                        session, msg.source, msg.objects, frame_id=msg.frame_id))
                    diag("boxes", session=session.session_id, source=msg.source, objects=len(msg.objects),
                         danger=sum(1 for o in msg.objects if o.severity == "danger"), dropped=boxes_limit.dropped)
            elif isinstance(msg, ConfirmReplyMsg):
                try:
                    result = await confirm.resolve(msg.id, msg.answer, msg.value)
                except Exception as exc:  # a failed commit stays pending and can be retried
                    log.exception("confirm %s failed", msg.id)
                    diag("confirm", severity="ERROR", action="resolve_failed", session=session.session_id,
                         id=msg.id, answer=msg.answer, error=type(exc).__name__, message=trunc(str(exc), 200))
                    await send(ErrorMsg(code="internal", say=line("error.internal", lang)))
                else:
                    if bridge:  # a tapped answer: Live must know, so it tells the result and never re-asks
                        facts = json.dumps(result, ensure_ascii=False, default=str)
                        await bridge.send_text(
                            f"[CONFIRMATION] The owner answered '{msg.answer}' on the phone for {msg.id}. "
                            f"Result facts: {facts}. Tell him the result briefly, in the language he is "
                            "speaking, numbers exactly as given. Do not call confirm_action for it again.",
                            user_turn=True)
                    else:  # no Live voice: the English meaning is the caption
                        await send(TranscriptMsg(role="aira", text=str(result.get("say_en", "")), final=True))
            elif isinstance(msg, EventMsg):
                monitor.client_event(session, msg.name, msg.data)
                if bridge and msg.name == "talk":  # a talk tap is the owner's next turn: a long-press stop ends
                    bridge.user_turn()
                await _guidance_hook("handle_client_event", session, msg.name, msg.data)
            elif isinstance(msg, RtcMsg):  # WebRTC signalling for a dashboard monitor: relayed, never read
                if rtc_limit.allow():
                    await rtc.from_phone(session, msg)
    except WebSocketDisconnect:
        pass
    finally:
        diag("ws", event="close", session=session.session_id if session else None,
             client_logs_dropped=log_limit.dropped, boxes_dropped=boxes_limit.dropped)
        if session:
            await _end_session(session, bridge)


@app.websocket("/monitor")
async def monitor_endpoint(ws: WebSocket) -> None:
    """``GET /monitor?shop=<id>``, then ``{t:"auth", key}``: the dashboard's read-only live feed of one shop.

    Guards, in order: the browser Origin allowlist (like ``/ws``), a ``shop`` that may be watched (in tokenless /
    demo mode only ``AIRA_DEMO_SHOP``: ``monitor.shop_allowed``) and ``monitor.MAX_MONITORS_PER_SHOP`` (each
    refusal closes with 4403 before the handshake), then the first message: ``{t:"auth", key}`` within
    ``monitor.AUTH_TIMEOUT_S``, compared in constant time to that shop's key, ``monitor.shop_key`` =
    HMAC(``AIRA_MONITOR_KEY``, shop) (with the env unset nobody gets in; a key for another shop is refused). A
    wrong, malformed or late key closes with 4403; nothing is published before it. The key is never read from the
    URL. A connected monitor first hears ``session start`` for every live session of the shop.
    """
    origin = ws.headers.get("origin")
    if not origin_allowed(origin):
        log.warning("monitor refused: origin %r is missing or not in AIRA_ALLOWED_ORIGINS", origin)
        await ws.close(code=4403)
        return
    shop = (ws.query_params.get("shop") or "").strip()
    if not shop:
        log.warning("monitor refused: no shop")
        await ws.close(code=4403)
        return
    if not monitor.shop_allowed(shop):
        log.warning("monitor refused: shop %r is not the demo shop", shop)
        await ws.close(code=4403)
        return
    if monitor.monitors(shop) >= monitor.MAX_MONITORS_PER_SHOP:
        log.warning("monitor refused: %d monitors already watch %s", monitor.monitors(shop), shop)
        await ws.close(code=4403)
        return
    await ws.accept()
    if not await monitor.authenticate(ws, shop):
        log.warning("monitor refused: bad, missing or late auth for %s", shop)
        diag("monitor", event="refused", shop=shop, origin=origin)
        await ws.close(code=4403)
        return
    sub = monitor.subscribe(shop)
    diag("monitor", event="open", shop=shop, origin=origin, monitors=monitor.monitors(shop))
    try:
        for live, _bridge in sessions.by_shop(shop):  # what is already going on
            sub.push(dump_monitor_msg(monitor_adapter.validate_python(
                {"t": "mon", "kind": "session", "session": live.session_id, "ts": int(time.time() * 1000),
                 "state": "start", **monitor.session_fields(live)})))
        await monitor.serve(ws, sub)
    except WebSocketDisconnect:
        pass
    finally:
        monitor.unsubscribe(sub)
        diag("monitor", event="close", shop=shop, monitors=monitor.monitors(shop))
