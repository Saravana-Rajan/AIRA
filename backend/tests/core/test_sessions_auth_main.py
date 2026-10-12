"""Session registry + notify, hello auth (no Firebase Auth: tokenless hello lands in the demo shop) and the
real-mode wiring of ``main.py`` (Live bridge, guidance hooks, optional routers, /health).

No network: real mode runs on the in-memory ``FakeBackend`` and a fake system prompt.
"""
import asyncio
import base64
import importlib
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from live_fakes import FakeBackend
from starlette.websockets import WebSocketDisconnect

from aira_live import auth, confirm, sessions
from aira_live import main as main_mod
from aira_live.i18n import t
from aira_live.live_bridge import NOTICE_PREFIX, LiveEvent
from aira_live.tool_runtime import live_tools
from aira_live.tools import NON_BLOCKING_TOOLS, TOOL_FUNCS

DEMO = "sunrise_dairy"
HELLO = {"t": "hello", "uid": "u1", "shopId": DEMO, "lang": "en", "verbosity": "short", "appVersion": "0.1.0"}
ORIGIN = {"Origin": "https://aira-shop.web.app"}  # every browser upgrade carries Origin; without it /ws is refused


def _wait(pred, timeout: float = 2.0) -> None:
    """Poll from the test thread (the server runs in TestClient's portal thread)."""
    deadline = time.time() + timeout
    while not pred():
        if time.time() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


def _registered(shop_id: str) -> list[str]:
    return [s.session_id for s, _ in sessions.by_shop(shop_id)]


def _tool_name(tool) -> str:
    return getattr(tool, "name", None) or tool.__name__


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GEMINI_API_KEY", "AIRA_MOCK", "AIRA_ALLOW_NO_TOKEN", "AIRA_DEMO_SHOP", "AIRA_APP_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def mem_store():
    previous = confirm._store
    store = confirm.MemoryProposalStore()
    confirm.set_store(store)
    yield store
    confirm.set_store(previous)


@pytest.fixture
def real_mode(monkeypatch, fake_db, mem_store):
    """GEMINI_API_KEY set (real mode), tokenless hello allowed, every Live connection is a new FakeBackend."""
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


# ---------- sessions: registry + notify ----------
async def test_notify_reaches_every_live_session_of_that_shop_only(mk_session):
    s1, r1 = mk_session(shop="sunrise")
    s2, r2 = mk_session(shop="sunrise")
    s3, r3 = mk_session(shop="other")
    spoken = []

    class Bridge:
        async def speak_notice(self, text):
            spoken.append(text)

    sessions.register(s1, Bridge())
    sessions.register(s2)
    sessions.register(s3)
    try:
        n = await sessions.notify("sunrise", "Sakthi vendor confirmed: delivery at 6 pm.")
        assert n == 2 and spoken == ["Sakthi vendor confirmed: delivery at 6 pm."]
        assert [m.t for m in r1.msgs] == ["transcript", "cue"] and r1.msgs[0].text.startswith("Sakthi vendor")
        assert r1.msgs[1].cue == "ack" and [m.t for m in r2.msgs] == ["transcript", "cue"]
        assert r3.msgs == []
    finally:
        for s in (s1, s2, s3):
            sessions.unregister(s.session_id)
    assert await sessions.notify("sunrise", "x") == 0


async def test_registry_attach_and_unregister_by_id_or_session(mk_session):
    s, _ = mk_session(shop="reg_shop")
    bridge = object()
    sessions.register(s)
    sessions.attach_bridge(s.session_id, bridge)
    assert sessions.by_shop("reg_shop") == [(s, bridge)]
    sessions.unregister(s.session_id)
    assert sessions.by_shop("reg_shop") == []
    sessions.register(s)
    sessions.unregister(s)  # the foundation's callers pass the Session itself
    assert sessions.by_shop("reg_shop") == []
    sessions.attach_bridge("gone", bridge)  # a session that already left is ignored
    sessions.unregister("gone")


async def test_notify_survives_a_failing_bridge(mk_session):
    bad, r_bad = mk_session(shop="nb_shop")
    good, r_good = mk_session(shop="nb_shop")

    class Broken:
        async def speak_notice(self, text):
            raise RuntimeError("live gone")

    sessions.register(bad, Broken())
    sessions.register(good)
    try:
        assert await sessions.notify("nb_shop", "Low stock: Aavin milk", cue="warn") == 1
        assert r_good.msgs[-1].cue == "warn"
    finally:
        sessions.unregister(bad)
        sessions.unregister(good)


# ---------- auth.verify_hello (no Firebase Auth) ----------
async def test_tokenless_hello_ignores_the_client_shop_and_uses_the_demo_shop(monkeypatch):
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    r = await auth.verify_hello(None, "u1", "someone_elses_shop", mock=False)
    assert r == auth.AuthResult(uid="u1", shop_id=DEMO, role="owner")
    assert auth.DEMO_SHOP == DEMO


async def test_demo_shop_comes_from_env(monkeypatch):
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    monkeypatch.setenv("AIRA_DEMO_SHOP", "test_shop")
    assert (await auth.verify_hello(None, "u1", DEMO, mock=False)).shop_id == "test_shop"


async def test_mock_mode_without_token_lands_in_the_demo_shop():
    r = await auth.verify_hello(None, "dev", "sunrise", mock=True)
    assert r.shop_id == DEMO and r.uid == "dev"


@pytest.mark.parametrize("token", [None, ""])
async def test_no_token_is_refused_when_tokenless_mode_is_off(token):
    with pytest.raises(auth.AuthError):
        await auth.verify_hello(token, "dev", DEMO, mock=False)


async def test_a_token_never_unlocks_another_shop(monkeypatch):
    # There is no Firebase Auth to check a token against, so a token grants nothing beyond tokenless mode.
    with pytest.raises(auth.AuthError):
        await auth.verify_hello("forged-token", "u9", "other_shop", mock=False)
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    assert (await auth.verify_hello("forged-token", "u9", "other_shop", mock=False)).shop_id == DEMO


# ---------- /health ----------
def test_health_and_healthz_alias():
    client = TestClient(main_mod.app)
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["ok"] is True
    assert client.get("/healthz").json() == health.json() == {"ok": True, "mock": True}


# ---------- guidance hooks (Sarmitha's aira_live.tools.guidance, imported lazily) ----------
def test_client_events_and_stop_reach_guidance_hooks(monkeypatch, fake_db):
    calls = {"event": [], "stop": []}

    async def handle_client_event(session, name, data):
        calls["event"].append((session.session_id, name, data))

    async def stop_guidance(session):
        calls["stop"].append(session.session_id)

    monkeypatch.setitem(sys.modules, "aira_live.tools.guidance",
                        SimpleNamespace(handle_client_event=handle_client_event, stop_guidance=stop_guidance))
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({**HELLO, "shopId": "sunrise"})
        sid = ws.receive_json()["sessionId"]
        ws.send_json({"t": "event", "name": "fingertip_in_target", "data": {"label": "Sakthi curd"}})
        ws.send_json({"t": "event", "name": "visibility", "data": {"hidden": True}})
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
    _wait(lambda: len(calls["stop"]) >= 2)  # disconnect cleanup runs after the client closes
    assert calls["event"] == [(sid, "fingertip_in_target", {"label": "Sakthi curd"}),
                              (sid, "visibility", {"hidden": True})]
    assert calls["stop"] == [sid, sid]  # once on stop, once on disconnect


def test_broken_guidance_hook_never_closes_the_socket(monkeypatch, fake_db):
    async def boom(*a):
        raise RuntimeError("guidance bug")

    monkeypatch.setitem(sys.modules, "aira_live.tools.guidance",
                        SimpleNamespace(handle_client_event=boom, stop_guidance=boom))
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        ws.send_json({"t": "event", "name": "fingertip_in_target", "data": {}})
        ws.send_json({"t": "text", "text": "still alive?"})
        assert ws.receive_json()["text"] == "(mock) you said: still alive?"
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}


# ---------- AIRA_APP_KEY: the shared demo key the app sends as hello.idToken ----------
async def test_app_key_must_match_the_hello_token(monkeypatch):
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    monkeypatch.setenv("AIRA_APP_KEY", "k-secret")
    assert (await auth.verify_hello("k-secret", "u1", DEMO, mock=False)).shop_id == DEMO
    for bad in (None, "", "k-secre", "K-SECRET", "k-secret "):
        with pytest.raises(auth.AuthError):
            await auth.verify_hello(bad, "u1", DEMO, mock=False)


async def test_app_key_is_not_checked_in_mock_mode_and_not_required_when_unset(monkeypatch):
    monkeypatch.setenv("AIRA_APP_KEY", "k-secret")
    assert (await auth.verify_hello(None, "dev", DEMO, mock=True)).shop_id == DEMO
    monkeypatch.delenv("AIRA_APP_KEY")
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    assert (await auth.verify_hello(None, "u1", DEMO, mock=False)).shop_id == DEMO


async def test_app_key_alone_does_not_switch_tokenless_mode_on(monkeypatch):
    monkeypatch.setenv("AIRA_APP_KEY", "k-secret")  # AIRA_ALLOW_NO_TOKEN unset: there is still no Firebase Auth
    with pytest.raises(auth.AuthError):
        await auth.verify_hello("k-secret", "u1", DEMO, mock=False)


def test_ws_hello_needs_the_app_key_when_one_is_set(real_mode, monkeypatch):
    monkeypatch.setenv("AIRA_APP_KEY", "k-secret")
    for token in ({}, {"idToken": "wrong"}):
        with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json({**HELLO, **token})
            assert ws.receive_json() == {"t": "error", "code": "permission", "say": t("auth.denied", "en")}
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 4403
    assert real_mode == []
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({**HELLO, "idToken": "k-secret"})
        assert ws.receive_json()["t"] == "ready"
    assert len(real_mode) == 1


# ---------- hello in real mode ----------
def test_real_mode_tokenless_hello_ignores_the_client_shop(real_mode):
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({**HELLO, "shopId": "other_shop"})
        ready = ws.receive_json()
        assert ready["t"] == "ready" and ready["mock"] is False
        assert real_mode[0].system_prompt == f"prompt for {DEMO}"
        assert ready["sessionId"] in _registered(DEMO) and _registered("other_shop") == []


def test_hello_without_token_is_refused_and_closed_when_tokenless_mode_is_off(real_mode, monkeypatch):
    monkeypatch.delenv("AIRA_ALLOW_NO_TOKEN")
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json() == {"t": "error", "code": "permission", "say": t("auth.denied", "en")}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4403
    assert real_mode == []  # Live was never started


def test_ws_real_mode_wires_bridge_audio_text_and_transcripts(real_mode):
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({**HELLO, "idToken": "ignored"})
        ready = ws.receive_json()
        sid = ready["sessionId"]
        fb = real_mode[0]
        assert ready["t"] == "ready" and ready["mock"] is False
        assert sorted(_tool_name(x) for x in fb.tools) == sorted(f.__name__ for f in TOOL_FUNCS)
        ws.send_json({"t": "audio", "pcm16": base64.b64encode(b"\x00\x01").decode()})
        _wait(lambda: fb.sent_audio)
        assert fb.sent_audio == [b"\x00\x01"]
        fb.push(LiveEvent("output_transcript", text="Hello Sam.", final=True))
        assert ws.receive_json() == {"t": "transcript", "role": "aira", "text": "Hello Sam.", "final": True}
        ws.send_json({"t": "text", "text": "how much curd is left?"})
        _wait(lambda: fb.sent_text)
        assert fb.sent_text == ["how much curd is left?"]
        ws.send_json({"t": "stop"})
        assert ws.receive_json() == {"t": "cue", "cue": "done"}
        fb.push(LiveEvent("audio", audio=b"\x05\x06"))  # the rest of the stopped answer is dropped
        fb.push(LiveEvent("input_transcript", text="Next question.", final=True))
        # Events arrive in order, so the dropped audio would have come first.
        assert ws.receive_json() == {"t": "transcript", "role": "user", "text": "Next question.", "final": True,
                                     "lang": "en"}
    _wait(lambda: fb.closed and sid not in _registered(DEMO))  # disconnect closes Live and unregisters


def test_live_tools_mark_delivery_count_and_sale_pay_non_blocking(mk_session):
    from google.adk.tools import FunctionTool
    from google.genai import types

    assert NON_BLOCKING_TOOLS == frozenset({"delivery_count", "sale_pay", "walk_to"})
    s, _ = mk_session()
    tools = live_tools(TOOL_FUNCS, s, NON_BLOCKING_TOOLS)
    by_name = {_tool_name(x): x for x in tools}
    assert set(by_name) == {f.__name__ for f in TOOL_FUNCS}
    for name in NON_BLOCKING_TOOLS:
        tool = by_name[name]
        assert isinstance(tool, FunctionTool) and tool.behavior == types.Behavior.NON_BLOCKING
    assert not isinstance(by_name["confirm_action"], FunctionTool)


async def test_non_blocking_tool_still_runs_with_its_session(mk_session):
    from google.adk.tools import FunctionTool

    from aira_live.session import get_session

    s, _ = mk_session()

    async def sale_pay(method: str) -> dict:
        """Wait for payment."""
        return {"say": f"waiting for {method}", "session": get_session().session_id}

    (tool,) = live_tools([sale_pay], s, frozenset({"sale_pay"}))
    assert isinstance(tool, FunctionTool)
    result = await tool.func(method="upi", tool_context=SimpleNamespace(function_call_id="fc1"))
    assert result == {"say": "waiting for upi", "session": s.session_id}
    assert s.tool_results == [{"tool": "sale_pay", "call_id": "fc1", "result": result}]


def test_notify_speaks_into_the_live_session(real_mode):
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ws.receive_json()
        fb = real_mode[0]
        assert ws.portal.call(sessions.notify, DEMO, "Sakthi vendor confirmed: delivery at 6 pm.") >= 1
        assert ws.receive_json() == {"t": "transcript", "role": "aira",
                                     "text": "Sakthi vendor confirmed: delivery at 6 pm.", "final": True, "lang": "en"}
        assert ws.receive_json() == {"t": "cue", "cue": "ack"}
        assert len(fb.sent_text) == 1 and fb.sent_text[0].startswith(NOTICE_PREFIX)
        assert "Sakthi vendor confirmed" in fb.sent_text[0]


def test_live_start_failure_reports_network_error_and_keeps_the_socket(real_mode, monkeypatch):
    class Broken(FakeBackend):
        async def start(self, *a):
            raise ConnectionError("live down")

    broken: list[Broken] = []

    def factory(session):
        broken.append(Broken())
        return broken[-1]

    monkeypatch.setattr(main_mod.app.state, "backend_factory", factory)
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json() == {"t": "error", "code": "network", "say": t("live.unavailable", "en")}
        ready = ws.receive_json()
        assert ready["t"] == "ready" and ready["sessionId"] in _registered(DEMO)
        ws.send_json({"t": "text", "text": "hello?"})
        assert ws.receive_json()["text"] == "(mock) you said: hello?"
    assert broken and broken[0].closed  # the half-open backend is closed


def test_confirm_reply_in_real_mode_tells_the_model(real_mode, mem_store):
    async def commit(payload, value):
        return {"say": "Order sent to Sakthi vendor."}

    confirm.register_committer("test.real.ws", commit)
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        sid = ws.receive_json()["sessionId"]
        asyncio.run(mem_store.put(DEMO, "cf_1", {"kind": "order", "prompt": "Order?", "payload": {},
                                                 "committer": "test.real.ws", "status": "pending",
                                                 "sessionId": sid}))
        ws.send_json({"t": "confirm", "id": "cf_1", "answer": "yes"})
        assert ws.receive_json()["text"] == "Order sent to Sakthi vendor."
        fb = real_mode[0]
        _wait(lambda: fb.sent_text)
        assert fb.sent_text[0].startswith("[CONFIRMATION]") and "cf_1" in fb.sent_text[0]
        assert "Order sent to Sakthi vendor." in fb.sent_text[0]


# ---------- tokenless mode guards: browser Origin allowlist + per-IP session cap (shared/docs/ops/security.md) ----------
def test_allowed_origins_default_to_the_hosting_site_and_local_vite(monkeypatch):
    monkeypatch.delenv("AIRA_ALLOWED_ORIGINS", raising=False)
    assert main_mod.allowed_origins() == ["https://aira-shop.web.app", "https://aira-shop.firebaseapp.com",
                                          "http://localhost:5173", "http://localhost:4173"]
    monkeypatch.setenv("AIRA_ALLOWED_ORIGINS", "https://demo.example/, http://127.0.0.1:5173 ,,")
    assert main_mod.allowed_origins() == ["https://demo.example", "http://127.0.0.1:5173"]  # the env list replaces
    assert main_mod.origin_allowed("https://demo.example") and main_mod.origin_allowed("https://demo.example/")
    assert not main_mod.origin_allowed("https://aira-shop.web.app")
    assert not main_mod.origin_allowed(None)  # no Origin header: refused (the app always sends one)
    assert not main_mod.origin_allowed("")


def test_ws_refuses_a_browser_origin_that_is_not_allowed(real_mode):
    with pytest.raises(WebSocketDisconnect) as closed:
        with TestClient(main_mod.app).websocket_connect("/ws", headers={"Origin": "https://evil.example"}) as ws:
            ws.send_json(HELLO)
            ws.receive_json()
    assert closed.value.code == 4403
    assert real_mode == []  # refused before hello: Live was never started
    with TestClient(main_mod.app).websocket_connect("/ws", headers={"Origin": "https://aira-shop.web.app"}) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["t"] == "ready"


def test_ws_refuses_an_upgrade_without_an_origin(real_mode):
    with pytest.raises(WebSocketDisconnect) as closed:
        with TestClient(main_mod.app).websocket_connect("/ws") as ws:
            ws.send_json(HELLO)
            ws.receive_json()
    assert closed.value.code == 4403
    assert real_mode == []


def test_ws_allowed_origins_come_from_env(real_mode, monkeypatch):
    monkeypatch.setenv("AIRA_ALLOWED_ORIGINS", "https://judge.example")
    with TestClient(main_mod.app).websocket_connect("/ws", headers={"Origin": "https://judge.example"}) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["t"] == "ready"
    with pytest.raises(WebSocketDisconnect):
        with TestClient(main_mod.app).websocket_connect("/ws", headers={"Origin": "https://aira-shop.web.app"}):
            pass


def test_client_ip_is_the_last_forwarded_hop():
    """Cloud Run appends the real client address last; the first entries are whatever the client sent."""
    ws = SimpleNamespace(headers={"x-forwarded-for": " 1.2.3.4, 203.0.113.9 "}, client=SimpleNamespace(host="10.1.1.1"))
    assert main_mod.client_ip(ws) == "203.0.113.9"
    assert main_mod.client_ip(SimpleNamespace(headers={"x-forwarded-for": "203.0.113.9"}, client=None)) == "203.0.113.9"
    assert main_mod.client_ip(SimpleNamespace(headers={"x-forwarded-for": " , "}, client=SimpleNamespace(host="10.1.1.1"))) == "10.1.1.1"
    assert main_mod.client_ip(SimpleNamespace(headers={}, client=SimpleNamespace(host="10.1.1.1"))) == "10.1.1.1"
    assert main_mod.client_ip(SimpleNamespace(headers={}, client=None)) == "unknown"


def test_sessions_count_per_ip(mk_session):
    a, _ = mk_session(shop="ip_shop")
    b, _ = mk_session(shop="ip_shop")
    c, _ = mk_session(shop="ip_shop")
    sessions.register(a, ip="1.1.1.1")
    sessions.register(b, ip="1.1.1.1")
    sessions.register(c, ip="2.2.2.2")
    try:
        assert sessions.count() == 3 and sessions.count("1.1.1.1") == 2 and sessions.count("3.3.3.3") == 0
    finally:
        for s in (a, b, c):
            sessions.unregister(s)
    assert sessions.count("1.1.1.1") == 0


def test_ws_caps_concurrent_live_sessions(real_mode, monkeypatch):
    """Bounds Gemini spend on the public URL: one phone over the cap gets error/permission and is closed."""
    monkeypatch.setattr(main_mod, "MAX_SESSIONS", 2)
    clients = [TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN).__enter__() for _ in range(2)]
    try:
        for ws in clients:
            ws.send_json(HELLO)
            assert ws.receive_json()["t"] == "ready"
        with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json(HELLO)
            assert ws.receive_json() == {"t": "error", "code": "permission", "say": t("auth.busy", "en")}
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 4403
        assert len(real_mode) == 2
        # a second hello on an existing socket replaces its own session, so it never counts twice
        clients[0].send_json({**HELLO, "lang": "ta"})
        assert clients[0].receive_json()["t"] == "ready"
    finally:
        for ws in clients:
            ws.__exit__(None, None, None)


def test_ws_caps_concurrent_sessions_per_ip(real_mode, monkeypatch):
    """A stranger with the URL gets MAX_SESSIONS_PER_IP sessions, not the whole service."""
    monkeypatch.setattr(main_mod, "MAX_SESSIONS_PER_IP", 2)
    monkeypatch.setattr(main_mod, "MAX_SESSIONS", 10)
    same = {**ORIGIN, "X-Forwarded-For": "203.0.113.9"}
    clients = [TestClient(main_mod.app).websocket_connect("/ws", headers=same).__enter__() for _ in range(2)]
    try:
        for ws in clients:
            ws.send_json(HELLO)
            assert ws.receive_json()["t"] == "ready"
        with TestClient(main_mod.app).websocket_connect("/ws", headers=same) as ws:
            ws.send_json(HELLO)
            assert ws.receive_json() == {"t": "error", "code": "permission", "say": t("auth.busy", "en")}
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        with TestClient(main_mod.app).websocket_connect("/ws", headers={**ORIGIN, "X-Forwarded-For": "198.51.100.2"}) as ws:
            ws.send_json(HELLO)
            assert ws.receive_json()["t"] == "ready"  # another phone is still served
        assert len(real_mode) == 3
    finally:
        for ws in clients:
            ws.__exit__(None, None, None)


# ---------- resume after a WebSocket blip ----------
def test_hello_with_resume_keeps_the_session_id_and_the_live_handle(real_mode):
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        ready = ws.receive_json()
        sid = ready["sessionId"]
        assert ready["resume"] == sid
        real_mode[0].push(LiveEvent("resumption_handle", handle="h-123"))
        _wait(lambda: any(b is not None and b._handle == "h-123" for _, b in sessions.by_shop(DEMO)))
    _wait(lambda: real_mode[0].closed)
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({**HELLO, "resume": sid})
        again = ws.receive_json()
        assert again["sessionId"] == sid and again["resume"] == sid
        assert len(real_mode) == 2 and real_mode[1].started_with == ["h-123"]
        assert sid in _registered(DEMO)


def test_resume_of_an_unknown_or_stale_session_starts_a_new_one(real_mode, monkeypatch):
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({**HELLO, "resume": "never-existed"})
        first = ws.receive_json()["sessionId"]
        assert first != "never-existed"
    _wait(lambda: real_mode[0].closed)
    monkeypatch.setattr(sessions, "RESUME_WINDOW_S", 0.0)
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({**HELLO, "resume": first})
        second = ws.receive_json()["sessionId"]
        assert second != first and real_mode[1].started_with == [None]


def test_second_hello_replaces_the_session_and_closes_its_bridge(real_mode):
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        first = ws.receive_json()["sessionId"]
        ws.send_json({**HELLO, "lang": "ta"})
        second = ws.receive_json()["sessionId"]
        assert first != second and len(real_mode) == 2
        assert real_mode[0].closed and not real_mode[1].closed
        assert first not in _registered(DEMO) and second in _registered(DEMO)
    _wait(lambda: real_mode[1].closed)


# ---------- optional routers (other lanes' APIRouters) ----------
# FastAPI >= 0.140 keeps an included router as one lazy entry in app.routes (no .path), so mounting is checked
# through MOUNTED_ROUTERS, the OpenAPI paths and a real request instead.
def test_jobs_router_is_mounted(monkeypatch):
    monkeypatch.delenv("AIRA_JOB_TOKEN", raising=False)
    assert "aira_live.jobs" in main_mod.MOUNTED_ROUTERS
    assert {"/jobs/expiry", "/jobs/day-summary", "/jobs/forecast-refresh"} <= set(main_mod.app.openapi()["paths"])
    client = TestClient(main_mod.app)
    assert client.post("/jobs/expiry").status_code != 404  # served (and refused: no job token)
    assert client.post("/jobs/not-a-job").status_code == 404


def test_webhooks_router_is_mounted_once_present():
    module = "aira_live.messaging.webhooks"
    try:
        webhooks = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if main_mod._is_missing(exc, module):
            pytest.skip(f"{module} is not in the tree yet (kanish/02)")
        raise
    assert module in main_mod.MOUNTED_ROUTERS
    assert {"/webhooks/whatsapp", "/webhooks/telegram"} <= {r.path for r in webhooks.router.routes}


def test_include_optional_routers_mounts_present_modules_and_skips_missing(monkeypatch):
    router = APIRouter()

    @router.get("/fake-route")
    def fake_route():
        return {"ok": True}

    monkeypatch.setitem(sys.modules, "aira_live.fake_router_mod", SimpleNamespace(router=router))
    app = FastAPI()
    mounted = main_mod.include_optional_routers(app, ("aira_live.fake_router_mod", "aira_live.not_there.webhooks"))
    assert mounted == ["aira_live.fake_router_mod"]
    assert TestClient(app).get("/fake-route").json() == {"ok": True}


def test_webhooks_router_is_in_the_optional_list():
    assert "aira_live.messaging.webhooks" in main_mod.OPTIONAL_ROUTERS
    assert "aira_live.jobs" in main_mod.OPTIONAL_ROUTERS
