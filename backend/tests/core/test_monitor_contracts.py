"""The /monitor channel contract: the shared fixtures round-trip through the Python mirror unchanged."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aira_live import contracts
from aira_live.contracts import MONITOR_KINDS, dump_monitor_msg, monitor_adapter


def _fixtures() -> list[dict]:
    for parent in Path(__file__).resolve().parents:
        f = parent / "shared" / "contracts" / "fixtures" / "monitor_msgs.json"
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    raise RuntimeError("monitor_msgs.json not found above this test")


MON = _fixtures()


def test_fixtures_cover_every_kind():
    assert {m["kind"] for m in MON} == set(MONITOR_KINDS)


@pytest.mark.parametrize("raw", MON, ids=lambda m: m["kind"])
def test_monitor_fixture_round_trips_unchanged(raw):
    msg = monitor_adapter.validate_python(raw)
    assert json.loads(dump_monitor_msg(msg)) == raw


def test_frame_keeps_snake_case_jpeg_key():
    msg = contracts.MonFrameMsg(session="s", ts=1, jpeg_b64="AAAA", w=480, h=640, purpose="guide")
    assert json.loads(dump_monitor_msg(msg))["jpeg_b64"] == "AAAA"


@pytest.mark.parametrize(
    "bad",
    [
        {"t": "mon", "kind": "video", "session": "s", "ts": 1},
        {"t": "mon", "kind": "hand", "session": "s", "ts": 1, "point": [1001, 0], "near": True},
        {"t": "mon", "kind": "transcript", "session": "s", "ts": 1, "role": "user", "text": "x", "lang": "ta", "final": True},
        {"t": "mon", "kind": "event", "session": "s", "ts": 1, "name": "x", "detail": "a string"},
    ],
)
def test_rejects_bad_messages(bad):
    with pytest.raises(ValidationError):
        monitor_adapter.validate_python(bad)


def _client_fixtures() -> list[dict]:
    for parent in Path(__file__).resolve().parents:
        f = parent / "shared" / "contracts" / "fixtures" / "monitor_client_msgs.json"
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    raise RuntimeError("monitor_client_msgs.json not found above this test")


def test_monitor_client_fixtures_parse_and_cover_auth_listen_and_rtc():
    """What a monitor sends: its Firebase ID token ({t:"auth", idToken}), listen requests and rtc signalling, in
    lockstep with TS."""
    raws = _client_fixtures()
    parsed = [contracts.parse_monitor_client_msg(json.dumps(raw)) for raw in raws]
    assert all(m is not None for m in parsed)
    assert {m.t for m in parsed} == {"auth", "listen", "rtc"}
    listens = [m for m in parsed if isinstance(m, contracts.MonListenMsg)]
    assert sorted((m.session for m in listens), key=str) == ["s1", None] or {m.session for m in listens} == {"s1", None}
    auth = next(m for m in parsed if isinstance(m, contracts.MonAuthMsg))
    assert auth.id_token == next(r["idToken"] for r in raws if r["t"] == "auth")
    assert contracts.parse_monitor_client_msg(json.dumps({"t": "auth", "idToken": ""})) is None
    assert contracts.parse_monitor_client_msg(json.dumps({"t": "auth", "key": "k"})) is None


def test_listen_is_a_session_id_or_null_and_nothing_else():
    assert contracts.parse_monitor_client_msg('{"t":"listen","session":"s1"}').session == "s1"
    assert contracts.parse_monitor_client_msg('{"t":"listen","session":null}').session is None
    assert contracts.parse_monitor_client_msg('{"t":"listen"}') is None
    assert contracts.parse_monitor_client_msg('{"t":"listen","session":""}') is None
    assert contracts.parse_monitor_client_msg('{"t":"listen","session":5}') is None


def test_ready_and_audio_messages_mirror_the_ts_contract():
    ready = json.loads(dump_monitor_msg(contracts.MonReadyMsg(session="m1a2b3c4", ts=1, audio=True)))
    assert ready == {"t": "mon", "kind": "ready", "session": "m1a2b3c4", "ts": 1, "audio": True}
    audio = json.loads(dump_monitor_msg(contracts.MonAudioMsg(session="s1", ts=2, who="aira", rate=24000, pcm="AAAA")))
    assert audio == {"t": "mon", "kind": "audio", "session": "s1", "ts": 2, "who": "aira", "rate": 24000, "pcm": "AAAA"}
    for bad in (
        {"t": "mon", "kind": "ready", "session": "m1", "ts": 1},
        {"t": "mon", "kind": "ready", "session": "m1", "ts": 1, "audio": "yes"},
        {"t": "mon", "kind": "ready", "ts": 1, "audio": True},
        {"t": "mon", "kind": "audio", "session": "s1", "ts": 1, "who": "customer", "rate": 16000, "pcm": "AAAA"},
        {"t": "mon", "kind": "audio", "session": "s1", "ts": 1, "who": "owner", "rate": 44100, "pcm": "AAAA"},
        {"t": "mon", "kind": "audio", "session": "s1", "ts": 1, "who": "owner", "rate": "16000", "pcm": "AAAA"},
        {"t": "mon", "kind": "audio", "session": "s1", "ts": 1, "who": "owner", "rate": 16000},
    ):
        with pytest.raises(ValidationError):
            monitor_adapter.validate_python(bad)
