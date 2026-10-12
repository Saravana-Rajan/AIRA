"""Pydantic mirror of shared/contracts/src/messages.ts and enums.ts (the WebSocket protocol).

Keep this file, the TS files and shared/contracts/fixtures in lockstep: change all three in one PR.
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

# ---------- enums (shared/contracts/src/enums.ts) ----------
Lang = Literal["ta", "hi", "en"]
Verbosity = Literal["short", "detail"]
FramePurpose = Literal["ask", "count", "point", "identify", "invoice", "cash", "guide", "describe"]
CueName = Literal["ack", "thinking", "done", "warn", "match", "mismatch", "unsure", "interrupted", "reconnect", "offline"]
Entity = Literal["order", "delivery", "sale", "payment", "inventory"]
ConfirmKind = Literal["order", "payment", "sale", "discrepancy", "generic"]
ConfirmAnswer = Literal["yes", "no"]
ClientEventName = Literal["visibility", "network", "fingertip_in_target"]
TranscriptRole = Literal["user", "aira"]
ErrorCode = Literal["network", "model_timeout", "camera", "permission", "unsupported", "internal"]
# Who drew a box: the phone's on-device MediaPipe detector, Gemini Robotics-ER 2, or the phone's bump guard.
BoxSource = Literal["mediapipe", "er2", "bump"]
BoxSeverity = Literal["danger"]

# Spatial coordinates: point [y, x], box [ymin, xmin, ymax, xmax], integers 0..NORM_MAX.
NORM_MAX = 1000
Norm = Annotated[StrictInt, Field(ge=0, le=NORM_MAX)]
Point = tuple[Norm, Norm]
Box = tuple[Norm, Norm, Norm, Norm]
# A ``boxes`` message carries at most this many objects (the detectors return 8; ER 2 at most a dozen).
MAX_BOXES = 32

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


class LogMsg(_ClientWire):
    """A diagnostic line from the phone, written to Cloud Logging by ``aira_live.diag`` as ``aira="client"``.

    ``tag`` names what happened ("build", "camera", "tilt", "frame", "hint", "speak"); ``data`` is small
    metadata, never audio or image bytes. The server keeps at most 2 KB of it and 20 lines a second per socket.
    """

    t: Literal["log"]
    tag: StrictStr
    data: dict[str, Any]


class BoxObject(_Wire):
    """One drawn object: a label, its box [ymin, xmin, ymax, xmax] (0-1000), an optional detector score (0-1)
    and ``severity: "danger"`` when the bump guard flags it in the walking path."""

    label: StrictStr
    box: Box
    score: Optional[Num] = None
    severity: Optional[BoxSeverity] = None


class BoxesMsg(_ClientWire):
    """Boxes the phone drew itself (MediaPipe on-device, at most 5 a second; the bump guard's obstacles).

    The server never answers it: it is relayed to the live monitor (``aira_live.monitor_hooks``) as
    kind ``vision`` and dropped above the per-socket rate limit. Nothing here changes state.
    """

    t: Literal["boxes"]
    source: BoxSource
    objects: list[BoxObject] = Field(max_length=MAX_BOXES)
    frame_id: Optional[StrictStr] = None


ClientMsg = Annotated[
    Union[HelloMsg, AudioInMsg, FrameMsg, TextMsg, EventMsg, ConfirmReplyMsg, StopMsg, LogMsg, BoxesMsg],
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
    # The language AIRA detected from the speaker's last finished utterance (``i18n.detect_lang``: "ta", "ja",
    # "en", ...). The phone's labels follow it. Absent on partial transcripts and before anyone has spoken.
    lang: Optional[StrictStr] = None
    # True on a finished user transcript that looks like a language the shop does not usually speak (shop doc
    # ``languages``): probably misheard, so AIRA asks to repeat instead of answering it. The caption marks it.
    unclear: Optional[StrictBool] = None


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


class BoxesOutMsg(_ServerWire):
    """Boxes Gemini Robotics-ER 2 drew (source ``er2``) for the phone's optional "Show boxes" overlay: the same
    shape as the client message, plus the label being looked for (``target``, hand guidance) and whether it
    was ``found`` (drawn green). Never spoken; the phone draws it or ignores it."""

    t: Literal["boxes"] = "boxes"
    source: BoxSource
    objects: list[BoxObject] = Field(max_length=MAX_BOXES)
    frame_id: Optional[StrictStr] = None
    target: Optional[StrictStr] = None
    found: Optional[StrictBool] = None


ServerMsg = Annotated[
    Union[ReadyMsg, AudioOutMsg, TranscriptMsg, RequestFrameMsg, TargetMsg, CountMsg, StateMsg, ConfirmMsg, CueMsg,
          ErrorMsg, BoxesOutMsg],
    Field(discriminator="t"),
]

# ---------- server -> monitor (GET /monitor?shop=<id>, then {t:"auth", key}; read-only) ----------
# Mirrors the MonitorMsg union in messages.ts and shared/contracts/fixtures/monitor_msgs.json.
# Every message is {t:"mon", kind, session, ts, ...}; ``jpeg_b64`` keeps its snake_case name on the wire.
MonitorRole = Literal["owner", "customer", "aira"]
MonitorSessionState = Literal["start", "end"]
# Who drew the boxes of a vision message: the phone's on-device MediaPipe detector, Gemini Robotics-ER 2, or
# the phone's bump guard (always severity "danger"). ``context`` says which ER 2 prompt it was.
VisionSource = BoxSource
VisionDistance = Literal["near", "arm", "far"]
MonitorState = Literal["listening", "thinking", "guiding", "found", "idle"]


class MonAuthMsg(_ClientWire):
    """The monitor's first (and only) message: the key, compared in constant time to AIRA_MONITOR_KEY."""

    t: Literal["auth"]
    key: StrictStr


monitor_client_adapter: TypeAdapter[MonAuthMsg] = TypeAdapter(MonAuthMsg)


def parse_monitor_client_msg(raw: str | bytes) -> MonAuthMsg | None:
    """Parse one text frame a monitor sent. None for bad JSON or an unknown/invalid message."""
    try:
        return monitor_client_adapter.validate_json(raw)
    except (ValidationError, ValueError):
        return None


class _MonWire(_ServerWire):
    t: Literal["mon"] = "mon"
    session: StrictStr
    ts: Num  # epoch ms


class MonSessionMsg(_MonWire):
    kind: Literal["session"] = "session"
    state: MonitorSessionState
    lang: StrictStr
    uid: Optional[StrictStr] = None  # "camera:<name>" marks a room camera (aira_live.cameras.sources)


class MonTranscriptMsg(_MonWire):
    kind: Literal["transcript"] = "transcript"
    role: MonitorRole
    text: StrictStr
    lang: StrictStr
    final: StrictBool


class MonFrameMsg(_MonWire):
    kind: Literal["frame"] = "frame"
    jpeg_b64: StrictStr = Field(alias="jpeg_b64")  # <= 480 px wide, quality 0.6, <= 4 fps a session
    w: StrictInt
    h: StrictInt
    purpose: FramePurpose


class VisionObject(_ServerWire):
    label: StrictStr
    box: Optional[Box]  # required; null means "no box"
    point: Optional[Point]  # required; null means "no point"
    score: Optional[Num] = None  # detector score or ER 2 confidence, 0-1
    severity: Optional[BoxSeverity] = None  # "danger": the bump guard flagged it in the walking path
    distance: Optional[VisionDistance] = None

    @model_serializer(mode="wrap")
    def _keep_nulls(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        data.setdefault("box", self.box)
        data.setdefault("point", self.point)
        return data


class MonVisionMsg(_MonWire):
    kind: Literal["vision"] = "vision"
    source: VisionSource
    objects: list[VisionObject]
    target: Optional[StrictStr] = None
    found: Optional[StrictBool] = None
    severity: Optional[BoxSeverity] = None  # "danger" when any object is
    context: Optional[StrictStr] = None  # the ER 2 prompt: camera_ask, describe, camera_list, product_find, guidance
    frame_id: Optional[StrictStr] = None


class MonHandMsg(_MonWire):
    kind: Literal["hand"] = "hand"
    point: Point
    near: StrictBool


class MonEventMsg(_MonWire):
    kind: Literal["event"] = "event"
    name: StrictStr
    detail: dict[str, Any]  # small metadata (tool name, confirm id, target label); never audio or image bytes


class MonStatusMsg(_MonWire):
    kind: Literal["status"] = "status"
    state: MonitorState


class MonStatsMsg(_MonWire):
    """Session counters, every 2 s per live session: Live API turns, ER 2 and Flash calls, the average vision
    latency (ms, null before the first call) and the phone's frame rate over the last window."""

    kind: Literal["stats"] = "stats"
    turns: StrictInt
    er2_calls: StrictInt
    flash_calls: StrictInt
    vision_ms: Optional[StrictInt]  # required; null before the first vision call
    fps: Num

    @model_serializer(mode="wrap")
    def _keep_null_vision_ms(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        data.setdefault("visionMs", self.vision_ms)
        return data


MonitorMsg = Annotated[
    Union[MonSessionMsg, MonTranscriptMsg, MonFrameMsg, MonVisionMsg, MonHandMsg, MonEventMsg, MonStatusMsg,
          MonStatsMsg],
    Field(discriminator="kind"),
]
monitor_adapter: TypeAdapter[MonitorMsg] = TypeAdapter(MonitorMsg)
MONITOR_KINDS: tuple[str, ...] = ("session", "transcript", "frame", "vision", "hand", "event", "status", "stats")


def dump_monitor_msg(msg: BaseModel) -> str:
    """Serialize a monitor message for ``ws.send_text``: camelCase, unset optional fields omitted."""
    return monitor_adapter.dump_json(msg, by_alias=True, exclude_none=True).decode()


def parse_monitor_msg(raw: str | bytes) -> (
    MonSessionMsg | MonTranscriptMsg | MonFrameMsg | MonVisionMsg | MonHandMsg | MonEventMsg | MonStatusMsg
    | MonStatsMsg | None
):
    """Parse one /monitor text frame. None for bad JSON or an unknown/invalid message."""
    try:
        return monitor_adapter.validate_json(raw)
    except (ValidationError, ValueError):
        return None


client_adapter: TypeAdapter[ClientMsg] = TypeAdapter(ClientMsg)
server_adapter: TypeAdapter[ServerMsg] = TypeAdapter(ServerMsg)


def _tags(union: Any) -> tuple[str, ...]:
    members = typing.get_args(typing.get_args(union)[0])
    return tuple(typing.get_args(m.model_fields["t"].annotation)[0] for m in members)


CLIENT_MSG_TYPES: tuple[str, ...] = _tags(ClientMsg)
SERVER_MSG_TYPES: tuple[str, ...] = _tags(ServerMsg)


def parse_client_msg(raw: str | bytes) -> (
    HelloMsg | AudioInMsg | FrameMsg | TextMsg | EventMsg | ConfirmReplyMsg | StopMsg | LogMsg | BoxesMsg | None
):
    """Parse one WebSocket text frame from the app. None for bad JSON or an unknown/invalid message."""
    try:
        return client_adapter.validate_json(raw)
    except ValidationError:
        return None


def parse_server_msg(raw: str | bytes) -> (
    ReadyMsg | AudioOutMsg | TranscriptMsg | RequestFrameMsg | TargetMsg | CountMsg | StateMsg | ConfirmMsg | CueMsg
    | ErrorMsg | BoxesOutMsg | None
):
    """Parse one server WebSocket text frame (used by tests and dev tools). None when invalid."""
    try:
        return server_adapter.validate_json(raw)
    except ValidationError:
        return None


def dump_server_msg(msg: BaseModel) -> str:
    """Serialize a server message for ``ws.send_text``: camelCase, unset optional fields omitted."""
    return server_adapter.dump_json(msg, by_alias=True, exclude_none=True).decode()

