"""Hearing a session on the dashboard (``AIRA_MONITOR_AUDIO``, ``monitor.listen``): the phone's microphone chunks
and AIRA's voice are relayed only to a monitor that asked, only for the session it named, with a bounded queue.

Everything is in-process: the hub, fake sessions, and the WebSocket route in mock mode (TestClient). Nothing is
stored and no network is touched. The flag is off by default (team rule): every test turns it on itself.
"""
from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient
from live_fakes import Recorder

from aira_live import config, contracts, monitor
from aira_live import main as main_mod
from aira_live.contracts import AudioOutMsg, TranscriptMsg
from aira_live.session import Session
from core.monitor_fakes import ORIGIN, SHOP

HELLO = {"t": "hello", "uid": "u1", "lang": "en", "verbosity": "short", "appVersion": "0.1.0"}
PCM = base64.b64encode(b"\x01\x00\x02\x00\x03\x00\x04\x00").decode()


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
def clean(monkeypatch):
    for name in ("AIRA_MONITOR_AUTH", "AIRA_MONITOR_VIEWERS", "AIRA_MONITOR_AUDIO", "GEMINI_API_KEY", "AIRA_MOCK",
                 "AIRA_ALLOW_NO_TOKEN", "AIRA_APP_KEY", "AIRA_MONITOR_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    monitor.reset()
    yield
    monitor.reset()


@pytest.fixture
def audio_on(monkeypatch):
    monkeypatch.setenv("AIRA_MONITOR_AUDIO", "1")


def _audio(sub) -> list[tuple[str, str, int, str]]:
    return [(m["session"], m["who"], m["rate"], m["pcm"]) for m in sub.take_audio()]


# ---------- the flag ----------
def test_the_flag_is_off_unless_it_is_1(monkeypatch):
    assert config.load().monitor_audio is False
    monkeypatch.setenv("AIRA_MONITOR_AUDIO", "0")
    assert config.load().monitor_audio is False
    monkeypatch.setenv("AIRA_MONITOR_AUDIO", "1")
    assert config.load().monitor_audio is True


def test_ready_line_says_whether_audio_may_be_asked_for_and_names_the_monitor(monkeypatch):
    from aira_live import rtc
    sub = monitor.subscribe(SHOP)
    off = json.loads(monitor.ready_line(sub))
    assert off == {"t": "mon", "kind": "ready", "session": rtc.monitor_id(sub), "ts": off["ts"], "audio": False}
    assert off["session"].startswith("m")
    monkeypatch.setenv("AIRA_MONITOR_AUDIO", "1")
    assert json.loads(monitor.ready_line(sub))["audio"] is True


def test_listen_is_ignored_with_the_flag_off(mk_session):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    assert monitor.listen(sub, s.session_id) is False
    assert sub.listening is None
    assert monitor.audio_in(s, PCM) == 0
    monitor.mirror(s, AudioOutMsg(pcm24=PCM))
    assert sub.take_audio() == []


# ---------- who hears what ----------
def test_nothing_is_relayed_to_a_monitor_that_did_not_ask(mk_session, audio_on):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    assert monitor.audio_in(s, PCM) == 0
    monitor.mirror(s, AudioOutMsg(pcm24=PCM))
    assert sub.take_audio() == []
    assert sub.take() == []  # and nothing of it leaks into the line queue either


def test_a_listening_monitor_hears_the_mic_and_aira_for_that_session_only(mk_session, audio_on):
    s, _ = mk_session()
    other, _ = mk_session()
    asked = monitor.subscribe(SHOP)
    silent = monitor.subscribe(SHOP)
    assert monitor.listen(asked, s.session_id) is True
    assert monitor.audio_in(s, PCM) == 1
    monitor.mirror(s, AudioOutMsg(pcm24="BBBB"))
    assert monitor.audio_in(other, "CCCC") == 0  # another session of the shop: not asked for
    monitor.mirror(other, AudioOutMsg(pcm24="DDDD"))
    assert _audio(asked) == [(s.session_id, "owner", 16000, PCM), (s.session_id, "aira", 24000, "BBBB")]
    assert silent.take_audio() == []
    monitor.audio_in(s, PCM)  # every chunk is a valid contract message, the bytes untouched
    assert contracts.parse_monitor_msg(asked._audio[0]).pcm == PCM


def test_audio_goes_to_the_audio_queue_and_the_line_queue_keeps_the_transcript(mk_session, audio_on):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    monitor.listen(sub, s.session_id)
    monitor.mirror(s, TranscriptMsg(role="aira", text="Two", final=False))
    monitor.mirror(s, AudioOutMsg(pcm24=PCM))
    assert [m["kind"] for m in sub.take()] == ["transcript"]
    assert [m["kind"] for m in sub.take_audio()] == ["audio"]


def test_a_room_camera_session_is_never_relayed(audio_on):
    cam = Session("camera:front", SHOP, "en", "short", Recorder())
    sub = monitor.subscribe(SHOP)
    monitor.listen(sub, cam.session_id)
    assert monitor.audio_in(cam, PCM) == 0
    assert sub.take_audio() == []


def test_a_session_of_another_shop_is_never_heard(mk_session, audio_on):
    s, _ = mk_session(shop="other_shop")
    sub = monitor.subscribe(SHOP)
    monitor.listen(sub, s.session_id)  # a guessed id of a session elsewhere
    monitor.subscribe("other_shop")  # so the other shop counts as monitored
    assert monitor.audio_in(s, PCM) == 0
    monitor.mirror(s, AudioOutMsg(pcm24=PCM))
    assert sub.take_audio() == []


# ---------- stopping ----------
def test_stop_switch_leave_and_session_end_all_stop_the_relay(mk_session, audio_on):
    s1, _ = mk_session()
    s2, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    monitor.listen(sub, s1.session_id)
    monitor.audio_in(s1, "A1")
    monitor.listen(sub, None)  # stop
    assert sub.listening is None
    monitor.audio_in(s1, "A2")
    assert [m["pcm"] for m in sub.take_audio()] == ["A1"]

    monitor.listen(sub, s1.session_id)
    monitor.listen(sub, s2.session_id)  # switch: the first session goes quiet, the second is heard
    monitor.audio_in(s1, "A3")
    monitor.audio_in(s2, "B1")
    assert [(m["session"], m["pcm"]) for m in sub.take_audio()] == [(s2.session_id, "B1")]

    monitor.session_ended(s2)  # the session ends: nobody keeps listening to it
    assert sub.listening is None
    monitor.audio_in(s2, "B2")
    assert sub.take_audio() == []

    monitor.listen(sub, s1.session_id)
    monitor.unsubscribe(sub)  # the monitor leaves: it is no longer anyone's listener
    assert monitor.listeners(s1) == []
    assert monitor.audio_in(s1, "A4") == 0


# ---------- bounded ----------
def test_a_backed_up_monitor_loses_its_oldest_chunks_and_counts_them(mk_session, audio_on, caplog):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    monitor.listen(sub, s.session_id)
    n = monitor.MAX_AUDIO_QUEUE + 5
    for i in range(n):
        monitor.audio_in(s, f"C{i}")
    assert sub.audio_dropped == 5
    got = [m["pcm"] for m in sub.take_audio()]
    assert len(got) == monitor.MAX_AUDIO_QUEUE and got[0] == "C5" and got[-1] == f"C{n - 1}"
    assert sub.take() == []  # the line queue was never touched
    with caplog.at_level("WARNING", logger="aira_live.monitor"):
        for i in range(monitor.MAX_AUDIO_QUEUE + monitor.AUDIO_DROP_LOG_EVERY):
            monitor.audio_in(s, "D")
    assert sub.audio_dropped == 5 + monitor.AUDIO_DROP_LOG_EVERY  # the queue was empty again: 32 fit, 100 did not
    assert any("lost 100 audio chunks" in r.getMessage() for r in caplog.records)


async def test_the_sender_drains_lines_before_audio(mk_session, audio_on):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    monitor.listen(sub, s.session_id)
    monitor.audio_in(s, "A1")
    monitor.status(s, "thinking")
    monitor.audio_in(s, "A2")
    kinds = [json.loads(await sub.next_line())["kind"] for _ in range(3)]
    assert kinds == ["status", "audio", "audio"]


# ---------- the socket ----------
def test_over_the_socket_a_monitor_hears_ready_then_audio_only_after_it_asked(audio_on):
    with TestClient(main_mod.app) as client:
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            assert _recv_until(mon, "ready")["audio"] is True
            _wait(lambda: monitor.monitored(SHOP))
            with client.websocket_connect("/ws", headers=ORIGIN) as ws:
                ws.send_json(HELLO)
                sid = ws.receive_json()["sessionId"]
                assert _recv_until(mon, "session")["session"] == sid
                ws.send_json({"t": "audio", "pcm16": "AAAA"})  # before any listen: not relayed
                ws.send_json({"t": "text", "text": "hello"})
                assert _recv_until(mon, "transcript")["text"] == "hello"  # the mock echo proves the audio went by
                mon.send_json({"t": "listen", "session": sid})
                _wait(lambda: monitor._HUB[SHOP][0].listening == sid)
                ws.send_json({"t": "audio", "pcm16": "BBBB"})
                audio = _recv_until(mon, "audio")
                assert audio == {"t": "mon", "kind": "audio", "session": sid, "ts": audio["ts"], "who": "owner",
                                 "rate": 16000, "pcm": "BBBB"}
                mon.send_json({"t": "listen", "session": None})
                _wait(lambda: monitor._HUB[SHOP][0].listening is None)
                ws.send_json({"t": "audio", "pcm16": "CCCC"})
                ws.send_json({"t": "text", "text": "bye"})
                while True:  # nothing but the echo arrives: the CCCC chunk was never relayed
                    m = mon.receive_json()
                    assert m["kind"] != "audio"
                    if m["kind"] == "transcript" and m["text"] == "bye":
                        break
        _wait(lambda: not monitor.monitored(SHOP))


def test_over_the_socket_ready_says_no_audio_and_listen_is_ignored_by_default():
    with TestClient(main_mod.app) as client:
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            assert _recv_until(mon, "ready")["audio"] is False
            _wait(lambda: monitor.monitored(SHOP))
            mon.send_json({"t": "listen", "session": "s1"})
            assert monitor.publish(SHOP, "status", "s1", state="idle") == 1
            assert _recv_until(mon, "status")["state"] == "idle"  # the socket is still served
            assert monitor._HUB[SHOP][0].listening is None
        _wait(lambda: not monitor.monitored(SHOP))


def test_ready_comes_after_the_live_sessions_a_late_monitor_first_hears():
    with TestClient(main_mod.app) as client, client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        sid = ws.receive_json()["sessionId"]
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            first, second = mon.receive_json(), mon.receive_json()
            assert (first["kind"], first["session"]) == ("session", sid)
            assert (second["kind"], second["audio"]) == ("ready", False)
            assert second["session"] != sid and second["session"].startswith("m")  # the monitor's own id
