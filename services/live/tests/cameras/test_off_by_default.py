"""Off by default: no cameras in config/runtime means no watcher task, no ffmpeg and no Gemini call. The camera
role on /ws (uid ``camera:<name>``) gets no Live bridge."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from aira_live import remote_config as rc
from aira_live.cameras import watch


@pytest.fixture(autouse=True)
def clean():
    rc._reset()
    yield
    rc._reset()


def test_runtime_config_keeps_the_cameras_list_and_defaults_to_empty():
    assert rc.cameras() == []
    assert rc.parse_doc({"cameras": [{"name": "a"}], "pointing_model": "x"}) == {"cameras": [{"name": "a"}], "pointing_model": "x"}
    assert rc.parse_doc({"cameras": "nope"}) == {}
    rc._cache.update({"cameras": [{"name": "front", "source": "phone", "modes": ["counter"]}]})
    assert rc.cameras()[0]["name"] == "front"


async def test_start_if_configured_starts_nothing_by_default(monkeypatch):
    started = []
    monkeypatch.setattr(watch, "build_source", lambda cfg: started.append(cfg))
    tasks = await watch.start_if_configured()
    assert tasks == [] and started == []


async def test_start_if_configured_starts_one_task_per_enabled_camera(monkeypatch):
    class Src:
        name = "x"

        async def frames(self):
            await asyncio.sleep(3600)
            yield b"", 0.0

        async def close(self):
            pass

    built = []

    def build(cfg):
        built.append(cfg.name)
        return Src()

    monkeypatch.setattr(watch, "build_source", build)
    rc._cache.update({"cameras": [
        {"name": "front", "source": "phone", "modes": ["counter"]},
        {"name": "off", "source": "rtsp", "modes": ["security"], "enabled": False},
        {"name": "shelf", "source": "rtsp", "modes": ["shelf"]},
    ]})
    rc._fetched_at = __import__("time").monotonic()   # fresh cache: refresh() is a no-op in tests
    tasks = await watch.start_if_configured()
    try:
        assert built == ["front", "shelf"] and len(tasks) == 2
        assert all(not t.done() for t in tasks)
    finally:
        await watch.stop(tasks)
    assert all(t.cancelled() or t.done() for t in tasks)


def test_main_lifespan_starts_and_stops_the_watchers(monkeypatch):
    from aira_live import main as main_mod

    calls = []

    async def fake_start():
        calls.append("start")
        return ["task"]

    async def fake_stop(tasks):
        calls.append(("stop", tasks))

    monkeypatch.setattr(watch, "start_if_configured", fake_start)
    monkeypatch.setattr(watch, "stop", fake_stop)
    with TestClient(main_mod.app):
        assert calls == ["start"]
    assert calls == ["start", ("stop", ["task"])]


def test_a_failing_camera_start_never_blocks_the_service(monkeypatch):
    from aira_live import main as main_mod

    async def boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(watch, "start_if_configured", boom)
    with TestClient(main_mod.app) as client:
        assert client.get("/health").status_code == 200


def test_camera_role_hello_gets_ready_but_no_live_bridge(monkeypatch):
    """Real mode: a shop-camera phone is a frame source, not a Live conversation (no Gemini Live cost)."""
    from aira_live import main as main_mod, sessions

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    monkeypatch.delenv("AIRA_MOCK", raising=False)
    started = []

    async def fake_start_live(session, handle=None):
        started.append(session.uid)
        return None

    monkeypatch.setattr(main_mod, "_start_live", fake_start_live)
    hello = {"t": "hello", "uid": "camera:front", "shopId": "murugan_dairy", "lang": "en", "verbosity": "short",
             "appVersion": "0.1.0"}
    with TestClient(main_mod.app).websocket_connect("/ws") as ws:
        ws.send_json(hello)
        ready = ws.receive_json()
        assert ready["t"] == "ready" and ready["mock"] is False
        assert started == []
        assert [s.uid for s, _ in sessions.by_shop("murugan_dairy")] == ["camera:front"]
        ws.send_json({"t": "frame", "id": "f1", "jpeg": "AAAA", "w": 4, "h": 4, "ts": 1.0, "purpose": "ask"})
        ws.send_json({"t": "hello", **{k: v for k, v in hello.items() if k != "uid"}, "uid": "u-owner"})
        assert ws.receive_json()["t"] == "ready"
    assert started == ["u-owner"]
