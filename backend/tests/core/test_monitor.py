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
    for name in ("GEMINI_API_KEY", "AIRA_MOCK", "AIRA_ALLOW_NO_TOKEN", "AIRA_APP_KEY", "AIRA_MONITOR_KEY"):
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
                                                    "status"}


@pytest.mark.parametrize("raw", [
    {"t": "mon", "kind": "nope", "session": "s", "ts": 1},
    {"t": "mon", "kind": "transcript", "session": "s", "ts": 1, "role": "user", "text": "x", "lang": "en",
     "final": True},
    {"t": "mon", "kind": "hand", "session": "s", "ts": 1, "point": [1200, 5], "near": True},
    {"t": "mon", "kind": "vision", "session": "s", "ts": 1, "source": "radar", "objects": []},
    {"t": "mon", "kind": "status", "session": "s", "ts": 1, "state": "sleeping"},
    {"t": "mon", "kind": "frame", "session": "s", "ts": 1, "jpeg_b64": "AAAA", "w": "480", "h": 1, "purpose": "ask"},
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
    assert [m["session"] for m in sub.take()] == [s.session_id, s2.session_id, s.session_id]


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
        return [], [], "no such product"

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
    vision = next(m for m in got if m["kind"] == "vision")
    assert vision["source"] == "guidance" and vision["target"] == "Aavin milk 500 ml" and vision["found"] is False
    assert vision["objects"] == [{"label": "Aavin milk 500 ml", "box": [390, 505, 435, 555], "point": [412, 530]}]


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


# ---------- vision observer ----------
async def test_vision_observer_publishes_objects_and_found_flag(mk_session):
    s, _ = mk_session()
    sub = monitor.subscribe(SHOP)
    assert monitor.on_vision_objects in observers.observers()
    objs = [{"label": "Aavin milk", "point": [500, 400], "box": [450, 350, 550, 450], "distance": "near"},
            {"label": "fridge", "point": [1200.4, -3]},  # out of range: clamped
            {"label": "", "point": [1, 1]},  # no label: dropped
            {"label": "curd"}]
    await observers.on_vision_objects(s, objs, {"source": "camera_ask", "purpose": "ask"})
    await observers.on_vision_objects(s, objs[:1], {"source": "product_find", "target": "Aavin milk"})
    await observers.on_vision_objects(s, objs[3:], {"source": "product_find", "target": "Aavin milk"})
    await observers.on_vision_objects(s, [], "describe")
    got = [m for m in sub.take() if m["kind"] == "vision"]
    assert [m["source"] for m in got] == ["camera_ask", "point", "point", "describe"]
    assert got[0]["objects"] == [
        {"label": "Aavin milk", "box": [450, 350, 550, 450], "point": [500, 400]},
        {"label": "fridge", "box": None, "point": [1000, 0]},
        {"label": "curd", "box": None, "point": None}]
    assert "found" not in got[0] and "target" not in got[0]
    assert got[1]["target"] == "Aavin milk" and got[1]["found"] is True
    assert got[2]["target"] == "Aavin milk" and got[2]["found"] is False
    assert got[3]["objects"] == []


async def test_vision_observer_never_raises_on_junk(mk_session):
    s, _ = mk_session()
    monitor.subscribe(SHOP)
    assert await observers.on_vision_objects(s, [None, 5, {"label": object()}], {"source": "describe"}) >= 1


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
        return {"say": "Order sent"}

    confirm.register_committer("test.commit", commit)
    res = await confirm.propose("order", "Order 30 L?", {"n": 30}, "test.commit")
    await confirm.resolve(res["confirm_id"], "yes")
    events = [m for m in sub.take() if m["kind"] == "event"]
    assert [m["name"] for m in events] == ["confirm_proposed", "confirm_resolved"]
    assert events[0]["detail"] == {"id": res["confirm_id"], "kind": "order", "prompt": "Order 30 L?"}
    assert events[1]["detail"]["id"] == res["confirm_id"] and events[1]["detail"]["answer"] == "yes"
    assert events[1]["detail"]["status"] == "committed" and events[1]["detail"]["say"] == "Order sent"


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


# ---------- key check ----------
def test_key_ok_needs_the_env_and_an_exact_match(monkeypatch):
    assert not monitor.key_ok("anything")  # env unset: nobody gets in
    monkeypatch.setenv("AIRA_MONITOR_KEY", "m-secret")
    assert monitor.key_ok("m-secret")
    assert not monitor.key_ok("m-secre") and not monitor.key_ok("m-secret ") and not monitor.key_ok(None)
    assert not monitor.key_ok("")


# ---------- the /monitor route ----------
def test_monitor_refused_without_env_key(monkeypatch):
    with pytest.raises(WebSocketDisconnect) as closed:
        with TestClient(main_mod.app).websocket_connect(f"/monitor?shop={SHOP}&key=x", headers=ORIGIN):
            pass
    assert closed.value.code == 4403


@pytest.mark.parametrize("query", [f"shop={SHOP}&key=wrong", f"shop={SHOP}", "key=m-secret", ""])
def test_monitor_refused_with_a_wrong_or_missing_key_or_shop(monkeypatch, query):
    monkeypatch.setenv("AIRA_MONITOR_KEY", "m-secret")
    with pytest.raises(WebSocketDisconnect) as closed:
        with TestClient(main_mod.app).websocket_connect(f"/monitor?{query}", headers=ORIGIN):
            pass
    assert closed.value.code == 4403
    assert not monitor.monitored(SHOP)


def test_monitor_refused_from_a_foreign_origin(monkeypatch):
    monkeypatch.setenv("AIRA_MONITOR_KEY", "m-secret")
    for headers in ({"Origin": "https://evil.example"}, {}):
        with pytest.raises(WebSocketDisconnect) as closed:
            with TestClient(main_mod.app).websocket_connect(f"/monitor?shop={SHOP}&key=m-secret", headers=headers):
                pass
        assert closed.value.code == 4403


def test_monitor_sees_sessions_transcripts_frames_and_hands_in_mock_mode(monkeypatch):
    monkeypatch.setenv("AIRA_MONITOR_KEY", "m-secret")
    # One TestClient context = one portal, so both sockets run on the same loop (as they do on Cloud Run).
    with TestClient(main_mod.app) as client, \
            client.websocket_connect(f"/monitor?shop={SHOP}&key=m-secret", headers=ORIGIN) as mon:
        _wait(lambda: monitor.monitored(SHOP))
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json(HELLO)
            ready = ws.receive_json()
            sid = ready["sessionId"]
            start = _recv_until(mon, "session")
            assert start["state"] == "start" and start["lang"] == "en" and start["session"] == sid
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
    monkeypatch.setenv("AIRA_MONITOR_KEY", "m-secret")
    with TestClient(main_mod.app) as client, client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        sid = ws.receive_json()["sessionId"]
        with client.websocket_connect(f"/monitor?shop={SHOP}&key=m-secret", headers=ORIGIN) as mon:
            start = _recv_until(mon, "session")
            assert start["state"] == "start" and start["session"] == sid
        with client.websocket_connect("/monitor?shop=other_shop&key=m-secret", headers=ORIGIN) as mon:
            mon.send_text("ignored")  # the monitor is read-only: inbound text is dropped, the socket stays open
            _wait(lambda: monitor.monitored("other_shop"))
            assert monitor.publish("other_shop", "status", "x", state="idle") == 1
            assert _recv_until(mon, "status")["state"] == "idle"


def test_monitor_sees_live_transcripts_with_customer_role(real_mode, monkeypatch):
    from aira_live.tools import sales
    monkeypatch.setenv("AIRA_MONITOR_KEY", "m-secret")
    with TestClient(main_mod.app) as client, \
            client.websocket_connect(f"/monitor?shop={SHOP}&key=m-secret", headers=ORIGIN) as mon:
        _wait(lambda: monitor.monitored(SHOP))
        with client.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json(HELLO)
            sid = ws.receive_json()["sessionId"]
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
