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
