"""aira-live: the Cloud Run service the phone app talks to.

- ``GET /healthz`` reports whether the service runs in mock mode (no ``GEMINI_API_KEY``).
- ``WS /ws`` speaks the final protocol (``aira_live.contracts``). In mock mode it echoes text, stores frames,
  resolves confirmations and answers ``stop``. ``saravana/01`` plugs Gemini Live in behind the same socket.

Review focus: an unknown or invalid message gets ``error/unsupported`` and the socket stays open; a frame
over ``max_frame_b64`` is refused and never stored.
"""
import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from aira_live import config, confirm, sessions
from aira_live.contracts import (
    ConfirmReplyMsg,
    CueMsg,
    ErrorMsg,
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
from aira_live.session import Session, current_session

log = logging.getLogger(__name__)

app = FastAPI(title="aira-live")


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "mock": config.load().mock}


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
                if session:  # a second hello replaces the session
                    sessions.unregister(session)
                session = Session(msg.uid, msg.shop_id, msg.lang, msg.verbosity, send)
                lang = msg.lang
                current_session.set(session)
                sessions.register(session)
                await send(ReadyMsg(session_id=session.session_id, resume=session.session_id, mock=settings.mock))
            elif isinstance(msg, StopMsg):
                await send(CueMsg(cue="done"))
            elif isinstance(msg, TextMsg):
                await send(TranscriptMsg(role="aira", text=t("mock.echo", lang, text=msg.text), final=True))
            elif session is None:  # frames, confirmations, audio and events need a hello first
                await send(ErrorMsg(code="unsupported", say=t("error.unsupported", lang)))
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
                    await send(TranscriptMsg(role="aira", text=result.get("say", ""), final=True))
            # audio and event: accepted; mock mode has no Live session to forward them to (saravana/01).
    except WebSocketDisconnect:
        pass
    finally:
        if session:
            sessions.unregister(session)
