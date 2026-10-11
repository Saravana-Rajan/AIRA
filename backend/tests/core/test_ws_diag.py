"""Diagnostics on the WebSocket: hello / frame lines, and the client ``log`` message (rate-limited, capped)."""
import json

import pytest
from fastapi.testclient import TestClient

from aira_live import main as main_mod
from aira_live.diag import RateLimiter
from aira_live.main import app

ORIGIN = {"Origin": "https://aira-shop.web.app"}
HELLO = {"t": "hello", "uid": "u1", "shopId": "sunrise_dairy", "lang": "en", "verbosity": "short",
         "appVersion": "0.1.0"}


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AIRA_MOCK", raising=False)
    monkeypatch.delenv("AIRA_DIAG", raising=False)


def records(capsys, event: str) -> list[dict]:
    out = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    return [r for r in out if r.get("aira") == event]


def test_hello_and_frames_are_logged_without_bytes(capsys):
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ready = ws.receive_json()
        ws.send_json({"t": "frame", "id": "f1", "jpeg": "/9j/4AAQ", "w": 640, "h": 480, "ts": 1, "purpose": "ask"})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    out = capsys.readouterr().out
    assert "/9j/4AAQ" not in out
    recs = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    hello = [r for r in recs if r["aira"] == "hello"]
    assert hello and hello[0]["uid"] == "u1" and hello[0]["app_version"] == "0.1.0"
    assert hello[0]["session"] == ready["sessionId"] and hello[0]["lang"] == "en" and hello[0]["mock"] is True
    frames = [r for r in recs if r["aira"] == "frame"]
    assert frames == [{**frames[0], "purpose": "ask", "bytes": 8, "w": 640, "h": 480, "id": "f1"}]
    assert "jpeg" not in frames[0]
    assert [r["event"] for r in recs if r["aira"] == "ws"] == ["open", "close"]


def test_client_log_message_is_accepted_and_written_as_a_client_line(capsys):
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ready = ws.receive_json()
        ws.send_json({"t": "log", "tag": "tilt", "data": {"beta": 131.2, "reason": "speak", "jpeg": "/9j/AAAA"}})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}  # a log line gets no reply
    client = records(capsys, "client")
    assert len(client) == 1
    assert client[0]["tag"] == "tilt" and client[0]["data"]["beta"] == 131.2
    assert client[0]["session"] == ready["sessionId"] and client[0]["shop"] == "sunrise_dairy"
    assert client[0]["data"]["jpeg"] == "<redacted 8>"


def test_client_log_before_hello_is_accepted_too(capsys):
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"t": "log", "tag": "build", "data": {"build": "a1b2c3d"}})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    client = records(capsys, "client")
    assert len(client) == 1 and client[0]["tag"] == "build" and client[0]["session"] is None


def test_client_log_data_is_capped_at_2kb(capsys):
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_json({"t": "log", "tag": "big", "data": {"blob": "z" * 5000}})
        ws.send_json({"t": "stop"})
        ws.receive_json()
    client = records(capsys, "client")
    assert len(client) == 1 and client[0]["data"]["_truncated"] is True and client[0]["data"]["size"] > 2048
    assert len(json.dumps(client[0]["data"])) <= 2048


def test_client_log_is_rate_limited_to_20_per_second_per_session(capsys, monkeypatch):
    monkeypatch.setattr(main_mod, "_log_limiter", lambda: RateLimiter(20, clock=lambda: 100.0))
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        for i in range(30):
            ws.send_json({"t": "log", "tag": "tilt", "data": {"i": i}})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    client = records(capsys, "client")
    assert [r["data"]["i"] for r in client] == list(range(20))


def test_malformed_log_message_is_unsupported(capsys):
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"t": "log", "tag": "tilt", "data": []})
        assert ws.receive_json()["code"] == "unsupported"
        ws.send_json({"t": "log", "tag": 5, "data": {}})
        assert ws.receive_json()["code"] == "unsupported"
    assert records(capsys, "client") == []
