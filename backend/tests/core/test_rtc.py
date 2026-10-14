"""WebRTC signalling relay (``aira_live.rtc``): the server passes ``{t:"rtc", ...}`` between a phone session
(/ws) and the dashboard monitors (/monitor) of the same shop, and nothing else. It never reads the SDP, never
crosses shops, caps the size and rate of what it relays, and pauses the JPEG preview of a session while every
monitor of the shop has a WebRTC picture of it.
"""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aira_live import contracts, monitor, rtc, sessions
from aira_live import main as main_mod
from aira_live.contracts import RequestFrameMsg, RtcMsg, RtcOutMsg, parse_monitor_client_msg

ORIGIN = {"Origin": "https://aira-shop.web.app"}
HELLO = {"t": "hello", "uid": "u1", "shopId": "sunrise_dairy", "lang": "en", "verbosity": "short",
         "appVersion": "0.1.0"}
SHOP = "sunrise_dairy"
SDP = "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
CAND = {"candidate": "candidate:1 1 udp 2122260223 192.0.2.1 50000 typ host", "sdpMid": "0", "sdpMLineIndex": 0}


def _msg(kind: str, to: str, **extra) -> RtcMsg:
    return contracts.client_adapter.validate_python({"t": "rtc", "kind": kind, "to": to, **extra})


def _rtc_sent(rec) -> list[dict]:
    return [json.loads(contracts.dump_server_msg(m)) for m in rec.msgs if isinstance(m, RtcOutMsg)]


def _previews(rec) -> list[tuple]:
    return [(m.continuous, m.stop) for m in rec.msgs if isinstance(m, RequestFrameMsg) and m.purpose == "preview"]


async def settle() -> None:
    for _ in range(30):
        await asyncio.sleep(0)


def _wait(pred, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while not pred():
        if time.time() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


def _recv_rtc(ws, kind: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = ws.receive_json()
        if msg["t"] == "rtc" and msg["kind"] == kind:
            return msg
    raise AssertionError(f"no rtc {kind!r} message")


def _recv_t(ws, t: str, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = ws.receive_json()
        if msg["t"] == t:
            return msg
    raise AssertionError(f"no {t!r} message")


@pytest.fixture(autouse=True)
def clean(monkeypatch, fake_viewers):
    for name in ("GEMINI_API_KEY", "AIRA_MOCK", "AIRA_ALLOW_NO_TOKEN", "AIRA_APP_KEY", "AIRA_MONITOR_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    monitor.reset()
    yield
    monitor.reset()


# ---------- contract ----------
def test_rtc_messages_validate_in_both_directions_and_keep_a_null_m_line_index():
    inbound = contracts.parse_client_msg(json.dumps({"t": "rtc", "kind": "offer", "to": "m1", "sdp": SDP}))
    assert isinstance(inbound, RtcMsg) and inbound.from_ is None and inbound.to == "m1" and inbound.sdp == SDP
    out = RtcOutMsg(kind="ice", from_="m1", to="s1", candidate=contracts.RtcCandidate(candidate="c", sdp_m_line_index=None))
    dumped = json.loads(contracts.dump_server_msg(out))
    assert dumped == {"t": "rtc", "kind": "ice", "from": "m1", "to": "s1", "candidate": {"candidate": "c", "sdpMLineIndex": None}}
    assert contracts.parse_server_msg(json.dumps({"t": "rtc", "kind": "want", "to": "s1"})) is None  # a server message names its sender
    for bad in ({"t": "rtc", "kind": "hello", "to": "m1"}, {"t": "rtc", "kind": "want"},
                {"t": "rtc", "kind": "ice", "to": "m1", "candidate": "candidate:1"},
                {"t": "rtc", "kind": "offer", "to": "m1", "sdp": 5}):
        assert contracts.parse_client_msg(json.dumps(bad)) is None
    assert contracts.RTC_KINDS == ("want", "offer", "answer", "ice", "connected", "bye")


def test_a_monitor_may_send_auth_or_rtc_and_nothing_else():
    from aira_live.contracts import MonAuthMsg
    auth = parse_monitor_client_msg(json.dumps({"t": "auth", "idToken": "a.b.c"}))
    assert isinstance(auth, MonAuthMsg) and auth.id_token == "a.b.c"
    assert parse_monitor_client_msg(json.dumps({"t": "auth", "key": "k"})) is None  # the old shared-key message
    assert parse_monitor_client_msg(json.dumps({"t": "auth", "idToken": ""})) is None
    want = parse_monitor_client_msg(json.dumps({"t": "rtc", "kind": "want", "to": "s1"}))
    assert isinstance(want, RtcMsg) and want.kind == "want"
    assert parse_monitor_client_msg(json.dumps({"t": "hello"})) is None


# ---------- the relay, in process ----------
async def test_want_offer_answer_ice_are_relayed_with_the_senders_id_within_the_shop(mk_session):
    s, rec = mk_session()
    sessions.register(s)
    try:
        sub = monitor.subscribe(SHOP)
        await settle()
        rec.msgs.clear()
        mid = rtc.monitor_id(sub)
        assert mid and mid != s.session_id
        assert await rtc.from_monitor(sub, _msg("want", s.session_id)) is True
        assert _rtc_sent(rec) == [{"t": "rtc", "kind": "want", "from": mid, "to": s.session_id}]
        # the phone's `from` is never trusted: the relay names the session itself
        assert await rtc.from_phone(s, _msg("offer", mid, sdp=SDP, **{"from": "liar"})) is True
        assert sub.take() == [{"t": "rtc", "kind": "offer", "from": s.session_id, "to": mid, "sdp": SDP}]
        assert await rtc.from_monitor(sub, _msg("answer", s.session_id, sdp=SDP)) is True
        assert await rtc.from_monitor(sub, _msg("ice", s.session_id, candidate=CAND)) is True
        sent = _rtc_sent(rec)
        assert sent[1] == {"t": "rtc", "kind": "answer", "from": mid, "to": s.session_id, "sdp": SDP}
        assert sent[2] == {"t": "rtc", "kind": "ice", "from": mid, "to": s.session_id, "candidate": CAND}
        assert await rtc.from_phone(s, _msg("ice", mid, candidate=CAND)) is True
        assert sub.take()[0]["candidate"] == CAND
    finally:
        sessions.unregister(s)


async def test_nothing_crosses_a_shop_or_reaches_an_unknown_peer(mk_session):
    s, rec = mk_session()
    other, other_rec = mk_session("other_shop")
    sessions.register(s)
    sessions.register(other)
    try:
        sub = monitor.subscribe(SHOP)
        other_sub = monitor.subscribe("other_shop")
        await settle()
        rec.msgs.clear()
        other_rec.msgs.clear()
        mid = rtc.monitor_id(sub)
        # a monitor of SHOP cannot signal a session of another shop, nor a session that does not exist
        assert await rtc.from_monitor(sub, _msg("want", other.session_id)) is False
        assert await rtc.from_monitor(sub, _msg("want", "no-such-session")) is False
        assert _rtc_sent(other_rec) == [] and _rtc_sent(rec) == []
        # a phone of another shop cannot reach SHOP's monitor, and nobody reaches an unknown monitor
        assert await rtc.from_phone(other, _msg("offer", mid, sdp=SDP)) is False
        assert await rtc.from_phone(s, _msg("offer", "no-such-monitor", sdp=SDP)) is False
        assert sub.take() == [] and other_sub.take() == []
    finally:
        sessions.unregister(s)
        sessions.unregister(other)


async def test_oversize_sdp_or_candidate_is_dropped(mk_session):
    s, rec = mk_session()
    sessions.register(s)
    try:
        sub = monitor.subscribe(SHOP)
        await settle()
        mid = rtc.monitor_id(sub)
        assert await rtc.from_phone(s, _msg("offer", mid, sdp="x" * (rtc.MAX_SDP + 1))) is False
        big = {"candidate": "c" * (rtc.MAX_CANDIDATE + 1)}
        assert await rtc.from_monitor(sub, _msg("ice", s.session_id, candidate=big)) is False
        assert sub.take() == [] and _rtc_sent(rec) == []
        assert await rtc.from_phone(s, _msg("offer", mid, sdp="x" * rtc.MAX_SDP)) is True
    finally:
        sessions.unregister(s)


def test_the_rate_limiter_bounds_signalling_per_socket():
    lim = rtc.limiter()
    now = 1000.0
    allowed = sum(1 for _ in range(rtc.MAX_PER_S + 5) if lim.allow(now))
    assert allowed == rtc.MAX_PER_S and lim.dropped == 5


async def test_diag_lines_never_carry_the_sdp(mk_session, monkeypatch):
    lines: list[dict] = []
    monkeypatch.setattr(rtc, "diag", lambda event, **f: lines.append({"aira": event, **f}))
    s, rec = mk_session()
    sessions.register(s)
    try:
        sub = monitor.subscribe(SHOP)
        await settle()
        mid = rtc.monitor_id(sub)
        await rtc.from_phone(s, _msg("offer", mid, sdp=SDP))
        await rtc.from_monitor(sub, _msg("ice", s.session_id, candidate=CAND))
        assert [l["kind"] for l in lines] == ["offer", "ice"]
        assert all(SDP not in json.dumps(l) and CAND["candidate"] not in json.dumps(l) for l in lines)
        assert lines[0]["bytes"] == len(SDP) and lines[0]["session"] == s.session_id and lines[0]["monitor"] == mid
    finally:
        sessions.unregister(s)


# ---------- preview pause / resume ----------
async def test_the_preview_pauses_while_every_monitor_has_webrtc_and_resumes_on_bye(mk_session):
    s, rec = mk_session()
    sessions.register(s)
    try:
        a = monitor.subscribe(SHOP)
        await settle()
        assert _previews(rec) == [(True, None)]
        await rtc.from_phone(s, _msg("connected", rtc.monitor_id(a)))
        assert _previews(rec) == [(True, None), (None, True)]  # the only monitor has video: no more JPEGs
        assert rtc.preview_paused(s.session_id) is True
        b = monitor.subscribe(SHOP)  # a second monitor without video needs the JPEG stream again
        await settle()
        assert _previews(rec)[-1] == (True, None) and rtc.preview_paused(s.session_id) is False
        await rtc.from_phone(s, _msg("connected", rtc.monitor_id(b)))
        assert _previews(rec)[-1] == (None, True) and rtc.preview_paused(s.session_id) is True
        await rtc.from_monitor(b, _msg("bye", s.session_id))  # the dashboard fell back: JPEGs again
        assert _previews(rec)[-1] == (True, None) and rtc.preview_paused(s.session_id) is False
        assert _rtc_sent(rec)[-1]["kind"] == "bye"
        monitor.unsubscribe(b)
        await settle()
        assert _previews(rec)[-1] == (None, True)  # only `a` is left, and it has video
        await rtc.from_phone(s, _msg("bye", rtc.monitor_id(a)))  # the phone's connection failed
        assert _previews(rec)[-1] == (True, None)
        assert a.take()[-1]["kind"] == "bye"
    finally:
        sessions.unregister(s)


async def test_a_monitor_leaving_says_bye_to_its_peers_and_frees_the_preview(mk_session):
    s, rec = mk_session()
    sessions.register(s)
    try:
        a = monitor.subscribe(SHOP)
        b = monitor.subscribe(SHOP)
        await settle()
        await rtc.from_monitor(a, _msg("want", s.session_id))
        await rtc.from_phone(s, _msg("connected", rtc.monitor_id(a)))
        await rtc.from_phone(s, _msg("connected", rtc.monitor_id(b)))
        assert _previews(rec)[-1] == (None, True)
        mid_a = rtc.monitor_id(a)
        monitor.unsubscribe(a)
        await settle()
        assert _rtc_sent(rec)[-1] == {"t": "rtc", "kind": "bye", "from": mid_a, "to": s.session_id}
        assert rtc.preview_paused(s.session_id) is True  # b still has video
        assert rtc.monitor_id(a) is None
        monitor.unsubscribe(b)
        await settle()
        assert rtc.preview_paused(s.session_id) is False
        assert _previews(rec)[-1] == (None, True)  # nobody watches: the preview stops anyway
    finally:
        sessions.unregister(s)


async def test_a_session_ending_says_bye_to_its_monitors_and_forgets_it(mk_session):
    s, rec = mk_session()
    sessions.register(s)
    try:
        a = monitor.subscribe(SHOP)
        await settle()
        await rtc.from_phone(s, _msg("connected", rtc.monitor_id(a)))
        a.take()
        monitor.session_ended(s)
        got = a.take()
        assert got[0] == {"t": "rtc", "kind": "bye", "from": s.session_id, "to": rtc.monitor_id(a)}
        assert got[1]["kind"] == "session" and got[1]["state"] == "end"
        assert rtc.preview_paused(s.session_id) is False
    finally:
        sessions.unregister(s)


# ---------- over the sockets ----------
def test_the_full_flow_over_ws_and_monitor(monkeypatch, mon_open):
    with TestClient(main_mod.app) as client, \
            mon_open(client) as mon:
        _wait(lambda: monitor.monitored(SHOP))
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json(HELLO)
            ready = ws.receive_json()
            sid = ready["sessionId"]
            assert ws.receive_json() == {"t": "request_frame", "purpose": "preview", "continuous": True}
            _wait(lambda: any(s.session_id == sid for s, _ in sessions.by_shop(SHOP)))
            mon.send_json({"t": "rtc", "kind": "want", "to": sid})
            want = _recv_rtc(ws, "want")
            mid = want["from"]
            assert want["to"] == sid and mid
            ws.send_json({"t": "rtc", "kind": "offer", "to": mid, "sdp": SDP})
            offer = _recv_rtc(mon, "offer")
            assert offer == {"t": "rtc", "kind": "offer", "from": sid, "to": mid, "sdp": SDP}
            mon.send_json({"t": "rtc", "kind": "answer", "to": sid, "sdp": SDP})
            assert _recv_rtc(ws, "answer") == {"t": "rtc", "kind": "answer", "from": mid, "to": sid, "sdp": SDP}
            mon.send_json({"t": "rtc", "kind": "ice", "to": sid, "candidate": CAND})
            assert _recv_rtc(ws, "ice")["candidate"] == CAND
            ws.send_json({"t": "rtc", "kind": "ice", "to": mid, "candidate": CAND})
            assert _recv_rtc(mon, "ice")["from"] == sid
            ws.send_json({"t": "rtc", "kind": "connected", "to": mid})
            assert _recv_t(ws, "request_frame") == {"t": "request_frame", "purpose": "preview", "stop": True}
            mon.send_json({"t": "rtc", "kind": "bye", "to": sid})
            assert _recv_rtc(ws, "bye")["from"] == mid
            assert _recv_t(ws, "request_frame") == {"t": "request_frame", "purpose": "preview", "continuous": True}
            # junk after auth never closes the monitor
            mon.send_text("{not json")
            mon.send_json({"t": "rtc", "kind": "want", "to": "nobody"})
            ws.send_json({"t": "rtc", "kind": "connected", "to": mid})
            assert _recv_t(ws, "request_frame")["stop"] is True
        _recv_rtc(mon, "bye")  # the session ended: its monitor hears bye


def test_rtc_before_auth_is_refused_and_never_reaches_a_phone(mon_open):
    """Signalling is behind the same check as the feed: an rtc message as the first frame (no token) closes the
    socket with 4401 and the phone hears nothing."""
    with TestClient(main_mod.app) as client, client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        sid = ws.receive_json()["sessionId"]
        with pytest.raises(WebSocketDisconnect) as closed:
            with mon_open(client, token=None) as mon:
                mon.send_json({"t": "rtc", "kind": "want", "to": sid})
                mon.receive_json()
        assert closed.value.code == 4401
        assert not monitor.monitored(SHOP)
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}  # nothing (no want, no request_frame) came before


def test_rtc_from_a_foreign_origin_is_refused_before_the_handshake():
    """The Origin allowlist stays as defence in depth: a stray rtc from off it never reaches a phone."""
    with TestClient(main_mod.app) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(f"/monitor?shop={SHOP}", headers={"Origin": "https://evil.example"}) as mon:
                mon.send_json({"t": "rtc", "kind": "want", "to": "s1"})
                mon.receive_json()
        assert closed.value.code == 4403
        assert not monitor.monitored(SHOP)
