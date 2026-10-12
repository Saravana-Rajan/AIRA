"""aira_live.monitor_hooks: the publish hook the live monitor registers on, and the producers that feed it
(the phone's ``boxes`` message, a shop camera's frames, ER 2 results)."""
import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from aira_live import main as main_mod
from aira_live import monitor_hooks
from aira_live.diag import RateLimiter
from aira_live.main import app

ORIGIN = {"Origin": "https://aira-shop.web.app"}
HELLO = {"t": "hello", "uid": "u1", "shopId": "sunrise_dairy", "lang": "en", "verbosity": "short",
         "appVersion": "0.1.0"}
CAMERA_HELLO = {**HELLO, "uid": "camera:front"}
BOXES = {"t": "boxes", "source": "mediapipe", "objects": [
    {"label": "chair", "box": [420, 310, 900, 690], "score": 0.82, "severity": "danger"},
    {"label": "bottle", "box": [100, 50, 300, 120], "score": 0.61}]}


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AIRA_MOCK", raising=False)


@pytest.fixture(autouse=True)
def clean_hooks():
    saved = list(monitor_hooks.HOOKS)
    monitor_hooks.HOOKS.clear()
    yield
    monitor_hooks.HOOKS[:] = saved


@pytest.fixture
def published():
    """Registers one async hook; returns the list of (session, msg) it received."""
    got: list[tuple] = []

    async def hook(session, msg):
        got.append((session, msg))

    monitor_hooks.register(hook)
    return got


def jpeg_b64(w: int, h: int) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 80, 40)).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


# ---------- the hook list ----------
def test_nothing_registered_by_default():
    assert monitor_hooks.HOOKS == [] and not monitor_hooks.has_hooks()


async def test_publish_with_no_hook_is_a_cheap_no_op():
    assert await monitor_hooks.monitor_publish("s", {"t": "mon", "kind": "vision"}) == 0


async def test_register_fans_out_copies_and_unregister_stops_it(published):
    seen_sync = []
    off = monitor_hooks.register(lambda s, m: seen_sync.append(m))   # a sync hook works too
    msg = {"t": "mon", "kind": "vision", "objects": []}
    assert await monitor_hooks.monitor_publish("sess", msg) == 2
    assert published[0][0] == "sess" and published[0][1] == msg and seen_sync == [msg]
    published[0][1]["mutated"] = True
    assert "mutated" not in msg
    off()
    off()
    assert await monitor_hooks.monitor_publish("sess", msg) == 1


async def test_a_failing_hook_never_breaks_the_caller_or_the_next_hook(published):
    async def bad(session, msg):
        raise RuntimeError("boom")

    monitor_hooks.HOOKS.insert(0, bad)
    assert await monitor_hooks.monitor_publish("s", {"t": "mon"}) == 1
    assert len(published) == 1


# ---------- message builders ----------
class _S:
    session_id, uid, shop_id = "sid1", "u1", "sunrise_dairy"


def test_vision_message_normalises_objects_and_drops_invented_ones():
    msg = monitor_hooks.vision_message(_S(), "er2", [
        {"label": "table", "point": [600, 500], "box": [200, 100, 900, 900], "distance": "near", "confidence": 0.9},
        {"label": "door", "point": [400, 900]},                                  # no box: box is null
        {"label": "shelf", "box_2d": [300, 200, 600, 500]},                      # ER 2's box_2d, no point
        {"label": "bad box", "point": [1, 1], "box": [500, 500, 100, 100]},      # inverted box -> null box
        {"label": "", "point": [1, 1]},                                          # no label: dropped
        {"label": "nothing"},                                                    # no box, no point: dropped
        {"label": "out", "box": [0, 0, 1001, 10]},                               # out of range: dropped
        "junk",
    ], target="table", found=True, frame_id="f1", context="camera_ask")
    assert msg["t"] == "mon" and msg["kind"] == "vision" and msg["session"] == "sid1" and msg["uid"] == "u1"
    assert msg["shop"] == "sunrise_dairy" and isinstance(msg["ts"], int) and msg["ts"] > 1_700_000_000_000
    assert msg["source"] == "er2" and msg["target"] == "table" and msg["found"] is True
    assert msg["frameId"] == "f1" and msg["context"] == "camera_ask"
    assert msg["objects"] == [
        {"label": "table", "box": [200, 100, 900, 900], "point": [600, 500], "score": 0.9, "distance": "near"},
        {"label": "door", "box": None, "point": [400, 900]},
        {"label": "shelf", "box": [300, 200, 600, 500], "point": None},
        {"label": "bad box", "box": None, "point": [1, 1]},
    ]
    plain = monitor_hooks.vision_message(_S(), "mediapipe", [])
    assert plain["objects"] == [] and "target" not in plain and "found" not in plain and "frameId" not in plain


def test_vision_message_keeps_danger_and_scores_from_the_phone():
    from aira_live.contracts import BoxObject

    msg = monitor_hooks.vision_message(_S(), "mediapipe", [
        BoxObject(label="chair", box=(420, 310, 900, 690), score=0.82, severity="danger"),
        {"label": "bottle", "box": [100, 50, 300, 120], "score": 1.7},
    ])
    assert msg["objects"] == [
        {"label": "chair", "box": [420, 310, 900, 690], "point": None, "score": 0.82, "severity": "danger"},
        {"label": "bottle", "box": [100, 50, 300, 120], "point": None, "score": 1.0},
    ]


def test_frame_message_downscales_to_a_small_preview():
    big = jpeg_b64(1280, 720)
    msg = monitor_hooks.frame_message(_S(), big, frame_id="f9", frame_ts=5.0, purpose="ask")
    assert msg["kind"] == "frame" and msg["frameId"] == "f9" and msg["frameTs"] == 5.0 and msg["purpose"] == "ask"
    assert (msg["w"], msg["h"]) == (480, 270)
    img = Image.open(io.BytesIO(base64.b64decode(msg["jpeg"])))
    assert img.size == (480, 270) and len(msg["jpeg"]) < len(big)
    small = jpeg_b64(320, 240)
    assert monitor_hooks.downscale_jpeg(small)[1:] == (320, 240)           # never upscaled


def test_frame_message_survives_a_bad_jpeg():
    msg = monitor_hooks.frame_message(_S(), "AAAA", frame_id="f", frame_ts=1.0, purpose="ask")
    assert msg["jpeg"] == "AAAA" and msg["w"] == 0


# ---------- the ER 2 shortcut: monitor + the phone's overlay ----------
async def test_publish_er2_goes_to_the_monitor_and_to_the_phone_as_a_boxes_message(mk_session, published):
    s, rec = mk_session()
    objs = [{"label": "Aavin milk", "point": [500, 500], "box": [400, 450, 600, 550], "confidence": 0.9},
            {"label": "door", "point": [400, 900]}]
    assert await monitor_hooks.publish_er2(s, objs, target="Aavin milk", found=True, frame_id="f1") == 1
    assert published[0][1]["source"] == "er2" and published[0][1]["found"] is True
    assert len(published[0][1]["objects"]) == 2
    out = json.loads(rec.msgs[-1].model_dump_json(by_alias=True, exclude_none=True))
    assert out == {"t": "boxes", "source": "er2", "frameId": "f1", "target": "Aavin milk", "found": True,
                   "objects": [{"label": "Aavin milk", "box": [400, 450, 600, 550], "score": 0.9}]}


async def test_publish_er2_sends_nothing_to_the_phone_without_boxes_and_never_raises(mk_session):
    s, rec = mk_session()
    assert await monitor_hooks.publish_er2(s, [{"label": "door", "point": [400, 900]}]) == 0
    assert rec.msgs == []

    class Broken:
        session_id = "x"

        async def send(self, msg):
            raise RuntimeError("socket gone")

    assert await monitor_hooks.publish_er2(Broken(), [{"label": "a", "box": [0, 0, 10, 10]}]) == 0


# ---------- /ws: the phone's boxes and the room camera's frames ----------
def test_boxes_message_is_relayed_to_the_monitor_as_vision_and_never_answered(published):
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ready = ws.receive_json()
        ws.send_json({**BOXES, "frameId": "f3"})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}   # nothing came back for the boxes
    assert len(published) == 1
    session, msg = published[0]
    assert session.session_id == ready["sessionId"]
    assert msg["kind"] == "vision" and msg["source"] == "mediapipe" and msg["frameId"] == "f3"
    assert msg["objects"][0] == {"label": "chair", "box": [420, 310, 900, 690], "point": None, "score": 0.82,
                                 "severity": "danger"}
    assert msg["objects"][1]["label"] == "bottle" and "severity" not in msg["objects"][1]


def test_boxes_before_hello_are_unsupported(published):
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(BOXES)
        assert ws.receive_json()["code"] == "unsupported"
    assert published == []


def test_malformed_boxes_are_unsupported_and_the_socket_stays_open(published):
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_json({"t": "boxes", "source": "mediapipe", "objects": [{"label": "x", "box": [0, 0, 2000, 1]}]})
        assert ws.receive_json()["code"] == "unsupported"
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    assert published == []


def test_boxes_are_rate_limited_per_socket(published, monkeypatch):
    monkeypatch.setattr(main_mod, "_boxes_limiter", lambda: RateLimiter(main_mod.BOXES_MAX_PER_S, clock=lambda: 100.0))
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        for i in range(20):
            ws.send_json({"t": "boxes", "source": "mediapipe", "objects": [], "frameId": f"f{i}"})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    assert main_mod.BOXES_MAX_PER_S >= 5                                     # the phone sends up to 5 a second
    assert [m["frameId"] for _, m in published] == [f"f{i}" for i in range(main_mod.BOXES_MAX_PER_S)]


def test_boxes_with_no_hook_cost_nothing_and_still_get_no_reply():
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_json(BOXES)
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}


def test_room_camera_frames_reach_the_monitor_downscaled_only_while_a_hook_is_registered(published):
    frame = {"t": "frame", "id": "f1", "jpeg": jpeg_b64(1280, 720), "w": 1280, "h": 720, "ts": 1, "purpose": "ask"}
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(CAMERA_HELLO)
        ready = ws.receive_json()
        ws.send_json(frame)
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    frames = [m for _, m in published if m["kind"] == "frame"]
    assert len(frames) == 1
    assert frames[0]["session"] == ready["sessionId"] and frames[0]["uid"] == "camera:front"
    assert (frames[0]["w"], frames[0]["h"]) == (480, 270) and frames[0]["frameId"] == "f1"
    assert len(frames[0]["jpeg"]) < len(frame["jpeg"])


def test_owner_frames_are_not_relayed_and_camera_frames_are_skipped_without_a_hook(published, monkeypatch):
    frame = {"t": "frame", "id": "f1", "jpeg": jpeg_b64(64, 48), "w": 64, "h": 48, "ts": 1, "purpose": "ask"}
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)                          # the owner's chest camera: private, not a room view
        ws.receive_json()
        ws.send_json(frame)
        ws.send_json({"t": "stop"})
        ws.receive_json()
    assert [m["kind"] for _, m in published] == []
    monitor_hooks.HOOKS.clear()
    calls = []
    orig = monitor_hooks.frame_message
    monkeypatch.setattr(monitor_hooks, "frame_message", lambda *a, **k: calls.append(1) or orig(*a, **k))
    with TestClient(app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(CAMERA_HELLO)
        ws.receive_json()
        ws.send_json(frame)
        ws.send_json({"t": "stop"})
        ws.receive_json()
    assert calls == []                               # nobody watching: no downscale, no message
