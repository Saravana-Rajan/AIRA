"""The monitor's preview stream and diarization-based speaker roles.

While at least one monitor watches a shop, every phone session of the shop streams small ``preview`` frames
(2-3 a second); the server relays them to the monitors only: never to Gemini, never stored. When the last
monitor leaves, the stream stops. Diarization labels name who is talking during a sale.
"""
import asyncio
import base64
import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from aira_live import monitor, sessions
from aira_live import main as main_mod
from aira_live.contracts import RequestFrameMsg
from aira_live.frames import Frame

ORIGIN = {"Origin": "https://aira-shop.web.app"}
HELLO = {"t": "hello", "uid": "u1", "shopId": "sunrise_dairy", "lang": "en", "verbosity": "short",
         "appVersion": "0.1.0"}
SHOP = "sunrise_dairy"
import hashlib
import hmac

KEY = hmac.new(b"m-secret", SHOP.encode(), hashlib.sha256).hexdigest()[:32]
AUTH = {"t": "auth", "key": KEY}


def jpeg_b64(w: int, h: int) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (20, 200, 30)).save(buf, format="JPEG", quality=50)
    return base64.b64encode(buf.getvalue()).decode()


def _requests(rec):
    return [(m.purpose, m.continuous, m.stop) for m in rec.msgs if isinstance(m, RequestFrameMsg)]


async def settle() -> None:
    for _ in range(30):
        await asyncio.sleep(0)


def _wait(pred, timeout: float = 2.0) -> None:
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
    for name in ("GEMINI_API_KEY", "AIRA_MOCK", "AIRA_ALLOW_NO_TOKEN", "AIRA_APP_KEY", "AIRA_MONITOR_KEY"):
        monkeypatch.delenv(name, raising=False)
    monitor.reset()
    yield
    monitor.reset()


# ---------- preview start / stop ----------
async def test_preview_starts_with_the_first_monitor_and_stops_with_the_last(mk_session):
    s, rec = mk_session()
    cam, cam_rec = mk_session()
    cam.uid = "camera:door"  # a shop camera only sends frames: nothing is asked of it
    sessions.register(s)
    sessions.register(cam)
    try:
        a = monitor.subscribe(SHOP)
        await settle()
        assert _requests(rec) == [("preview", True, None)] and _requests(cam_rec) == []
        b = monitor.subscribe(SHOP)
        await settle()
        assert len(_requests(rec)) == 1  # a second monitor changes nothing
        monitor.unsubscribe(a)
        await settle()
        assert len(_requests(rec)) == 1  # one monitor still watches
        monitor.unsubscribe(b)
        await settle()
        assert _requests(rec) == [("preview", True, None), ("preview", None, True)]
        assert _requests(cam_rec) == []
    finally:
        sessions.unregister(s)
        sessions.unregister(cam)


async def test_a_session_that_joins_a_watched_shop_starts_its_preview(mk_session):
    s, rec = mk_session()
    other, other_rec = mk_session("other_shop")
    monitor.subscribe(SHOP)
    await monitor.preview_on_join(s)
    await monitor.preview_on_join(other)
    assert _requests(rec) == [("preview", True, None)] and _requests(other_rec) == []
    cam, cam_rec = mk_session()
    cam.uid = "camera:door"
    await monitor.preview_on_join(cam)
    assert _requests(cam_rec) == []


async def test_preview_requests_never_raise_into_the_hub(mk_session):
    s, rec = mk_session()

    async def broken(msg):
        raise RuntimeError("socket gone")

    s._send = broken
    sessions.register(s)
    try:
        sub = monitor.subscribe(SHOP)
        await settle()
        monitor.unsubscribe(sub)
        await settle()
        assert not monitor.monitored(SHOP)
    finally:
        sessions.unregister(s)


# ---------- preview frames: relayed as they are, never to Gemini ----------
async def test_a_small_preview_frame_is_relayed_without_re_encoding(mk_session, monkeypatch):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)

    def never(b64):
        raise AssertionError("a preview frame must not be decoded")

    monkeypatch.setattr(monitor, "downscale", never)
    frame = Frame(id="p1", jpeg_b64=jpeg_b64(480, 270), w=480, h=270, ts=1.0, purpose="preview")
    assert await monitor.publish_frame(s, frame) is True
    got = sub.take()
    assert len(got) == 1 and got[0]["purpose"] == "preview" and got[0]["jpeg_b64"] == frame.jpeg_b64
    assert (got[0]["w"], got[0]["h"]) == (480, 270)
    assert monitor.stats_of(s.session_id)["fps"] > 0


async def test_an_oversize_preview_frame_is_still_downscaled(mk_session):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    frame = Frame(id="p1", jpeg_b64=jpeg_b64(960, 540), w=960, h=540, ts=1.0, purpose="preview")
    assert await monitor.publish_frame(s, frame) is True
    got = sub.take()[0]
    assert (got["w"], got["h"]) == (480, 270)


def test_preview_frames_reach_the_monitor_but_are_never_stored_for_the_tools(monkeypatch):
    monkeypatch.setenv("AIRA_MONITOR_KEY", "m-secret")
    with TestClient(main_mod.app) as client, \
            client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
        mon.send_json(AUTH)
        _wait(lambda: monitor.monitored(SHOP))
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json(HELLO)
            ready = ws.receive_json()
            assert ready["t"] == "ready"
            # the server asks the phone for the preview stream as soon as the session joins a watched shop
            req = ws.receive_json()
            assert req == {"t": "request_frame", "purpose": "preview", "continuous": True}
            ws.send_json({"t": "frame", "id": "p1", "jpeg": jpeg_b64(480, 270), "w": 480, "h": 270, "ts": 5,
                          "purpose": "preview"})
            frame = _recv_until(mon, "frame")
            assert frame["purpose"] == "preview" and (frame["w"], frame["h"]) == (480, 270)
            live = [s for s, _ in sessions.by_shop(SHOP)]
            assert len(live) == 1 and live[0].frames.latest(5) == []  # nothing for camera_ask & co to read
            ws.send_json({"t": "frame", "id": "g1", "jpeg": jpeg_b64(480, 270), "w": 480, "h": 270, "ts": 6,
                          "purpose": "guide"})  # (throttled on the monitor: 4 fps a session)
            _wait(lambda: len(live[0].frames.latest(5)) == 1)
            assert [f.purpose for f in live[0].frames.latest(5)] == ["guide"]
        _recv_until(mon, "session")


def test_the_last_monitor_leaving_stops_the_phone_preview(monkeypatch):
    monkeypatch.setenv("AIRA_MONITOR_KEY", "m-secret")
    with TestClient(main_mod.app) as client, client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["t"] == "ready"
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            mon.send_json(AUTH)
            _recv_until(mon, "session")
            assert ws.receive_json() == {"t": "request_frame", "purpose": "preview", "continuous": True}
        _wait(lambda: not monitor.monitored(SHOP))
        assert ws.receive_json() == {"t": "request_frame", "purpose": "preview", "stop": True}


# ---------- who is talking: diarization ----------
async def test_speaker_role_follows_diarization_labels_during_a_sale(mk_session, monkeypatch):
    from aira_live.tools import sales

    s, _ = mk_session()
    monitor.note_speaker(s.session_id, "speaker_1")  # the first (and most frequent) voice is Sam
    monitor.note_speaker(s.session_id, "speaker_1")
    assert monitor.speaker_role(s, "two litres of milk") == "owner"
    monitor.note_speaker(s.session_id, "speaker_2")
    assert monitor.speaker_role(s, "one curd please") == "owner"  # no sale open: nobody is a customer yet
    monkeypatch.setitem(sales.active_sale, s.session_id, "sale_1")
    assert monitor.speaker_role(s, "one curd please") == "customer"
    monitor.note_speaker(s.session_id, "speaker_1")
    assert monitor.speaker_role(s, "one curd please") == "owner"
    monitor.note_speaker(s.session_id, "speaker_2")
    assert monitor.speaker_role(s, "AIRA, how much is left?") == "owner"  # naming AIRA still wins
    monitor.set_speaker(s.session_id, "owner")  # a sale tool's word wins over the labels
    assert monitor.speaker_role(s, "one curd please") == "owner"
    monitor.set_speaker(s.session_id, None)
    assert monitor.speaker_role(s, "one curd please") == "customer"
    monitor.forget(s.session_id)
    assert monitor.speaker_role(s, "one curd please") == "customer"  # back to the sale heuristic
    monitor.note_speaker(s.session_id, "")  # junk labels are ignored
    assert monitor.speaker_role(s, "one curd please") == "customer"
