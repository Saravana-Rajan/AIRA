"""The live feed's default: no authentication, by the owner's decision (shared/docs/ops/security.md §2a).

With AIRA_MONITOR_AUTH unset, /monitor subscribes a browser from an allowed Origin at once: no first message,
no token, no verifier call. The Origin allowlist, the shop check and the per-shop cap still apply, and
AIRA_MONITOR_AUTH=1 turns the sign-in gate (test_monitor_auth.py) back on.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aira_live import config, monitor
from aira_live import main as main_mod
from core.monitor_fakes import ORIGIN, SHOP, FakeVerifier

HELLO = {"t": "hello", "uid": "u1", "lang": "en", "verbosity": "short", "appVersion": "0.1.0"}
HELLO_2 = {**HELLO, "uid": "u2"}


def _wait(pred, timeout: float = 3.0) -> None:
    deadline = time.time() + timeout
    while not pred():
        if time.time() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


def _recv_until(ws, kind: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = ws.receive_json()
        if msg["t"] == "mon" and msg["kind"] == kind:
            return msg
    raise AssertionError(f"no {kind!r} monitor message")


@pytest.fixture(autouse=True)
def open_mode(monkeypatch) -> FakeVerifier:
    """No AIRA_MONITOR_AUTH, no viewers, no demo key; a fake verifier that must never be called."""
    for name in ("AIRA_MONITOR_AUTH", "AIRA_MONITOR_VIEWERS", "GEMINI_API_KEY", "AIRA_MOCK", "AIRA_ALLOW_NO_TOKEN",
                 "AIRA_APP_KEY", "AIRA_MONITOR_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    fake = FakeVerifier()
    monkeypatch.setattr(monitor, "verify_user", fake)
    monitor.reset()
    yield fake
    monitor.reset()


def _refused(client, headers=ORIGIN, shop: str = SHOP) -> int:
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/monitor?shop={shop}", headers=headers) as mon:
            mon.receive_json()
    return closed.value.code


def test_the_flag_is_off_unless_it_is_1(monkeypatch):
    assert config.load().monitor_auth is False
    monkeypatch.setenv("AIRA_MONITOR_AUTH", "0")
    assert config.load().monitor_auth is False
    monkeypatch.setenv("AIRA_MONITOR_AUTH", "1")
    assert config.load().monitor_auth is True


def test_a_monitor_from_the_hosting_site_is_subscribed_without_sending_anything(open_mode):
    with TestClient(main_mod.app) as client:
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            _wait(lambda: monitor.monitored(SHOP))
            assert monitor._HUB[SHOP][0].viewer is None
            assert monitor.publish(SHOP, "status", "s1", state="idle") == 1
            got = _recv_until(mon, "status")
            assert got["session"] == "s1" and got["state"] == "idle"
        _wait(lambda: not monitor.monitored(SHOP))
    assert open_mode.calls == []


def test_a_late_monitor_hears_every_live_session_with_its_start_time(open_mode):
    with TestClient(main_mod.app) as client, client.websocket_connect("/ws", headers=ORIGIN) as ws1, \
            client.websocket_connect("/ws", headers=ORIGIN) as ws2:
        before = time.time() * 1000
        ws1.send_json(HELLO)
        sid1 = ws1.receive_json()["sessionId"]
        ws2.send_json(HELLO_2)
        sid2 = ws2.receive_json()["sessionId"]
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            first, second = mon.receive_json(), mon.receive_json()  # before any other message
            starts = {m["session"]: m for m in (first, second)}
            assert set(starts) == {sid1, sid2}
            for sid, uid in ((sid1, "u1"), (sid2, "u2")):
                m = starts[sid]
                assert m["t"] == "mon" and m["kind"] == "session" and m["state"] == "start"
                assert m["lang"] == "en" and m["uid"] == uid
                assert isinstance(m["startedAt"], int) and not isinstance(m["startedAt"], bool)
                assert abs(m["startedAt"] - before) < 60_000
            ws1.send_json({"t": "stop"})
            ws1.close()
            end = _recv_until(mon, "session")
            assert end["state"] == "end" and end["session"] == sid1
    assert open_mode.calls == []


def test_an_auth_message_on_an_open_monitor_is_ignored(open_mode):
    with TestClient(main_mod.app) as client:
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            _wait(lambda: monitor.monitored(SHOP))
            mon.send_json({"t": "auth", "idToken": "a.b.c"})
            time.sleep(0.1)
            assert monitor.monitored(SHOP)
            assert monitor.publish(SHOP, "status", "s1", state="thinking") == 1
            assert _recv_until(mon, "status")["state"] == "thinking"
    assert open_mode.calls == []


def test_a_foreign_or_missing_origin_is_still_refused_before_accept(open_mode):
    with TestClient(main_mod.app) as client:
        for headers in ({"Origin": "https://evil.example"}, {}):
            assert _refused(client, headers=headers) == 4403
    assert not monitor.monitored(SHOP) and open_mode.calls == []


def test_another_shop_is_still_refused(open_mode):
    with TestClient(main_mod.app) as client:
        assert _refused(client, shop="other_shop") == 4403
    assert not monitor.monitored("other_shop") and open_mode.calls == []


def test_the_per_shop_cap_still_applies(monkeypatch, open_mode):
    monkeypatch.setattr(monitor, "MAX_MONITORS_PER_SHOP", 1)
    with TestClient(main_mod.app) as client:
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN):
            _wait(lambda: monitor.monitors(SHOP) == 1)
            assert _refused(client) == 4403
            assert monitor.monitors(SHOP) == 1


def test_the_flag_turns_the_gate_back_on(monkeypatch, fake_viewers):
    """fake_viewers sets AIRA_MONITOR_AUTH=1: a monitor that sends no token is closed with 4401."""
    monkeypatch.setattr(monitor, "AUTH_TIMEOUT_S", 0.2)
    with TestClient(main_mod.app) as client:
        assert _refused(client) == 4401
    assert not monitor.monitored(SHOP) and fake_viewers.calls == []


def test_session_fields_carry_the_start_time_in_ms(mk_session):
    before = time.time()
    s, _ = mk_session()
    assert before <= s.started_at <= time.time()
    fields = monitor.session_fields(s)
    assert fields["lang"] == "en" and fields["uid"] == "u1"
    assert isinstance(fields["started_at"], int) and fields["started_at"] == int(s.started_at * 1000)
    assert monitor.session_fields(object()) == {"lang": "en"}  # a session without a start time: no field
