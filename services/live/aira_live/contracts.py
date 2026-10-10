"""Pydantic mirror of packages/contracts/src/messages.ts and enums.ts (the WebSocket protocol).

Keep this file, the TS files and packages/contracts/fixtures in lockstep: change all three in one PR.
tests/core/test_contracts.py validates the shared fixtures and compares every enum with enums.ts.

- Python attributes are snake_case; the wire is camelCase (``shop_id`` <-> ``shopId``).
- Client messages accept camelCase keys only, exactly like the TS guard ``isClientMsg``.
- Server messages are built in Python by attribute name: ``ReadyMsg(session_id=..., resume=..., mock=True)``.
- Scalars are strict, like the TS guards: ``"3"`` is not an int, ``"true"`` is not a bool, numbers are finite.
- Send server messages with ``dump_server_msg(msg)``: camelCase, unset optional fields omitted,
  and ``count.expected`` kept even when it is null.
"""
from __future__ import annotations

import typing
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    AllowInfNan,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    Strict,
    StrictBool,
    StrictInt,
    StrictStr,
    TypeAdapter,
    ValidationError,
    model_serializer,
)
from pydantic.alias_generators import to_camel

# ---------- enums (packages/contracts/src/enums.ts) ----------
Lang = Literal["ta", "hi", "en"]
Verbosity = Literal["short", "detail"]
FramePurpose = Literal["ask", "count", "point", "identify", "invoice", "cash", "guide"]
CueName = Literal["ack", "thinking", "done", "warn", "match", "mismatch", "unsure", "interrupted", "reconnect", "offline"]
Entity = Literal["order", "delivery", "sale", "payment", "inventory"]
ConfirmKind = Literal["order", "payment", "sale", "discrepancy", "generic"]
ConfirmAnswer = Literal["yes", "no"]
ClientEventName = Literal["visibility", "network", "fingertip_in_target"]
TranscriptRole = Literal["user", "aira"]
ErrorCode = Literal["network", "model_timeout", "camera", "permission", "unsupported", "internal"]

# Spatial coordinates: point [y, x], box [ymin, xmin, ymax, xmax], integers 0..NORM_MAX.
NORM_MAX = 1000
Norm = Annotated[StrictInt, Field(ge=0, le=NORM_MAX)]
Point = tuple[Norm, Norm]
Box = tuple[Norm, Norm, Norm, Norm]

# A finite JSON number (TS `isNum`). Integers stay ints so payloads round-trip unchanged.
Num = Union[StrictInt, Annotated[float, Strict(), AllowInfNan(False)]]


class _Wire(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="ignore",
    )


class _ClientWire(_Wire):
    """Inbound only: camelCase keys, as the TS guard requires."""

    model_config = ConfigDict(validate_by_name=False)


class _ServerWire(_Wire):
    """Outbound: built in Python by attribute name, dumped as camelCase."""

    model_config = ConfigDict(validate_by_name=True)


# ---------- client -> server ----------
class HelloMsg(_ClientWire):
    t: Literal["hello"]
    uid: StrictStr
    id_token: Optional[StrictStr] = None  # unused: no Firebase Auth (server checks AIRA_ALLOW_NO_TOKEN)
    shop_id: StrictStr
    lang: Lang
    verbosity: Verbosity
    app_version: StrictStr
    resume: Optional[StrictStr] = None


class AudioInMsg(_ClientWire):
    t: Literal["audio"]
    pcm16: StrictStr  # 16 kHz mono PCM16, base64


class FrameMsg(_ClientWire):
    t: Literal["frame"]
    id: StrictStr
    jpeg: StrictStr  # base64 JPEG
    w: StrictInt
    h: StrictInt
    ts: Num  # epoch ms
    purpose: FramePurpose


class TextMsg(_ClientWire):
    t: Literal["text"]
    text: StrictStr


class EventMsg(_ClientWire):
    t: Literal["event"]
    name: ClientEventName
    data: dict[str, Any]


class ConfirmReplyMsg(_ClientWire):
    t: Literal["confirm"]
    id: StrictStr
    answer: ConfirmAnswer
    value: Optional[StrictStr] = None


class StopMsg(_ClientWire):
    t: Literal["stop"]


ClientMsg = Annotated[
    Union[HelloMsg, AudioInMsg, FrameMsg, TextMsg, EventMsg, ConfirmReplyMsg, StopMsg],
    Field(discriminator="t"),
]


# ---------- server -> client ----------
class ReadyMsg(_ServerWire):
    t: Literal["ready"] = "ready"
    session_id: StrictStr
    resume: StrictStr
    mock: StrictBool


class AudioOutMsg(_ServerWire):
    t: Literal["audio"] = "audio"
    pcm24: StrictStr  # 24 kHz mono PCM16, base64


class TranscriptMsg(_ServerWire):
    t: Literal["transcript"] = "transcript"
    role: TranscriptRole
    text: StrictStr
    final: StrictBool


class Burst(_ServerWire):
    count: StrictInt
    interval_ms: StrictInt


class RequestFrameMsg(_ServerWire):
    t: Literal["request_frame"] = "request_frame"
    purpose: FramePurpose
    burst: Optional[Burst] = None
    continuous: Optional[StrictBool] = None
    stop: Optional[StrictBool] = None


class TargetMsg(_ServerWire):
    t: Literal["target"] = "target"
    frame_id: StrictStr
    label: StrictStr
    point: Point
    box: Optional[Box] = None
    confidence: Num


class CountMsg(_ServerWire):
    t: Literal["count"] = "count"
    sku: StrictStr
    counted: StrictInt
    expected: Optional[Num]  # required; null means "no expected number"
    final: StrictBool
    confidence: Num

    @model_serializer(mode="wrap")
    def _keep_null_expected(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # `expected: null` is meaningful on the wire, so exclude_none must not drop it.
        data = handler(self)
        data.setdefault("expected", self.expected)
        return data


class StateMsg(_ServerWire):
    t: Literal["state"] = "state"
    entity: Entity
    id: StrictStr
    state: StrictStr
    summary: StrictStr  # "OPEN_URL <url>" asks the app to open a click-to-chat / tel: link


class ConfirmMsg(_ServerWire):
    t: Literal["confirm"] = "confirm"
    id: StrictStr
    kind: ConfirmKind
    prompt: StrictStr


class CueMsg(_ServerWire):
    t: Literal["cue"] = "cue"
    cue: CueName


class ErrorMsg(_ServerWire):
    t: Literal["error"] = "error"
    code: ErrorCode
    say: StrictStr


ServerMsg = Annotated[
    Union[ReadyMsg, AudioOutMsg, TranscriptMsg, RequestFrameMsg, TargetMsg, CountMsg, StateMsg, ConfirmMsg, CueMsg,
          ErrorMsg],
    Field(discriminator="t"),
]

client_adapter: TypeAdapter[ClientMsg] = TypeAdapter(ClientMsg)
server_adapter: TypeAdapter[ServerMsg] = TypeAdapter(ServerMsg)


def _tags(union: Any) -> tuple[str, ...]:
    members = typing.get_args(typing.get_args(union)[0])
    return tuple(typing.get_args(m.model_fields["t"].annotation)[0] for m in members)


CLIENT_MSG_TYPES: tuple[str, ...] = _tags(ClientMsg)
SERVER_MSG_TYPES: tuple[str, ...] = _tags(ServerMsg)


def parse_client_msg(raw: str | bytes) -> HelloMsg | AudioInMsg | FrameMsg | TextMsg | EventMsg | ConfirmReplyMsg | StopMsg | None:
    """Parse one WebSocket text frame from the app. None for bad JSON or an unknown/invalid message."""
    try:
        return client_adapter.validate_json(raw)
    except ValidationError:
        return None


def parse_server_msg(raw: str | bytes) -> (
    ReadyMsg | AudioOutMsg | TranscriptMsg | RequestFrameMsg | TargetMsg | CountMsg | StateMsg | ConfirmMsg | CueMsg
    | ErrorMsg | None
):
    """Parse one server WebSocket text frame (used by tests and dev tools). None when invalid."""
    try:
        return server_adapter.validate_json(raw)
    except ValidationError:
        return None


def dump_server_msg(msg: BaseModel) -> str:
    """Serialize a server message for ``ws.send_text``: camelCase, unset optional fields omitted."""
    return server_adapter.dump_json(msg, by_alias=True, exclude_none=True).decode()
