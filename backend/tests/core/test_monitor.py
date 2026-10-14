"""Live monitor (``/monitor``): a read-only fan-out, per shop, of what a session hears, sees and does.

Everything here is in-process: the hub, the frame throttle/downscale, the speaker roles, the vision observer,
the event hooks and the WebSocket route (TestClient). Nothing is stored and no network is touched.
"""
import asyncio
import base64
import io
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from live_fakes import FakeBackend
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from aira_live import confirm, contracts, monitor, sessions
from aira_live import main as main_mod
from aira_live.contracts import ConfirmMsg, CountMsg, StateMsg, TargetMsg, TranscriptMsg
from aira_live.frames import Frame
from aira_live.live_bridge import LiveEvent
from aira_live.tool_runtime import wrap_tool
from aira_live.vision import observers

ORIGIN = {"Origin": "https://aira-shop.web.app"}
HELLO = {"t": "hello", "uid": "u1", "shopId": "sunrise_dairy", "lang": "en", "verbosity": "short",
         "appVersion": "0.1.0"}
SHOP = "sunrise_dairy"


def jpeg_b64(w: int, h: int, quality: int = 90) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def decode(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def _wait(pred, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while not pred():
        if time.time() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


def _recv_until(ws, kind: str, timeout: float = 3.0) -> dict:
    """Read monitor messages until one of ``kind`` arrives (the TestClient read blocks, so bound it)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = ws.receive_json()
        assert msg["t"] == "mon" and msg["session"] and isinstance(msg["ts"], (int, float))
        if msg["kind"] == kind:
            return msg
    raise AssertionError(f"no {kind!r} monitor message")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("GEMINI_API_KEY", "AIRA_MOCK", "AIRA_ALLOW_NO_TOKEN", "AIRA_APP_KEY", "AIRA_MONITOR_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    monitor.reset()
    yield
    monitor.reset()


@pytest.fixture
def mem_store():
    previous = confirm._store
    store = confirm.MemoryProposalStore()
    confirm.set_store(store)
    yield store
    confirm.set_store(previous)


@pytest.fixture
def real_mode(monkeypatch, fake_db, mem_store):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    backends: list[FakeBackend] = []

    async def fake_prompt(shop_id, lang, verbosity):
        return f"prompt for {shop_id}"

    def factory(session):
        backends.append(FakeBackend())
        return backends[-1]

    monkeypatch.setattr(main_mod, "build_system_prompt", fake_prompt)
    monkeypatch.setattr(main_mod.app.state, "backend_factory", factory)
    return backends


# ---------- contract ----------
def _fixtures() -> list[dict]:
    from pathlib import Path
    for parent in Path(__file__).resolve().parents:
        fix = parent / "shared" / "contracts" / "fixtures" / "monitor_msgs.json"
        if fix.is_file():
            return json.loads(fix.read_text(encoding="utf-8"))
    raise RuntimeError("shared/contracts/fixtures/monitor_msgs.json not found")


def test_monitor_fixtures_validate_round_trip_and_cover_every_kind():
    raws = _fixtures()
    seen = set()
    for raw in raws:
        msg = contracts.monitor_adapter.validate_python(raw)
        assert msg.t == "mon"
        seen.add(msg.kind)
        assert json.loads(contracts.dump_monitor_msg(msg)) == raw
    assert seen == set(contracts.MONITOR_KINDS) == {"session", "transcript", "frame", "vision", "hand", "event",
                                                    "status", "stats"}


@pytest.mark.parametrize("raw", [
    {"t": "mon", "kind": "nope", "session": "s", "ts": 1},
    {"t": "mon", "kind": "transcript", "session": "s", "ts": 1, "role": "user", "text": "x", "lang": "en",
     "final": True},
    {"t": "mon", "kind": "hand", "session": "s", "ts": 1, "point": [1200, 5], "near": True},
    {"t": "mon", "kind": "vision", "session": "s", "ts": 1, "source": "radar", "objects": []},
    {"t": "mon", "kind": "status", "session": "s", "ts": 1, "state": "sleeping"},
    {"t": "mon", "kind": "frame", "session": "s", "ts": 1, "jpeg_b64": "AAAA", "w": "480", "h": 1, "purpose": "ask"},
    {"t": "mon", "kind": "vision", "session": "s", "ts": 1, "source": "er2", "objects": [
        {"label": "x", "box": None, "point": None, "severity": "high"}]},
    {"t": "mon", "kind": "stats", "session": "s", "ts": 1, "turns": 1, "er2Calls": 0, "flashCalls": 0, "fps": 0},
    {"t": "transcript", "role": "aira", "text": "x", "final": True},
])
def test_bad_monitor_msgs_are_rejected(raw):
    assert contracts.parse_monitor_msg(json.dumps(raw)) is None


# ---------- hub ----------
async def test_publish_fans_out_per_shop_and_is_a_no_op_without_monitors():
    assert monitor.publish(SHOP, "status", "s1", state="idle") == 0
    assert not monitor.monitored(SHOP)
    a = monitor.subscribe(SHOP)
    b = monitor.subscribe(SHOP)
    other = monitor.subscribe("other_shop")
    assert monitor.monitored(SHOP) and monitor.monitored("other_shop")
    assert monitor.publish(SHOP, "status", "s1", state="listening") == 2
    for sub in (a, b):
        got = sub.take()
        assert len(got) == 1 and got[0]["kind"] == "status" and got[0]["state"] == "listening"
        assert got[0]["t"] == "mon" and got[0]["session"] == "s1" and got[0]["ts"] > 0
    assert other.take() == []
    monitor.unsubscribe(a)
    monitor.unsubscribe(b)
    assert not monitor.monitored(SHOP)
    assert monitor.publish(SHOP, "status", "s1", state="idle") == 0
    monitor.unsubscribe(other)


async def test_publish_drops_the_oldest_when_a_monitor_is_slow():
    sub = monitor.subscribe(SHOP)
    for i in range(monitor.MAX_QUEUE + 5):
        monitor.publish(SHOP, "event", "s1", name="tick", detail={"i": i})
    got = sub.take()
    assert len(got) == monitor.MAX_QUEUE
    assert got[0]["detail"]["i"] == 5 and got[-1]["detail"]["i"] == monitor.MAX_QUEUE + 4


async def test_publish_never_raises_on_an_invalid_message():
    sub = monitor.subscribe(SHOP)
    assert monitor.publish(SHOP, "status", "s1", state="sleeping") == 0  # not a MonitorState
    assert monitor.publish(SHOP, "nope", "s1") == 0
    assert sub.take() == []


# ---------- frames: only while monitored, throttled, downscaled ----------
async def test_frame_is_not_processed_without_a_monitor(mk_session, monkeypatch):
    s, _ = mk_session()
    calls = []
    monkeypatch.setattr(monitor, "downscale", lambda b64: calls.append(b64) or (b64, 1, 1))
    frame = Frame(id="f1", jpeg_b64=jpeg_b64(640, 480), w=640, h=480, ts=1.0, purpose="ask")
    assert await monitor.publish_frame(s, frame) is False
    assert calls == []


async def test_frame_is_downscaled_to_480_wide_and_published(mk_session):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    frame = Frame(id="f1", jpeg_b64=jpeg_b64(1280, 960), w=1280, h=960, ts=1.0, purpose="guide")
    assert await monitor.publish_frame(s, frame) is True
    got = sub.take()
    assert len(got) == 1 and got[0]["kind"] == "frame" and got[0]["purpose"] == "guide"
    assert got[0]["w"] == 480 and got[0]["h"] == 360
    img = decode(got[0]["jpeg_b64"])
    assert img.size == (480, 360) and img.format == "JPEG"
    assert len(got[0]["jpeg_b64"]) < len(frame.jpeg_b64)


async def test_small_frame_is_re_encoded_but_not_upscaled(mk_session):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    frame = Frame(id="f1", jpeg_b64=jpeg_b64(320, 240), w=320, h=240, ts=1.0, purpose="ask")
    assert await monitor.publish_frame(s, frame) is True
    got = sub.take()[0]
    assert (got["w"], got["h"]) == (320, 240) and decode(got["jpeg_b64"]).size == (320, 240)


async def test_frames_are_throttled_to_4_fps_per_session(mk_session, monkeypatch):
    s, _ = mk_session()
    s2, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    now = [100.0]
    monkeypatch.setattr(monitor, "_monotonic", lambda: now[0])
    b64 = jpeg_b64(64, 48)

    def frame(i):
        return Frame(id=f"f{i}", jpeg_b64=b64, w=64, h=48, ts=float(i), purpose="guide")

    assert await monitor.publish_frame(s, frame(1)) is True
    now[0] += 0.1
    assert await monitor.publish_frame(s, frame(2)) is False  # too soon
    assert await monitor.publish_frame(s2, frame(2)) is True  # another session has its own budget
    now[0] += 0.2  # 0.3 s after the first frame
    assert await monitor.publish_frame(s, frame(3)) is True
    # an unread frame is replaced by its session's newer one (a slow monitor gets the latest picture)
    assert [m["session"] for m in sub.take()] == [s2.session_id, s.session_id]


async def test_preview_frames_are_relayed_up_to_10_fps_per_session(mk_session, monkeypatch):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    now = [100.0]
    monkeypatch.setattr(monitor, "_monotonic", lambda: now[0])
    b64 = jpeg_b64(64, 48)

    def frame(i, purpose="preview"):
        return Frame(id=f"p{i}", jpeg_b64=b64, w=64, h=48, ts=float(i), purpose=purpose)

    assert monitor.PREVIEW_MAX_FPS == 10 and monitor.MAX_FPS == 4
    sent = []

    async def pub(f):
        ok = await monitor.publish_frame(s, f)
        sent.extend(m["purpose"] for m in sub.take())  # read each one before the next replaces it
        return ok

    assert await pub(frame(1)) is True
    now[0] += 0.05
    assert await pub(frame(2)) is False  # 20 fps is too fast even for the preview
    now[0] += 0.06  # 0.11 s after the first: within 10 fps
    assert await pub(frame(3)) is True
    now[0] += 0.11
    assert await pub(frame(4, "guide")) is False  # other purposes keep the 4 fps budget
    now[0] += 0.15
    assert await pub(frame(5, "guide")) is True
    assert sent == ["preview", "preview", "guide"]


async def test_a_monitor_queue_keeps_only_the_newest_frame_of_a_session():
    sub = monitor.subscribe(SHOP)
    monitor.publish(SHOP, "frame", "s1", jpeg_b64="AAAA", w=1, h=1, purpose="preview")
    monitor.publish(SHOP, "status", "s1", state="thinking")
    monitor.publish(SHOP, "frame", "s2", jpeg_b64="BBBB", w=1, h=1, purpose="preview")
    monitor.publish(SHOP, "frame", "s1", jpeg_b64="CCCC", w=1, h=1, purpose="preview")
    got = sub.take()
    assert [(m["kind"], m["session"]) for m in got] == [("status", "s1"), ("frame", "s2"), ("frame", "s1")]
    assert got[-1]["jpeg_b64"] == "CCCC"  # the older frame of s1 was replaced, not queued behind


async def test_undecodable_frame_is_skipped(mk_session):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    frame = Frame(id="f1", jpeg_b64="not-a-jpeg", w=1, h=1, ts=1.0, purpose="ask")
    assert await monitor.publish_frame(s, frame) is False
    assert sub.take() == []


def test_downscale_quality_is_0_6():
    big = jpeg_b64(480, 360, quality=95)
    small, w, h = monitor.downscale(big)
    assert (w, h) == (480, 360) and len(small) < len(big)
    assert monitor.JPEG_QUALITY == 60 and monitor.MAX_WIDTH == 480 and monitor.MAX_FPS == 4


# ---------- transcript roles ----------
async def test_speaker_role_is_owner_without_a_sale(mk_session):
    s, _ = mk_session()
    assert monitor.speaker_role(s, "two litres of milk") == "owner"


async def test_speaker_role_is_customer_during_a_sale_unless_the_line_addresses_aira(mk_session, monkeypatch):
    from aira_live.tools import sales
    s, _ = mk_session()
    monkeypatch.setitem(sales.active_sale, s.session_id, "sale_1")
    assert monitor.speaker_role(s, "one curd and one paneer") == "customer"
    assert monitor.speaker_role(s, "AIRA, how much stock is left?") == "owner"
    assert monitor.speaker_role(s, "ஐரா, எவ்வளவு?") == "owner"


async def test_tool_speaker_overrides_the_heuristic_while_the_sale_is_active(mk_session, monkeypatch):
    from aira_live.tools import sales
    s, _ = mk_session()
    monitor.set_speaker(s.session_id, "owner")
    monkeypatch.setitem(sales.active_sale, s.session_id, "sale_1")
    assert monitor.speaker_role(s, "one curd") == "owner"
    monitor.set_speaker(s.session_id, "customer")
    assert monitor.speaker_role(s, "AIRA give me one curd") == "customer"
    monkeypatch.delitem(sales.active_sale, s.session_id)
    assert monitor.speaker_role(s, "one curd") == "owner"  # the sale is over: the owner is talking again
    monitor.forget(s.session_id)


async def test_sale_start_names_the_customer_as_speaker(mk_session, monkeypatch):
    """The tool registry is frozen, so sale_start takes no speaker parameter: a sale is a customer talking."""
    from aira_live.session import current_session
    from aira_live.tools import sales

    async def no_such_product(shop_id, items, lang):
        return [], [], {"ok": False, "error": "PRODUCT_UNKNOWN", "product": "unobtainium"}

    s, _ = mk_session()
    current_session.set(s)
    monkeypatch.setattr(sales, "resolve_lines", no_such_product)
    assert (await sales.sale_start([{"product": "unobtainium", "qty": 1}]))["ok"] is False
    assert monitor._SPEAKER.get(s.session_id) == "customer"
    assert monitor.speaker_role(s, "one curd") == "owner"  # no sale is open yet: still the owner
    monkeypatch.setitem(sales.active_sale, s.session_id, "sale_1")
    assert monitor.speaker_role(s, "AIRA one curd") == "customer"  # the tool's word wins while the sale is open


async def test_mirror_publishes_transcripts_with_roles(mk_session, monkeypatch):
    from aira_live.tools import sales
    s, _ = mk_session(lang="ta")
    sub = monitor.subscribe(SHOP)
    monitor.mirror(s, TranscriptMsg(role="user", text="ரெண்டு பால்", final=True, lang="ta"))
    monitor.mirror(s, TranscriptMsg(role="aira", text="Two", final=False))
    monkeypatch.setitem(sales.active_sale, s.session_id, "sale_1")
    monitor.mirror(s, TranscriptMsg(role="user", text="one curd", final=True, lang="en"))
    got = [m for m in sub.take() if m["kind"] == "transcript"]
    assert [(m["role"], m["text"], m["lang"], m["final"]) for m in got] == [
        ("owner", "ரெண்டு பால்", "ta", True), ("aira", "Two", "ta", False), ("customer", "one curd", "en", True)]


# ---------- mirror: server messages become events / vision / status ----------
async def test_mirror_maps_state_count_and_target_but_not_confirm(mk_session):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    monitor.mirror(s, StateMsg(entity="order", id="o1", state="ORDER_PLACED", summary="30 L curd sent"))
    monitor.mirror(s, CountMsg(sku="sakthi_curd_1l", counted=28, expected=30, final=False, confidence=0.9))
    monitor.mirror(s, ConfirmMsg(id="cf_1", kind="order", prompt="Order 30 L?"))  # confirm.propose says it once
    monitor.mirror(s, TargetMsg(frame_id="f1", label="Aavin milk 500 ml", point=[412, 530],
                                box=[390, 505, 435, 555], confidence=0.8))
    got = sub.take()
    kinds = [(m["kind"], m.get("name")) for m in got]
    assert ("event", "state") in kinds and ("event", "count") in kinds
    assert ("event", "confirm_proposed") not in kinds
    state = next(m for m in got if m.get("name") == "state")
    assert state["detail"] == {"entity": "order", "id": "o1", "state": "ORDER_PLACED", "summary": "30 L curd sent"}
    # The target's box reaches the monitor through the guidance loop's publish_boxes (source er2), not here.
    assert not [m for m in got if m["kind"] == "vision"]
    target = next(m for m in got if m.get("name") == "target")
    assert target["detail"] == {"label": "Aavin milk 500 ml", "confidence": 0.8}


async def test_mirror_maps_guidance_frames_and_cues_to_status(mk_session):
    from aira_live.contracts import CueMsg, RequestFrameMsg
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    monitor.mirror(s, RequestFrameMsg(purpose="guide", continuous=True))
    monitor.mirror(s, CueMsg(cue="thinking"))
    monitor.mirror(s, RequestFrameMsg(purpose="guide", stop=True))
    got = sub.take()
    assert [(m["kind"], m.get("state") or m.get("name")) for m in got] == [
        ("event", "guidance_start"), ("status", "guiding"), ("status", "thinking"),
        ("event", "guidance_stop"), ("status", "idle")]


# ---------- box producers (monitor_hooks): ER 2 results and the phone's own boxes ----------
async def test_hub_is_registered_with_monitor_hooks_and_publishes_er2_results(mk_session):
    from aira_live import monitor_hooks
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    assert monitor._from_hooks in monitor_hooks.HOOKS
    objs = [{"label": "Aavin milk", "point": [500, 400], "box": [450, 350, 550, 450], "distance": "near",
             "confidence": 0.9},
            {"label": "fridge", "point": [400, 900]},
            {"label": "", "point": [1, 1]},  # no label: dropped
            {"label": "curd"}]  # neither box nor point: dropped by the producer
    await monitor_hooks.publish_er2(s, objs, context="camera_ask", frame_id="f1")
    await monitor_hooks.publish_er2(s, objs[:1], target="Aavin milk", found=True, context="guidance")
    await monitor_hooks.publish_er2(s, [], target="Aavin milk", found=False, context="guidance")
    got = [m for m in sub.take() if m["kind"] == "vision"]
    assert [m["source"] for m in got] == ["er2", "er2", "er2"]
    assert [m["context"] for m in got] == ["camera_ask", "guidance", "guidance"]
    assert got[0]["frameId"] == "f1" and "target" not in got[0] and "found" not in got[0]
    assert got[0]["objects"] == [
        {"label": "Aavin milk", "box": [450, 350, 550, 450], "point": [500, 400], "score": 0.9, "distance": "near"},
        {"label": "fridge", "box": None, "point": [400, 900]}]
    assert got[1]["target"] == "Aavin milk" and got[1]["found"] is True
    assert got[2]["target"] == "Aavin milk" and got[2]["found"] is False and got[2]["objects"] == []
    assert "severity" not in got[0]


async def test_phone_boxes_reach_the_monitor_with_their_source_and_severity(mk_session):
    from aira_live import monitor_hooks
    from aira_live.contracts import BoxObject
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    danger = [BoxObject(label="chair", box=(420, 310, 900, 690), score=0.82, severity="danger"),
              BoxObject(label="bottle", box=(100, 50, 300, 120), score=0.61)]
    await monitor_hooks.monitor_publish(s, monitor_hooks.vision_message(s, "mediapipe", danger[1:], frame_id="f3"))
    await monitor_hooks.monitor_publish(s, monitor_hooks.vision_message(s, "bump", danger))
    got = [m for m in sub.take() if m["kind"] == "vision"]
    assert got[0]["source"] == "mediapipe" and got[0]["frameId"] == "f3" and "severity" not in got[0]
    assert got[0]["objects"] == [{"label": "bottle", "box": [100, 50, 300, 120], "point": None, "score": 0.61}]
    assert got[1]["source"] == "bump" and got[1]["severity"] == "danger"
    assert got[1]["objects"][0] == {"label": "chair", "box": [420, 310, 900, 690], "point": None, "score": 0.82,
                                    "severity": "danger"}


async def test_hook_never_raises_on_junk_and_is_a_no_op_without_a_monitor(mk_session):
    from aira_live import monitor_hooks
    s, _ = mk_session()
    assert await monitor_hooks.publish_er2(s, [{"label": "a", "box": [0, 0, 10, 10]}]) == 1  # the hub ran, silently
    sub = monitor.subscribe(SHOP)
    monitor._from_hooks(s, {"kind": "vision", "source": "radar", "objects": [None, 5, {"label": object()}]})
    monitor._from_hooks(s, {"kind": "frame", "jpeg": "AAAA", "w": 0, "h": 0})
    monitor._from_hooks(s, {"kind": "frame", "jpeg": "AAAA", "w": 4, "h": 3, "purpose": "ask"})
    got = sub.take()
    assert [m["kind"] for m in got] == ["frame"]  # the invalid source was dropped, the sized frame relayed
    assert got[0]["jpeg_b64"] == "AAAA" and got[0]["w"] == 4


async def test_guidance_found_publishes_a_green_target_and_the_event(mk_session):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    monitor.guidance_found(s, "Aavin milk", point=(412, 530), box=(390, 505, 435, 555))
    got = sub.take()
    assert [m["kind"] for m in got] == ["vision", "event", "status"]
    assert got[0]["source"] == "er2" and got[0]["context"] == "guidance" and got[0]["found"] is True
    assert got[0]["objects"] == [{"label": "Aavin milk", "box": [390, 505, 435, 555], "point": [412, 530]}]
    assert got[1]["name"] == "guidance_found" and got[2]["state"] == "found"


# ---------- stats ----------
async def test_stats_count_turns_vision_calls_and_frames(mk_session, monkeypatch):
    from aira_live.session import current_session
    s, _ = mk_session()
    sessions.register(s)
    sub = monitor.subscribe(SHOP)
    try:
        assert monitor.stats_of(s.session_id) == {"turns": 0, "er2_calls": 0, "flash_calls": 0, "vision_ms": None,
                                                  "fps": 0}
        monitor.count_turn(s.session_id)
        monitor.count_turn(s.session_id)
        monitor.count_vision("gemini-robotics-er-2-preview", 900, s.session_id)
        monitor.count_vision("gemini-3.8-flash", 300, s.session_id)
        monitor.count_vision("gemini-3.8-flash", 300)  # no current session: dropped, not invented
        current_session.set(s)
        monitor.count_vision("gemini-robotics-er-2-preview", 1200)  # attributed to the current session
        now = [100.0]
        monkeypatch.setattr(monitor, "_monotonic", lambda: now[0])
        for _ in range(5):
            monitor.count_frame(s.session_id)
        now[0] += 3.0  # the window moved on: those frames no longer count
        monitor.count_frame(s.session_id)
        assert monitor.stats_of(s.session_id) == {"turns": 2, "er2_calls": 2, "flash_calls": 1, "vision_ms": 800,
                                                  "fps": 0.5}
        assert monitor.publish_stats(SHOP) == 1
        got = [m for m in sub.take() if m["kind"] == "stats"]
        assert got == [{"t": "mon", "kind": "stats", "session": s.session_id, "ts": got[0]["ts"], "turns": 2,
                        "er2Calls": 2, "flashCalls": 1, "visionMs": 800, "fps": 0.5}]
        monitor.forget(s.session_id)
        assert monitor.stats_of(s.session_id)["turns"] == 0
    finally:
        sessions.unregister(s)
    assert monitor.publish_stats("nobody_watches") == 0


async def test_stats_ticker_runs_while_the_shop_is_watched(monkeypatch):
    monkeypatch.setattr(monitor, "STATS_INTERVAL_S", 0.01)
    calls = []
    monkeypatch.setattr(monitor, "publish_stats", lambda shop: calls.append(shop))
    sub = monitor.subscribe(SHOP)
    task = monitor._STATS_TASKS[SHOP]
    await asyncio.sleep(0.05)
    assert calls and set(calls) == {SHOP}
    monitor.unsubscribe(sub)
    assert SHOP not in monitor._STATS_TASKS
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


async def test_vision_calls_are_counted_from_generate_json(mk_session, monkeypatch):
    from types import SimpleNamespace
    from aira_live.session import current_session
    from aira_live.vision import gemini
    s, _ = mk_session()
    current_session.set(s)

    class FakeModels:
        async def generate_content(self, model, contents, config):
            return SimpleNamespace(text='{"point": [1, 2]}')

    monkeypatch.setattr(gemini, "client", lambda: SimpleNamespace(aio=SimpleNamespace(models=FakeModels())))
    monkeypatch.setattr(gemini, "model_for", lambda role: "gemini-robotics-er-2-preview")
    monkeypatch.setattr(gemini, "fallback_for", lambda role: None)
    await gemini.generate_json("point", ["x"], "{}")
    st = monitor.stats_of(s.session_id)
    assert st["er2_calls"] == 1 and st["flash_calls"] == 0 and isinstance(st["vision_ms"], int)


# ---------- events: tools, confirm, notify ----------
async def test_tool_calls_are_published_as_events(mk_session):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)

    async def stock_query(product: str | None = None) -> dict:
        return {"say": "20 packs", "ok": True}

    async def broken() -> dict:
        raise RuntimeError("boom")

    await wrap_tool(stock_query, s)(product="milk", tool_context=SimpleNamespace(function_call_id="c1"))
    with pytest.raises(RuntimeError):
        await wrap_tool(broken, s)(tool_context=None)
    got = sub.take()
    events = [m for m in got if m["kind"] == "event"]
    assert [m["name"] for m in events] == ["tool_call", "tool_call"]
    assert events[0]["detail"]["tool"] == "stock_query" and events[0]["detail"]["ok"] is True
    assert events[0]["detail"]["args"] == {"product": "milk"} and events[0]["detail"]["say"] == "20 packs"
    assert events[1]["detail"]["tool"] == "broken" and events[1]["detail"]["ok"] is False
    assert events[1]["detail"]["error"] == "RuntimeError"
    assert [m["state"] for m in got if m["kind"] == "status"][0] == "thinking"


async def test_confirm_propose_and_resolve_publish_events(mk_session, mem_store):
    from aira_live.session import current_session
    s, _ = mk_session()
    current_session.set(s)
    sub = monitor.subscribe(SHOP)

    async def commit(payload, value):
        return {"say_en": "Order sent"}

    confirm.register_committer("test.commit", commit)
    res = await confirm.propose("order", "Order 30 L?", {"n": 30}, "test.commit")
    await confirm.resolve(res["confirm_id"], "yes")
    events = [m for m in sub.take() if m["kind"] == "event"]
    assert [m["name"] for m in events] == ["confirm_proposed", "confirm_resolved"]
    assert events[0]["detail"] == {"id": res["confirm_id"], "kind": "order", "prompt": "Order 30 L?"}
    assert events[1]["detail"]["id"] == res["confirm_id"] and events[1]["detail"]["answer"] == "yes"
    assert events[1]["detail"]["status"] == "committed" and events[1]["detail"]["say"] == "Order sent"  # the English meaning


async def test_notify_publishes_an_event(mk_session):
    """The spoken line itself reaches the monitor as an aira transcript through main's send mirror."""
    s, _ = mk_session()
    sessions.register(s)
    sub = monitor.subscribe(SHOP)
    try:
        assert await sessions.notify(SHOP, "Vendor replied: ok", cue="ack") == 1
    finally:
        sessions.unregister(s)
    got = sub.take()
    ev = [m for m in got if m["kind"] == "event"]
    assert ev and ev[0]["name"] == "notify" and ev[0]["detail"] == {"text": "Vendor replied: ok", "cue": "ack"}


# ---------- access: no key, an Origin allowlist only ----------
def test_there_is_no_monitor_key_any_more():
    """Removed by the owner's decision: no key derivation, no auth message, no env, no CLI."""
    from aira_live import config, contracts as c
    for name in ("shop_key", "key_ok", "authenticate", "main", "AUTH_TIMEOUT_S"):
        assert not hasattr(monitor, name), name
    assert not hasattr(c, "MonAuthMsg")
    assert "monitor_key" not in config.Settings.__dataclass_fields__
    assert contracts.parse_monitor_client_msg(json.dumps({"t": "auth", "key": "k"})) is None


def test_default_monitor_origins_are_the_hosting_site_only():
    from aira_live import config
    assert config.load().monitor_origins == ("https://aira-shop.web.app", "https://aira-shop.firebaseapp.com")
    assert monitor.origin_allowed("https://aira-shop.web.app")
    assert monitor.origin_allowed("https://aira-shop.firebaseapp.com/")  # a trailing slash is ignored
    assert not monitor.origin_allowed("http://localhost:5198")  # local hosts only through AIRA_MONITOR_ORIGINS
    assert not monitor.origin_allowed("http://127.0.0.1:5173")
    assert not monitor.origin_allowed("https://evil.example")
    assert not monitor.origin_allowed("https://aira-shop.web.app.evil.example")
    assert not monitor.origin_allowed("http://aira-shop.web.app")  # scheme matters
    assert not monitor.origin_allowed(None) and not monitor.origin_allowed("") and not monitor.origin_allowed("  ")


def test_monitor_origins_env_replaces_the_default_and_supports_any_port(monkeypatch):
    from aira_live import config
    monkeypatch.setenv("AIRA_MONITOR_ORIGINS", " https://aira-shop.web.app/, http://localhost:*,http://127.0.0.1:* ,,")
    assert config.load().monitor_origins == ("https://aira-shop.web.app", "http://localhost:*", "http://127.0.0.1:*")
    assert monitor.origin_allowed("http://localhost:5198")
    assert monitor.origin_allowed("http://localhost:5173") and monitor.origin_allowed("http://localhost")
    assert monitor.origin_allowed("http://127.0.0.1:4173")
    assert monitor.origin_allowed("https://aira-shop.web.app")
    assert not monitor.origin_allowed("https://aira-shop.firebaseapp.com")  # the env replaces the default
    assert not monitor.origin_allowed("https://localhost:5198")  # the scheme of a ":*" entry is exact
    assert not monitor.origin_allowed("http://localhost.evil.example:5198")
    assert not monitor.origin_allowed("http://localhost:51x8")
    assert not monitor.origin_allowed(None)
    monkeypatch.setenv("AIRA_MONITOR_ORIGINS", " , ")  # blank = the default
    assert config.load().monitor_origins == config.DEFAULT_MONITOR_ORIGINS
    assert monitor.origin_allowed("https://x.example", ["https://x.example"])  # an explicit list


def test_only_the_demo_shop_may_be_watched_in_tokenless_mode(monkeypatch):
    assert monitor.shop_allowed(SHOP)
    assert not monitor.shop_allowed("other_shop")
    monkeypatch.setenv("AIRA_DEMO_SHOP", "other_shop")
    assert monitor.shop_allowed("other_shop") and not monitor.shop_allowed(SHOP)


# ---------- the /monitor route ----------
def _open(client, shop=SHOP):
    """A /monitor socket from the hosting site; nothing is sent, there is no key."""
    mon = client.websocket_connect(f"/monitor?shop={shop}", headers=ORIGIN)
    ws = mon.__enter__()
    return mon, ws


def test_monitor_connects_without_a_key_from_an_allowed_origin():
    with TestClient(main_mod.app).websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
        _wait(lambda: monitor.monitored(SHOP))
        assert monitor.monitors(SHOP) == 1
        assert monitor.publish(SHOP, "status", "x", state="idle") == 1
        assert _recv_until(mon, "status")["state"] == "idle"
    _wait(lambda: not monitor.monitored(SHOP))


def test_monitor_connects_from_the_firebaseapp_origin_too():
    with TestClient(main_mod.app).websocket_connect(f"/monitor?shop={SHOP}", headers={"Origin": "https://aira-shop.firebaseapp.com"}):
        _wait(lambda: monitor.monitored(SHOP))
    _wait(lambda: not monitor.monitored(SHOP))


def test_monitor_ignores_a_key_in_the_url_and_any_auth_message():
    """A stale #key= link or an old dashboard still works: there is nothing to check."""
    with TestClient(main_mod.app).websocket_connect(f"/monitor?shop={SHOP}&key=whatever", headers=ORIGIN) as mon:
        mon.send_json({"t": "auth", "key": "wrong"})  # read-only: ignored like any other text
        _wait(lambda: monitor.monitored(SHOP))
        assert monitor.publish(SHOP, "status", "x", state="idle") == 1
        assert _recv_until(mon, "status")["state"] == "idle"
    _wait(lambda: not monitor.monitored(SHOP))


@pytest.mark.parametrize("query", ["", "shop="])
def test_monitor_without_a_shop_watches_the_demo_shop(query):
    """One shop: ``?shop=`` is optional; left out, the demo shop is watched."""
    with TestClient(main_mod.app).websocket_connect(f"/monitor?{query}", headers=ORIGIN):
        _wait(lambda: monitor.monitored(SHOP))
        assert monitor.monitors(SHOP) == 1
    _wait(lambda: not monitor.monitored(SHOP))


def test_monitor_refused_for_a_shop_that_is_not_the_demo_shop():
    """Tokenless mode: every phone lands in the demo shop, so only that shop may be watched (closed before the
    handshake)."""
    with pytest.raises(WebSocketDisconnect) as closed:
        with TestClient(main_mod.app).websocket_connect("/monitor?shop=other_shop", headers=ORIGIN) as mon:
            mon.receive_json()
    assert closed.value.code == 4403
    assert not monitor.monitored("other_shop") and not monitor.monitored(SHOP)


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {"Origin": "http://localhost:5198"},
                                     {"Origin": "http://aira-shop.web.app"}, {}])
def test_monitor_refused_from_a_foreign_or_missing_origin(headers):
    """The only guard: an Origin off AIRA_MONITOR_ORIGINS (by default the hosting site; localhost is not on
    it) or no Origin at all is closed before the handshake. Not authentication: a forged header passes."""
    with pytest.raises(WebSocketDisconnect) as closed:
        with TestClient(main_mod.app).websocket_connect(f"/monitor?shop={SHOP}", headers=headers):
            pass
    assert closed.value.code == 4403
    assert not monitor.monitored(SHOP)


def test_monitor_origins_env_lets_a_local_dashboard_in(monkeypatch):
    monkeypatch.setenv("AIRA_MONITOR_ORIGINS", "https://aira-shop.web.app,http://localhost:*")
    with TestClient(main_mod.app).websocket_connect(f"/monitor?shop={SHOP}", headers={"Origin": "http://localhost:5198"}):
        _wait(lambda: monitor.monitored(SHOP))
    _wait(lambda: not monitor.monitored(SHOP))
    with pytest.raises(WebSocketDisconnect) as closed:  # the env replaces the default list
        with TestClient(main_mod.app).websocket_connect(f"/monitor?shop={SHOP}", headers={"Origin": "https://aira-shop.firebaseapp.com"}):
            pass
    assert closed.value.code == 4403


def test_monitor_refused_over_the_per_shop_cap(monkeypatch):
    monkeypatch.setattr(monitor, "MAX_MONITORS_PER_SHOP", 1)
    with TestClient(main_mod.app) as client:
        mon, _ws = _open(client)
        try:
            _wait(lambda: monitor.monitors(SHOP) == 1)
            with pytest.raises(WebSocketDisconnect) as closed:
                with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN):
                    pass
            assert closed.value.code == 4403
            assert monitor.monitors(SHOP) == 1
        finally:
            mon.__exit__(None, None, None)
    _wait(lambda: not monitor.monitored(SHOP))


def test_a_new_monitor_first_hears_the_live_sessions():
    with TestClient(main_mod.app) as client, client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            start = _recv_until(mon, "session")  # the first thing it hears is the live session
            assert start["state"] == "start"


def test_monitor_sees_sessions_transcripts_frames_and_hands_in_mock_mode(monkeypatch):
    # One TestClient context = one portal, so both sockets run on the same loop (as they do on Cloud Run).
    with TestClient(main_mod.app) as client, \
            client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
        _wait(lambda: monitor.monitored(SHOP))
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json(HELLO)
            ready = ws.receive_json()
            sid = ready["sessionId"]
            start = _recv_until(mon, "session")
            assert start["state"] == "start" and start["lang"] == "en" and start["session"] == sid
            assert start["uid"] == "u1"
            ws.send_json({"t": "text", "text": "how much milk"})
            ws.receive_json()
            typed = _recv_until(mon, "transcript")
            assert typed["role"] == "owner" and typed["text"] == "how much milk" and typed["final"] is True
            echo = _recv_until(mon, "transcript")
            assert echo["role"] == "aira" and echo["text"].endswith("how much milk")
            ws.send_json({"t": "frame", "id": "f1", "jpeg": jpeg_b64(960, 720), "w": 960, "h": 720, "ts": 1,
                          "purpose": "guide"})
            frame = _recv_until(mon, "frame")
            assert frame["w"] == 480 and frame["h"] == 360 and frame["purpose"] == "guide"
            assert decode(frame["jpeg_b64"]).size == (480, 360)
            ws.send_json({"t": "event", "name": "fingertip_in_target", "data": {"point": [400, 500]}})
            hand = _recv_until(mon, "hand")
            assert hand["point"] == [400, 500] and hand["near"] is True
            ev = _recv_until(mon, "event")  # every client event is an event too, the hand's included
            assert ev["name"] == "client_event" and ev["detail"]["name"] == "fingertip_in_target"
            ws.send_json({"t": "event", "name": "network", "data": {"online": False}})
            ev = _recv_until(mon, "event")
            assert ev["name"] == "client_event" and ev["detail"] == {"name": "network", "data": {"online": False}}
        end = _recv_until(mon, "session")
        assert end["state"] == "end" and end["session"] == sid
    _wait(lambda: not monitor.monitored(SHOP))


def test_late_monitor_gets_the_live_sessions_of_its_shop(monkeypatch):
    with TestClient(main_mod.app) as client, client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        sid = ws.receive_json()["sessionId"]
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            start = _recv_until(mon, "session")
            assert start["state"] == "start" and start["session"] == sid and start["uid"] == "u1"
        with client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
            mon.send_text("ignored")  # after auth the monitor is read-only: inbound text is dropped
            _wait(lambda: monitor.monitors(SHOP) == 1)
            assert monitor.publish(SHOP, "status", "x", state="idle") == 1
            assert _recv_until(mon, "status")["state"] == "idle"


def test_monitor_sees_live_transcripts_with_customer_role(real_mode, monkeypatch):
    from aira_live.tools import sales
    with TestClient(main_mod.app) as client, \
            client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
        _wait(lambda: monitor.monitored(SHOP))
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json(HELLO)
            sid = ws.receive_json()["sessionId"]
            assert ws.receive_json()["t"] == "request_frame"  # the watched session streams its preview
            _wait(lambda: len(real_mode) == 1)
            backend = real_mode[0]
            backend.push(LiveEvent("input_transcript", text="how much milk is left", final=True))
            assert ws.receive_json()["t"] == "transcript"
            owner = _recv_until(mon, "transcript")
            assert owner["role"] == "owner" and owner["text"] == "how much milk is left"
            sales.active_sale[sid] = "sale_1"
            try:
                backend.push(LiveEvent("input_transcript", text="one curd please", final=True))
                assert ws.receive_json()["t"] == "transcript"
                cust = _recv_until(mon, "transcript")
                assert cust["role"] == "customer" and cust["text"] == "one curd please"
                backend.push(LiveEvent("output_transcript", text="Twenty rupees.", final=True))
                assert ws.receive_json()["t"] == "transcript"
                aira = _recv_until(mon, "transcript")
                assert aira["role"] == "aira" and aira["text"] == "Twenty rupees."
            finally:
                sales.active_sale.pop(sid, None)


def test_frames_are_not_downscaled_when_nobody_watches(monkeypatch):
    calls = []
    monkeypatch.setattr(monitor, "downscale", lambda b64: calls.append(b64) or (b64, 1, 1))
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_json({"t": "frame", "id": "f1", "jpeg": jpeg_b64(64, 48), "w": 64, "h": 48, "ts": 1,
                      "purpose": "ask"})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    assert calls == []


def test_room_camera_session_is_marked_by_uid_and_its_frames_reach_the_monitor(monkeypatch):
    with TestClient(main_mod.app) as client, \
            client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
        _wait(lambda: monitor.monitored(SHOP))
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json({**HELLO, "uid": "camera:front"})
            sid = ws.receive_json()["sessionId"]
            start = _recv_until(mon, "session")
            assert start["session"] == sid and start["uid"] == "camera:front"
            ws.send_json({"t": "frame", "id": "f1", "jpeg": jpeg_b64(1280, 720), "w": 1280, "h": 720, "ts": 1,
                          "purpose": "ask"})
            frame = _recv_until(mon, "frame")
            assert frame["session"] == sid and (frame["w"], frame["h"]) == (480, 270)


def test_monitor_hears_stats_for_a_live_session(monkeypatch):
    monkeypatch.setattr(monitor, "STATS_INTERVAL_S", 0.05)
    with TestClient(main_mod.app) as client, \
            client.websocket_connect(f"/monitor?shop={SHOP}", headers=ORIGIN) as mon:
        _wait(lambda: monitor.monitored(SHOP))
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json(HELLO)
            sid = ws.receive_json()["sessionId"]
            stats = _recv_until(mon, "stats")
            assert stats["session"] == sid
            assert stats == {"t": "mon", "kind": "stats", "session": sid, "ts": stats["ts"], "turns": 0,
                             "er2Calls": 0, "flashCalls": 0, "visionMs": None, "fps": 0}
