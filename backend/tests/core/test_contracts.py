"""aira_live.contracts must accept exactly what shared/contracts accepts.

Both sides validate the SAME fixture files (shared/contracts/fixtures), and the enum
lists are compared against shared/contracts/src/enums.ts, so the two can't drift apart.
"""
import json
import re
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from aira_live import contracts
from aira_live.contracts import (
    CountMsg,
    ReadyMsg,
    RequestFrameMsg,
    TargetMsg,
    Burst,
    client_adapter,
    dump_server_msg,
    parse_client_msg,
    parse_server_msg,
    server_adapter,
)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "shared" / "contracts" / "fixtures").is_dir():
            return parent
    raise RuntimeError("shared/contracts/fixtures not found above this test")


ROOT = _repo_root()
FIX = ROOT / "shared" / "contracts" / "fixtures"
ENUMS_TS = ROOT / "shared" / "contracts" / "src" / "enums.ts"
MESSAGES_TS = ROOT / "shared" / "contracts" / "src" / "messages.ts"


def load(name: str) -> list[dict]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def ts_list(source: Path, const: str) -> list[str]:
    m = re.search(rf"export const {const} = Object\.freeze\(\[(.*?)\]", source.read_text(encoding="utf-8"), re.S)
    assert m, f"{const} not found in {source.name}"
    return re.findall(r'"([^"]+)"', m.group(1))


CLIENT = load("client_msgs.json")
SERVER = load("server_msgs.json")


# ---------- fixtures ----------
def test_every_client_fixture_validates_and_covers_all_types():
    seen = {client_adapter.validate_python(m).t for m in CLIENT}
    assert seen == set(ts_list(MESSAGES_TS, "CLIENT_MSG_TYPES"))
    assert seen == {"hello", "audio", "frame", "text", "event", "confirm", "stop", "log", "boxes"}


def test_every_server_fixture_validates_and_covers_all_types():
    seen = {server_adapter.validate_python(m).t for m in SERVER}
    assert seen == set(ts_list(MESSAGES_TS, "SERVER_MSG_TYPES"))
    assert seen == {"ready", "audio", "transcript", "request_frame", "target", "count", "state", "confirm", "cue", "error",
                    "boxes"}


def test_type_tuples_match_ts():
    assert contracts.CLIENT_MSG_TYPES == tuple(ts_list(MESSAGES_TS, "CLIENT_MSG_TYPES"))
    assert contracts.SERVER_MSG_TYPES == tuple(ts_list(MESSAGES_TS, "SERVER_MSG_TYPES"))


@pytest.mark.parametrize("raw", CLIENT, ids=lambda m: m["t"])
def test_client_fixture_round_trips_unchanged(raw):
    msg = client_adapter.validate_json(json.dumps(raw))
    assert json.loads(client_adapter.dump_json(msg, by_alias=True, exclude_none=True)) == raw


@pytest.mark.parametrize("raw", SERVER, ids=lambda m: m["t"])
def test_server_fixture_round_trips_unchanged(raw):
    msg = parse_server_msg(json.dumps(raw))
    assert msg is not None
    assert json.loads(dump_server_msg(msg)) == raw


def test_client_log_line_carries_a_tag_and_a_data_object():
    msg = client_adapter.validate_python({"t": "log", "tag": "tilt", "data": {"beta": 131.2, "reason": "speak"}})
    assert msg.t == "log" and msg.tag == "tilt" and msg.data == {"beta": 131.2, "reason": "speak"}
    assert parse_client_msg('{"t":"log","tag":"build","data":{}}').data == {}


def test_hello_is_valid_without_id_token():
    hello = client_adapter.validate_python(
        {"t": "hello", "uid": "u1", "shopId": "sunrise_dairy", "lang": "ta", "verbosity": "short", "appVersion": "0.1.0"}
    )
    assert hello.id_token is None and hello.shop_id == "sunrise_dairy" and hello.app_version == "0.1.0"


# ---------- enums mirror enums.ts ----------
@pytest.mark.parametrize(
    "alias, ts_const",
    [
        ("Lang", "LANGS"),
        ("Verbosity", "VERBOSITIES"),
        ("FramePurpose", "FRAME_PURPOSES"),
        ("CueName", "CUE_NAMES"),
        ("Entity", "ENTITIES"),
        ("ConfirmKind", "CONFIRM_KINDS"),
        ("ConfirmAnswer", "CONFIRM_ANSWERS"),
        ("ClientEventName", "CLIENT_EVENT_NAMES"),
        ("TranscriptRole", "TRANSCRIPT_ROLES"),
        ("ErrorCode", "ERROR_CODES"),
        ("BoxSource", "BOX_SOURCES"),
        ("BoxSeverity", "BOX_SEVERITIES"),
    ],
)
def test_literal_matches_ts_enum(alias, ts_const):
    assert typing.get_args(getattr(contracts, alias)) == tuple(ts_list(ENUMS_TS, ts_const))


def test_norm_max_matches_ts():
    m = re.search(r"export const NORM_MAX = (\d+);", ENUMS_TS.read_text(encoding="utf-8"))
    assert m and int(m.group(1)) == contracts.NORM_MAX == 1000


# ---------- rejections (same cases as shared/contracts/test/fixtures.test.ts) ----------
BAD_CLIENT = [
    {"t": "nope"},
    {"t": "frame", "id": "f", "jpeg": "x", "w": 1, "h": 1, "ts": 1, "purpose": "selfie"},
    {"t": "hello", "uid": "u", "lang": "ta", "verbosity": "short", "appVersion": "0"},
    {"t": "hello", "uid": "u", "shopId": "s", "lang": "fr", "verbosity": "short", "appVersion": "0"},
    {"t": "hello", "uid": "u", "shopId": "s", "lang": "ta", "verbosity": "short", "appVersion": "0", "idToken": 5},
    {"t": "confirm", "id": "c", "answer": "maybe"},
    {"t": "event", "name": "shake", "data": {}},
    {"t": "event", "name": "network", "data": None},
    {"t": "audio"},
    {"t": "text"},
    {"t": "frame", "id": "f", "jpeg": "x", "w": 1.5, "h": 1, "ts": 1, "purpose": "count"},
    {"t": "frame", "id": "f", "jpeg": "x", "w": "640", "h": 1, "ts": 1, "purpose": "count"},
    {"t": "confirm", "id": "c1", "kind": "order", "prompt": "?"},
    {"t": "log", "tag": 5, "data": {}},
    {"t": "log", "tag": "tilt", "data": []},
    {"t": "log", "tag": "tilt"},
    # the wire is camelCase only, as in the TS guard
    {"t": "hello", "uid": "u", "shop_id": "s", "lang": "ta", "verbosity": "short", "app_version": "0"},
    # boxes: a known source, well-formed objects (box inside 0-1000, finite score, severity only "danger")
    {"t": "boxes", "source": "yolo", "objects": []},
    {"t": "boxes", "source": "mediapipe"},
    {"t": "boxes", "source": "mediapipe", "objects": {}},
    {"t": "boxes", "source": "mediapipe", "objects": [{"label": "chair"}]},
    {"t": "boxes", "source": "mediapipe", "objects": [{"label": "chair", "box": [0, 0, 1001, 10]}]},
    {"t": "boxes", "source": "mediapipe", "objects": [{"label": "chair", "box": [0, 0, 10]}]},
    {"t": "boxes", "source": "mediapipe", "objects": [{"label": "chair", "box": [0, 0, 10.5, 10]}]},
    {"t": "boxes", "source": "mediapipe", "objects": [{"label": 5, "box": [0, 0, 10, 10]}]},
    {"t": "boxes", "source": "mediapipe", "objects": [{"label": "chair", "box": [0, 0, 10, 10], "score": "0.9"}]},
    {"t": "boxes", "source": "mediapipe", "objects": [{"label": "chair", "box": [0, 0, 10, 10], "severity": "warn"}]},
    {"t": "boxes", "source": "mediapipe", "objects": [{"label": "chair", "box": [0, 0, 10, 10]}] * 33},
]

BAD_SERVER = [
    {"t": "future_thing"},
    {"t": "target", "frameId": "f", "label": "x", "point": [1200, 5], "confidence": 1},
    {"t": "target", "frameId": "f", "label": "x", "point": [5], "confidence": 1},
    {"t": "target", "frameId": "f", "label": "x", "point": [5, 5], "box": [0, 0, 1001, 10], "confidence": 1},
    {"t": "target", "frameId": "f", "label": "x", "point": [5.5, 5], "confidence": 1},
    {"t": "target", "frameId": "f", "label": "x", "point": [-1, 5], "confidence": 1},
    {"t": "cue", "cue": "boom"},
    {"t": "count", "sku": "s", "counted": 3, "final": False, "confidence": 0.5},
    {"t": "count", "sku": "s", "counted": 3, "expected": None, "final": False, "confidence": True},
    {"t": "request_frame", "purpose": "count", "burst": {"count": 3}},
    {"t": "request_frame", "purpose": "count", "continuous": "yes"},
    {"t": "state", "entity": "customer", "id": "x", "state": "S", "summary": ""},
    {"t": "error", "code": "oops", "say": "x"},
    {"t": "ready", "sessionId": "s", "resume": "s", "mock": "true"},
    {"t": "audio", "pcm16": "AAAA"},
    {"t": "confirm", "id": "c1", "answer": "yes"},
    {"t": "boxes", "source": "lidar", "objects": []},
    {"t": "boxes", "source": "er2", "objects": [{"label": "x", "box": [0, 0, 10, 10]}], "found": "yes"},
    {"t": "boxes", "source": "er2", "objects": [{"label": "x", "box": [0, 0, 10, 10]}], "target": 7},
    {"t": "boxes", "source": "er2", "objects": [{"label": "x", "box": [0, 0, 10, 10], "severity": "danger"}] * 33},
]


@pytest.mark.parametrize("raw", BAD_CLIENT, ids=lambda m: json.dumps(m))
def test_bad_client_msg_rejected(raw):
    with pytest.raises(ValidationError):
        client_adapter.validate_python(raw)
    assert parse_client_msg(json.dumps(raw)) is None


@pytest.mark.parametrize("raw", BAD_SERVER, ids=lambda m: json.dumps(m))
def test_bad_server_msg_rejected(raw):
    with pytest.raises(ValidationError):
        server_adapter.validate_python(raw)
    assert parse_server_msg(json.dumps(raw)) is None


def test_boxes_messages_carry_boxes_in_0_1000_and_an_optional_score_and_severity():
    msg = client_adapter.validate_python({"t": "boxes", "source": "mediapipe", "objects": [
        {"label": "chair", "box": [420, 310, 900, 690], "score": 0.82, "severity": "danger"},
        {"label": "bottle", "box": [100, 50, 300, 120]}]})
    assert msg.t == "boxes" and msg.source == "mediapipe" and msg.frame_id is None
    assert msg.objects[0].box == (420, 310, 900, 690) and msg.objects[0].severity == "danger"
    assert msg.objects[1].score is None and msg.objects[1].severity is None
    assert contracts.MAX_BOXES == 32
    out = json.loads(dump_server_msg(contracts.BoxesOutMsg(
        source="er2", objects=[contracts.BoxObject(label="milk", box=(1, 2, 3, 4), score=0.9)],
        frame_id="f1", target="milk", found=True)))
    assert out == {"t": "boxes", "source": "er2", "objects": [{"label": "milk", "box": [1, 2, 3, 4], "score": 0.9}],
                   "frameId": "f1", "target": "milk", "found": True}
    assert parse_server_msg(json.dumps(out)).found is True
    # target/found are server-only: like every other extra key they are ignored on the way in
    assert not hasattr(parse_client_msg(json.dumps(out)), "found")


def test_non_finite_numbers_rejected():
    with pytest.raises(ValidationError):
        client_adapter.validate_python(
            {"t": "frame", "id": "f", "jpeg": "x", "w": 1, "h": 1, "ts": float("inf"), "purpose": "count"}
        )
    with pytest.raises(ValidationError):
        server_adapter.validate_python(
            {"t": "count", "sku": "s", "counted": 3, "expected": float("nan"), "final": False, "confidence": 0.5}
        )


# ---------- parse helpers ----------
def test_parse_helpers():
    assert parse_server_msg('{"t":"cue","cue":"interrupted"}').cue == "interrupted"
    assert parse_server_msg("{not json") is None
    assert parse_server_msg('["t"]') is None
    assert parse_client_msg('{"t":"stop"}').t == "stop"
    assert parse_client_msg(b'{"t":"text","text":"hi"}').text == "hi"
    assert parse_client_msg('{"t":"text"}') is None
    assert parse_client_msg("") is None


def test_direction_matters_for_shared_tags():
    assert parse_client_msg('{"t":"audio","pcm16":"AAAA"}') is not None
    assert parse_server_msg('{"t":"audio","pcm16":"AAAA"}') is None
    assert parse_server_msg('{"t":"confirm","id":"c1","answer":"yes"}') is None


# ---------- server dump ----------
def test_server_dump_uses_camel_case():
    msg = server_adapter.validate_python(
        {"t": "count", "sku": "s", "counted": 3, "expected": None, "final": False, "confidence": 0.5}
    )
    out = json.loads(server_adapter.dump_json(msg, by_alias=True))
    assert out == {"t": "count", "sku": "s", "counted": 3, "expected": None, "final": False, "confidence": 0.5}


def test_dump_server_msg_omits_unset_optionals_but_keeps_null_expected():
    assert json.loads(dump_server_msg(CountMsg(sku="s", counted=3, expected=None, final=True, confidence=0.9))) == {
        "t": "count", "sku": "s", "counted": 3, "expected": None, "final": True, "confidence": 0.9,
    }
    assert json.loads(dump_server_msg(RequestFrameMsg(purpose="invoice", burst=Burst(count=2, interval_ms=400)))) == {
        "t": "request_frame", "purpose": "invoice", "burst": {"count": 2, "intervalMs": 400},
    }
    assert json.loads(dump_server_msg(TargetMsg(frame_id="f1", label="x", point=(1, 2), confidence=1))) == {
        "t": "target", "frameId": "f1", "label": "x", "point": [1, 2], "confidence": 1,
    }


def test_server_models_build_by_python_name_and_dump_camel_case():
    msg = ReadyMsg(session_id="s1", resume="s1", mock=True)
    assert json.loads(dump_server_msg(msg)) == {"t": "ready", "sessionId": "s1", "resume": "s1", "mock": True}
    assert isinstance(dump_server_msg(msg), str)


def test_every_dumped_server_msg_parses_back():
    for raw in SERVER:
        msg = parse_server_msg(json.dumps(raw))
        assert parse_server_msg(dump_server_msg(msg)) == msg
