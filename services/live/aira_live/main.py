"""aira-live: the Cloud Run service the phone app talks to.

- ``GET /health`` (alias ``/healthz``) says the service is up and whether it runs in mock mode (no
  ``GEMINI_API_KEY``). Cloud Run's frontend reserves some paths that end in "z", so probes use ``/health``.
- ``WS /ws`` speaks the final protocol (``aira_live.contracts``):
  - ``hello`` is checked by ``auth.verify_hello`` (no Firebase Auth): tokenless phones always land in the demo
    shop, and with tokenless mode off the socket gets ``error/permission`` and is closed.
  - Real mode: each session gets a ``LiveBridge`` to Gemini Live (``model_for("live")``). Mic audio and typed
    text go to Live; Live's audio, captions and cues come back through the bridge.
  - Mock mode: text is echoed, and audio is accepted and dropped.
  - Both modes store frames, resolve confirmations, answer ``stop`` and pass client ``event``s and ``stop`` to
    the guidance hooks (``aira_live.tools.guidance``, imported lazily).
- Other lanes' HTTP routes are ``APIRouter``s in ``OPTIONAL_ROUTERS``, mounted when their module is present.

Review focus: an unknown or invalid message gets ``error/unsupported`` and the socket stays open; a frame
over ``max_frame_b64`` is refused and never stored; the client's ``shopId`` never picks the shop.
"""
import importlib
import inspect
import json
import logging
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

from aira_live import config, confirm, sessions
from aira_live.auth import AuthError, verify_hello
from aira_live.contracts import (
    AudioInMsg,
    ConfirmReplyMsg,
    CueMsg,
    ErrorMsg,
    EventMsg,
    FrameMsg,
    HelloMsg,
    ReadyMsg,
    StopMsg,
    TextMsg,
    TranscriptMsg,
    client_adapter,
    dump_server_msg,
)
from aira_live.frames import Frame
from aira_live.i18n import t
from aira_live.live_bridge import AdkLiveBackend, LiveBridge
from aira_live.prompt import build_system_prompt
from aira_live.session import Session, current_session
from aira_live.tool_runtime import live_tools
from aira_live.tools import NON_BLOCKING_TOOLS, TOOL_FUNCS

log = logging.getLogger(__name__)

# APIRouter modules of other lanes, mounted when present: jobs (saravana/02), WhatsApp/Telegram webhooks (kanish/02),
# the dashboard's read-only HTTP API (api).
OPTIONAL_ROUTERS: tuple[str, ...] = ("aira_live.jobs", "aira_live.messaging.webhooks", "aira_live.api")

# The service is public (--allow-unauthenticated) and runs on one instance: this many phones at once is plenty
# for the one-shop demo and bounds what a stranger with the URL can spend on Gemini Live.
MAX_SESSIONS = 3

# Browser origins allowed to call the HTTP API (the dashboard's forecast card). The Firebase Hosting site plus
# any extra origins in env AIRA_CORS_ORIGINS (comma-separated, e.g. a local Vite dev server).
HOSTING_ORIGINS = ("https://aira-shop.web.app", "https://aira-shop.firebaseapp.com")


def cors_origins() -> list[str]:
    extra = [o.strip().rstrip("/") for o in os.getenv("AIRA_CORS_ORIGINS", "").split(",") if o.strip()]
    return [*HOSTING_ORIGINS, *extra]


app = FastAPI(title="aira-live")
app.add_middleware(CORSMiddleware, allow_origins=cors_origins(), allow_methods=["GET"], allow_headers=[])
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


async def _guidance_hook(name: str, *args) -> None:
    """Call ``aira_live.tools.guidance.<name>(*args)`` when Sarmitha's module is present. Never raises."""
    module = "aira_live.tools.guidance"
    try:
        guidance = importlib.import_module(module)
        result = getattr(guidance, name)(*args)
        if inspect.isawaitable(result):
            await result
    except ModuleNotFoundError as exc:
        if not _is_missing(exc, module):
            log.exception("guidance hook %s failed", name)
    except Exception:
        log.exception("guidance hook %s failed", name)


async def _start_live(session: Session, resume_handle: str | None = None) -> LiveBridge | None:
    """Real mode: connect ``session`` to Gemini Live (resuming the previous Live conversation when a reconnecting
    phone brought its handle). On failure, tell the phone and return ``None``."""
    bridge: LiveBridge | None = None
    try:
        prompt = await build_system_prompt(session.shop_id, session.lang, session.verbosity)
        tools = live_tools(TOOL_FUNCS, session, NON_BLOCKING_TOOLS)
        bridge = LiveBridge(session, lambda: app.state.backend_factory(session), prompt, tools,
                            resume_handle=resume_handle)
        await bridge.start()
    except Exception:
        log.exception("live start failed for session %s", session.session_id)
        if bridge is not None:
            await bridge.close()
        await session.send(ErrorMsg(code="network", say=t("live.unavailable", session.lang)))
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
    if bridge is not None:
        await bridge.close()


def parse(raw: str | None, max_frame_b64: int):
    """One WebSocket message -> ``(client message, None)`` or ``(None, i18n key of the error to speak)``."""
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
    await ws.accept()
    settings = config.load()

    async def send(msg: BaseModel) -> None:
        await ws.send_text(dump_server_msg(msg))

    session: Session | None = None
    bridge: LiveBridge | None = None  # real mode only
    lang = "en"
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            msg, error_key = parse(message.get("text"), settings.max_frame_b64)
            if msg is None:
                await send(ErrorMsg(code="unsupported", say=t(error_key, lang)))
                continue

            if isinstance(msg, HelloMsg):
                try:  # the client's shopId is never trusted: verify_hello picks the shop
                    who = await verify_hello(msg.id_token, msg.uid, msg.shop_id, settings.mock)
                except AuthError as exc:
                    log.warning("hello refused: %s", exc)
                    await send(ErrorMsg(code="permission", say=t("auth.denied", msg.lang)))
                    await ws.close(code=4403)
                    return
                if session:  # a second hello replaces the session
                    await _end_session(session, bridge)
                    bridge = None
                elif sessions.count() >= MAX_SESSIONS:  # bound Gemini spend on the public URL
                    log.warning("hello refused: %d live sessions already", sessions.count())
                    await send(ErrorMsg(code="permission", say=t("auth.busy", msg.lang)))
                    await ws.close(code=4403)
                    return
                resumed, handle = sessions.take_ended(msg.resume)
                session = Session(who.uid, who.shop_id, msg.lang, msg.verbosity, send,
                                  session_id=msg.resume if resumed else None)
                lang = msg.lang
                current_session.set(session)
                sessions.register(session)
                if not settings.mock:
                    bridge = await _start_live(session, handle)
                await send(ReadyMsg(session_id=session.session_id, resume=session.session_id, mock=settings.mock))
            elif isinstance(msg, StopMsg):  # long-press: stop guidance and the answer being spoken
                if session:
                    await _guidance_hook("stop_guidance", session)
                if bridge:
                    bridge.hard_stop()
                await send(CueMsg(cue="done"))
            elif isinstance(msg, TextMsg):
                if bridge:
                    await bridge.send_text(msg.text)
                else:
                    await send(TranscriptMsg(role="aira", text=t("mock.echo", lang, text=msg.text), final=True))
            elif session is None:  # frames, confirmations, audio and events need a hello first
                await send(ErrorMsg(code="unsupported", say=t("error.unsupported", lang)))
            elif isinstance(msg, AudioInMsg):
                if bridge:  # mock mode has no Live session: audio is accepted and dropped
                    await bridge.send_audio(msg.pcm16)
            elif isinstance(msg, FrameMsg):
                session.frames.add(Frame(id=msg.id, jpeg_b64=msg.jpeg, w=msg.w, h=msg.h, ts=msg.ts,
                                         purpose=msg.purpose))
            elif isinstance(msg, ConfirmReplyMsg):
                try:
                    result = await confirm.resolve(msg.id, msg.answer, msg.value)
                except Exception:  # a failed commit stays pending and can be retried
                    log.exception("confirm %s failed", msg.id)
                    await send(ErrorMsg(code="internal", say=t("error.internal", lang)))
                else:
                    say = result.get("say", "")
                    await send(TranscriptMsg(role="aira", text=say, final=True))
                    if bridge:  # a tapped answer: Live must know, so it speaks the result and never re-asks
                        await bridge.send_text(
                            f"[CONFIRMATION] The owner answered '{msg.answer}' on the phone for {msg.id}. "
                            f"Result: {say}. Do not call confirm_action for it again.")
            elif isinstance(msg, EventMsg):
                await _guidance_hook("handle_client_event", session, msg.name, msg.data)
    except WebSocketDisconnect:
        pass
    finally:
        if session:
            await _end_session(session, bridge)
