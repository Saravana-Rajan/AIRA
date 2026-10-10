"""Mock-mode aira-live: /healthz and the /ws WebSocket on the final protocol (no Gemini key needed)."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from aira_live import confirm, sessions
from aira_live.frames import FrameStore
from aira_live.main import app

HELLO = {"t": "hello", "uid": "u1", "shopId": "murugan_dairy", "lang": "en", "verbosity": "short",
         "appVersion": "0.1.0"}


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AIRA_MOCK", raising=False)


@pytest.fixture
def store():
    """A fresh in-memory proposal store, restored afterwards so tests never share proposals."""
    previous = confirm._store
    s = confirm.MemoryProposalStore()
    confirm.set_store(s)
    yield s
    confirm.set_store(previous)


def put_pending(store, confirm_id, committer):
    asyncio.run(store.put("murugan_dairy", confirm_id, {"kind": "order", "prompt": "Order?", "payload": {},
                                                        "committer": committer, "status": "pending",
                                                        "sessionId": "x"}))


def test_healthz_reports_mock_without_key():
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True, "mock": True}


def test_healthz_reports_live_with_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert TestClient(app).get("/healthz").json() == {"ok": True, "mock": False}


def test_hello_then_text_echo():
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json(HELLO)
        ready = ws.receive_json()
        assert ready["t"] == "ready" and ready["mock"] is True and ready["sessionId"]
        assert ready["resume"] == ready["sessionId"]
        ws.send_json({"t": "text", "text": "hello aira"})
        assert ws.receive_json() == {"t": "transcript", "role": "aira", "text": "(mock) you said: hello aira",
                                     "final": True}


def test_echo_uses_the_hello_language():
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({**HELLO, "lang": "ta"})
        ws.receive_json()
        ws.send_json({"t": "text", "text": "வணக்கம்"})
        reply = ws.receive_json()
        assert reply["text"].endswith("வணக்கம்") and not reply["text"].startswith("(mock) you said")


def test_unknown_type_returns_error_and_socket_stays_open():
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"t": "bogus"})
        assert ws.receive_json()["code"] == "unsupported"
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '"hello"', "null", '{"t": "hello", "uid": 1}'])
def test_bad_json_or_invalid_message_is_unsupported_and_socket_stays_open(raw):
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_text(raw)
        err = ws.receive_json()
        assert err["t"] == "error" and err["code"] == "unsupported" and err["say"]
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}


def test_binary_message_is_unsupported_and_socket_stays_open():
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_json()["code"] == "unsupported"
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}


def test_oversized_frame_rejected_not_stored(monkeypatch):
    stored = []
    real_add = FrameStore.add

    def spy(self, frame):
        stored.append(frame.id)
        return real_add(self, frame)

    monkeypatch.setattr(FrameStore, "add", spy)
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json(HELLO); ws.receive_json()
        ws.send_json({"t": "frame", "id": "big", "jpeg": "A" * 1_600_000, "w": 1, "h": 1, "ts": 1, "purpose": "count"})
        err = ws.receive_json()
        assert err["code"] == "unsupported" and err["say"] == "That image is too large."
        ws.send_json({"t": "frame", "id": "ok", "jpeg": "AAAA", "w": 1, "h": 1, "ts": 2, "purpose": "count"})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}  # a valid frame gets no reply
    assert stored == ["ok"]


def test_confirm_message_resolves_proposal(store):
    async def commit(payload, value):
        return {"say": "Order sent to Sakthi vendor."}

    confirm.register_committer("test.ws", commit)
    put_pending(store, "c1", "test.ws")
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json(HELLO); ws.receive_json()
        ws.send_json({"t": "confirm", "id": "c1", "answer": "yes"})
        assert ws.receive_json() == {"t": "transcript", "role": "aira", "text": "Order sent to Sakthi vendor.",
                                     "final": True}
        ws.send_json({"t": "confirm", "id": "c1", "answer": "yes"})  # a second yes never commits twice
        assert ws.receive_json()["text"] == "That was already done."


def test_confirm_for_unknown_id_is_spoken_not_fatal(store):
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json(HELLO); ws.receive_json()
        ws.send_json({"t": "confirm", "id": "missing", "answer": "yes"})
        reply = ws.receive_json()
        assert reply["t"] == "transcript" and reply["text"] == "I couldn't find that request."


def test_failing_committer_reports_internal_error_and_socket_stays_open(store):
    async def commit(payload, value):
        raise RuntimeError("network down")

    confirm.register_committer("test.ws.boom", commit)
    put_pending(store, "c2", "test.ws.boom")
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json(HELLO); ws.receive_json()
        ws.send_json({"t": "confirm", "id": "c2", "answer": "yes"})
        err = ws.receive_json()
        assert err["t"] == "error" and err["code"] == "internal" and err["say"]
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    assert asyncio.run(store.get("murugan_dairy", "c2"))["status"] == "pending"  # still retryable


def test_messages_that_need_a_session_before_hello_are_refused():
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"t": "confirm", "id": "c1", "answer": "yes"})
        assert ws.receive_json()["code"] == "unsupported"
        ws.send_json({"t": "frame", "id": "f", "jpeg": "AAAA", "w": 1, "h": 1, "ts": 1, "purpose": "count"})
        assert ws.receive_json()["code"] == "unsupported"


def test_audio_and_events_are_accepted_silently_in_mock_mode():
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json(HELLO); ws.receive_json()
        ws.send_json({"t": "audio", "pcm16": "AAAA"})
        ws.send_json({"t": "event", "name": "visibility", "data": {"visible": True}})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}


def test_session_is_registered_for_notify_and_removed_on_disconnect():
    async def notify():
        return await sessions.notify("murugan_dairy", "Low stock: Sakthi curd", cue="warn")

    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json(HELLO); ws.receive_json()
        assert ws.portal.call(notify) == 1
        assert ws.receive_json() == {"t": "transcript", "role": "aira", "text": "Low stock: Sakthi curd",
                                     "final": True}
        assert ws.receive_json() == {"t": "cue", "cue": "warn"}
    assert asyncio.run(notify()) == 0
