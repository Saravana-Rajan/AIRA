"""The browser e2e server: the mock aira-live plus /e2e/* routes that script a session (mock mode only)."""
import pytest
from fastapi.testclient import TestClient

from aira_live import confirm
from aira_live.devtools.e2e_server import app

HELLO = {"t": "hello", "uid": "e2e-phone", "shopId": "sunrise_dairy", "lang": "en", "verbosity": "short",
         "appVersion": "0.1.0"}


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("AIRA_MOCK", "1")


@pytest.fixture(autouse=True)
def fresh_store():
    previous = confirm._store
    confirm.set_store(confirm.MemoryProposalStore())
    yield
    confirm.set_store(previous)


def test_health_and_ws_are_the_real_mock_service():
    client = TestClient(app)
    assert client.get("/health").json() == {"ok": True, "mock": True}
    with client.websocket_connect("/ws", headers={"Origin": "https://aira-shop.web.app"}) as ws:
        ws.send_json(HELLO)
        ready = ws.receive_json()
        assert ready["t"] == "ready" and ready["mock"] is True


def test_routes_refuse_without_a_live_session():
    client = TestClient(app)
    assert client.get("/e2e/sessions").json() == {"sessions": []}
    assert client.post("/e2e/confirm", json={"kind": "sale", "prompt": "Sale?"}).status_code == 409
    assert client.post("/e2e/count", json={"counted": 1, "expected": 3, "final": False}).status_code == 409


def test_routes_refuse_outside_mock_mode(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("AIRA_MOCK", raising=False)
    client = TestClient(app)
    assert client.get("/e2e/sessions").status_code == 403
    assert client.post("/e2e/confirm", json={"kind": "sale", "prompt": "Sale?"}).status_code == 403
    assert client.post("/e2e/count", json={"counted": 1, "expected": 3, "final": False}).status_code == 403


def test_confirm_is_proposed_then_committed_by_the_backend_on_yes():
    client = TestClient(app)
    with client.websocket_connect("/ws", headers={"Origin": "https://aira-shop.web.app"}) as ws:
        ws.send_json(HELLO)
        ready = ws.receive_json()
        assert client.get("/e2e/sessions").json() == {"sessions": [ready["sessionId"]]}

        r = client.post("/e2e/confirm", json={"kind": "sale", "prompt": "Sale: 2 x Sakthi curd 1 L, Rs 120?"})
        assert r.status_code == 200
        cid = r.json()["confirmId"]
        assert cid.startswith("cf_")
        assert ws.receive_json() == {"t": "confirm", "id": cid, "kind": "sale",
                                     "prompt": "Sale: 2 x Sakthi curd 1 L, Rs 120?"}
        assert client.get(f"/e2e/confirm/{cid}").json() == {"status": "pending"}

        ws.send_json({"t": "confirm", "id": cid, "answer": "yes"})
        # The committer records the sale as a state change, then main.py speaks the result.
        state = ws.receive_json()
        assert state["t"] == "state" and state["entity"] == "sale" and state["state"] == "COMPLETED"
        assert state["summary"] == "Sale: 2 x Sakthi curd 1 L, Rs 120?"
        said = ws.receive_json()
        assert said["t"] == "transcript" and said["role"] == "aira" and said["final"] is True
        assert "Done" in said["text"]
        assert client.get(f"/e2e/confirm/{cid}").json() == {"status": "committed"}


def test_no_answer_rejects_and_never_commits():
    client = TestClient(app)
    with client.websocket_connect("/ws", headers={"Origin": "https://aira-shop.web.app"}) as ws:
        ws.send_json(HELLO); ws.receive_json()
        cid = client.post("/e2e/confirm", json={"kind": "payment", "prompt": "Pay?"}).json()["confirmId"]
        ws.receive_json()
        ws.send_json({"t": "confirm", "id": cid, "answer": "no"})
        said = ws.receive_json()
        assert said["t"] == "transcript"
        assert client.get(f"/e2e/confirm/{cid}").json() == {"status": "rejected"}


def test_count_reaches_the_phone():
    client = TestClient(app)
    with client.websocket_connect("/ws", headers={"Origin": "https://aira-shop.web.app"}) as ws:
        ws.send_json(HELLO); ws.receive_json()
        r = client.post("/e2e/count", json={"counted": 12, "expected": 30, "final": False})
        assert r.status_code == 200
        assert ws.receive_json() == {"t": "count", "sku": "sakthi_curd_1l", "counted": 12, "expected": 30,
                                     "final": False, "confidence": 0.9}
        client.post("/e2e/count", json={"counted": 30, "expected": 30, "final": True, "sku": "aavin_milk_500ml"})
        m = ws.receive_json()
        assert m["sku"] == "aavin_milk_500ml" and m["final"] is True


def test_unknown_confirm_id_is_404():
    assert TestClient(app).get("/e2e/confirm/cf_nope").status_code == 404
