"""Ambient mode: AIRA speaks by itself about what the owner shows the camera and what comes into view.

- hold-up frames (purpose ``identify_held``): catalog first (name, pack, price), then a free-text label, else an
  aim hint at most once per 8 s
- scene announcements reuse ``tools.describe.DescribeLoop``, started with ambient mode
- on by default after the session starts (persisted); "quiet mode" / "stop describing" turn it off, "describe
  again" turns it on; never speaks while the owner talks or AIRA answers; hold-up lines beat scene lines
"""
import asyncio
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from aira_live import prompt, sessions
from aira_live.contracts import RequestFrameMsg, TranscriptMsg
from aira_live.frames import Frame
from aira_live.i18n import t
from aira_live.live_bridge import _listeners
from aira_live.session import current_session
from aira_live.tools import ambient, describe
from aira_live.vision.gemini import VisionError
from aira_live.vision.identify import IdentifyResult, ProductRef

TINY = "/9j/4AAQSkZJRg=="
MILK = ProductRef(sku="aavin_milk_500ml", name="Aavin Milk", brand="Aavin", pack="500 ml")
CURD = ProductRef(sku="sakthi_curd_1l", name="Sakthi Curd", brand="Sakthi", pack="1 L")


def _frame(ts: float, purpose: str = ambient.PURPOSE) -> Frame:
    return Frame(id=f"h{ts}", jpeg_b64=TINY, w=640, h=480, ts=ts, purpose=purpose)


class Clock:
    def __init__(self, t0: float = 1000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def session(mk_session, fake_db):
    s, rec = mk_session(lang="en")
    sessions.register(s)
    token = current_session.set(s)
    yield s, rec
    current_session.reset(token)
    sessions.unregister(s)
    ambient._ACTIVE.clear()
    ambient._SWITCH.clear()
    describe._ACTIVE.clear()
    _listeners.pop(s.session_id, None)


@pytest.fixture
def vision(monkeypatch):
    """Fake the two identifications: ``catalog`` replies (IdentifyResult or exception) and ``free`` replies
    (dict or exception), each consumed in order. Records the calls."""
    calls = {"catalog": 0, "free": 0}
    catalog: list = []
    free: list = []

    async def fake_products(shop_id):
        return [MILK, CURD]

    async def fake_identify(frame, candidates):
        calls["catalog"] += 1
        item = catalog.pop(0) if catalog else IdentifyResult(sku=None, confidence=0.0, alternatives=[])
        if isinstance(item, Exception):
            raise item
        return item

    async def fake_free(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        calls["free"] += 1
        item = free.pop(0) if free else {"label": "", "confidence": 0.0}
        if isinstance(item, Exception):
            raise item
        return dict(item)

    monkeypatch.setattr(ambient, "shop_products", fake_products)
    monkeypatch.setattr(ambient, "identify_product", fake_identify)
    monkeypatch.setattr(ambient, "generate_json", fake_free)
    return SimpleNamespace(calls=calls, catalog=catalog, free=free)


def _loop(s, clock, **kw):
    kw.setdefault("busy", lambda: False)
    return ambient.HoldUpLoop(s, clock=clock, frame_timeout_s=0.05, **kw)


def _spoken(rec) -> list[str]:
    return [m.text for m in rec.msgs if isinstance(m, TranscriptMsg)]


def _frame_requests(rec):
    return [(m.purpose, m.continuous, m.stop) for m in rec.msgs if isinstance(m, RequestFrameMsg)]


def _price(fake_db, sku: str, price):
    fake_db.store[f"shops/sunrise_dairy/products/{sku}"] = {"name": "x", "price": price, "active": True}


# --- voice trigger, i18n, prompt ---------------------------------------------------------------------------------


@pytest.mark.parametrize("text, want", [
    ("stop describing", False), ("quiet mode", False), ("be quiet", False), ("please stop telling me things", False),
    ("describe again", True), ("start describing", True), ("keep describing", True), ("describe mode on", True),
    ("அமைதி முறை", False), ("மீண்டும் விவரி", True), ("शांत मोड", False), ("फिर से बताओ", True),
    ("how much is curd", None), ("", None), ("describe what is in front of me", None),
])
def test_voice_trigger_parsing(text, want):
    assert ambient.parse_ambient_command(text) is want


def test_i18n_keys_exist_in_all_three_languages():
    for lang in ("en", "ta", "hi"):
        for key in ("ambient.on", "ambient.off", "ambient.held", "ambient.held_price", "ambient.held_free",
                    "ambient.closer"):
            assert t(key, lang) != key, (key, lang)


def test_prompt_tells_the_model_ambient_lines_are_notices_it_must_not_repeat():
    text = prompt.render_prompt({"name": "Sunrise Dairy"}, [], [], "en", "short")
    assert "quiet mode" in text.lower() and "ambient" in text.lower()
    assert "do not repeat" in text.lower()


# --- the identify_held path --------------------------------------------------------------------------------------


async def test_catalog_product_is_named_with_pack_and_price(session, vision, fake_db):
    s, rec = session
    _price(fake_db, MILK.sku, 30)
    loop = _loop(s, Clock())
    vision.catalog.append(IdentifyResult(sku=MILK.sku, confidence=0.9, alternatives=[]))
    s.frames.add(_frame(1))
    assert await loop.step() is True
    assert _spoken(rec) == ["That's Aavin Milk, 500 ml. 30 rupees."]
    assert vision.calls == {"catalog": 1, "free": 0}
    assert rec.msgs[0].lang == "en"


async def test_catalog_product_without_a_price_is_named_without_one(session, vision):
    s, rec = session
    loop = _loop(s, Clock())
    vision.catalog.append(IdentifyResult(sku=CURD.sku, confidence=0.8, alternatives=[]))
    s.frames.add(_frame(1))
    await loop.step()
    assert _spoken(rec) == ["That's Sakthi Curd, 1 L."]


async def test_free_text_label_when_it_is_not_a_catalog_product(session, vision):
    s, rec = session
    loop = _loop(s, Clock())
    vision.catalog.append(IdentifyResult(sku=None, confidence=0.2, alternatives=[]))
    vision.free.append({"label": "a water bottle", "confidence": 0.8})
    s.frames.add(_frame(1))
    await loop.step()
    assert _spoken(rec) == ["That's a water bottle."]
    assert vision.calls == {"catalog": 1, "free": 1}


async def test_low_confidence_gives_the_aim_hint_at_most_once_per_eight_seconds(session, vision):
    s, rec = session
    clock = Clock()
    loop = _loop(s, clock, min_gap_s=0.0)
    vision.free.append({"label": "something", "confidence": 0.3})
    s.frames.add(_frame(1))
    await loop.step()
    assert _spoken(rec) == [t("ambient.closer", "en")]
    clock.t += 4
    vision.free.append({"label": "", "confidence": 0.0})
    s.frames.add(_frame(2))
    await loop.step()
    assert len(_spoken(rec)) == 1                                   # within 8 s: quiet
    clock.t += 4.5
    vision.free.append({"label": "", "confidence": 0.0})
    s.frames.add(_frame(3))
    await loop.step()
    assert _spoken(rec) == [t("ambient.closer", "en")] * 2


async def test_people_are_never_described_and_get_no_hint(session, vision):
    s, rec = session
    loop = _loop(s, Clock())
    vision.free.append({"label": "a man", "confidence": 0.9})
    s.frames.add(_frame(1))
    await loop.step()
    assert _spoken(rec) == []


async def test_at_most_one_identification_every_three_seconds(session, vision):
    s, rec = session
    clock = Clock()
    loop = _loop(s, clock, min_gap_s=3.0)
    vision.catalog.extend([IdentifyResult(sku=MILK.sku, confidence=0.9, alternatives=[])] * 3)
    s.frames.add(_frame(1))
    await loop.step()
    clock.t += 1
    s.frames.add(_frame(2))
    await loop.step()
    assert vision.calls["catalog"] == 1 and loop.calls == 1          # the second frame is dropped
    clock.t += 2.5
    s.frames.add(_frame(3))
    await loop.step()
    assert vision.calls["catalog"] == 2


async def test_no_speech_and_no_model_call_while_the_owner_talks_or_aira_answers(session, vision):
    s, rec = session
    busy = {"v": True}
    loop = _loop(s, Clock(), busy=lambda: busy["v"])
    vision.catalog.append(IdentifyResult(sku=MILK.sku, confidence=0.9, alternatives=[]))
    s.frames.add(_frame(1))
    await loop.step()
    assert vision.calls["catalog"] == 0 and _spoken(rec) == []
    busy["v"] = False
    s.frames.add(_frame(2))
    await loop.step()
    assert _spoken(rec) == ["That's Aavin Milk, 500 ml."]


async def test_a_line_is_dropped_when_someone_started_talking_during_the_model_call(session, vision, monkeypatch):
    s, rec = session
    busy = {"v": False}
    loop = _loop(s, Clock(), busy=lambda: busy["v"])

    async def talk_during(frame, candidates):
        busy["v"] = True
        return IdentifyResult(sku=MILK.sku, confidence=0.9, alternatives=[])

    monkeypatch.setattr(ambient, "identify_product", talk_during)
    s.frames.add(_frame(1))
    await loop.step()
    assert _spoken(rec) == []


async def test_vision_failure_is_logged_and_the_loop_goes_on(session, vision):
    s, rec = session
    loop = _loop(s, Clock())
    vision.catalog.append(VisionError("down"))
    s.frames.add(_frame(1))
    assert await loop.step() is True
    assert _spoken(rec) == [] and loop.active


async def test_a_hold_up_line_makes_the_scene_loop_wait(session, vision):
    s, rec = session
    clock = Clock()
    loop = _loop(s, clock)
    assert loop.holding() is False
    vision.catalog.append(IdentifyResult(sku=MILK.sku, confidence=0.9, alternatives=[]))
    s.frames.add(_frame(1))
    await loop.step()
    assert loop.holding() is True
    clock.t += ambient.HOLD_AFTER_SPEECH_S + 0.1
    assert loop.holding() is False


async def test_only_identify_held_frames_are_looked_at(session, vision):
    s, rec = session
    loop = _loop(s, Clock())
    vision.catalog.append(IdentifyResult(sku=MILK.sku, confidence=0.9, alternatives=[]))
    s.frames.add(_frame(1, purpose="describe"))
    await loop.step()
    assert vision.calls["catalog"] == 0 and _spoken(rec) == []


# --- start, stop, voice, persistence ------------------------------------------------------------------------------


async def test_start_runs_both_loops_and_stop_ends_them(session):
    s, rec = session
    amb = await ambient.start(s, run=False)
    assert ambient.active(s) is amb and ambient.is_on(s)
    assert describe.active(s) is not None and amb.holdup.active
    assert _frame_requests(rec) == [("describe", True, None), ("identify_held", True, None)]
    assert await ambient.stop(s) is True
    assert ambient.active(s) is None and not ambient.is_on(s)
    assert describe.active(s) is None and not amb.holdup.active
    assert set(_frame_requests(rec)[2:]) == {("describe", None, True), ("identify_held", None, True)}
    assert await ambient.stop(s) is False                            # idempotent


async def test_scene_loop_waits_while_a_hold_up_line_is_fresh(session):
    s, rec = session
    clock = Clock()
    amb = await ambient.start(s, run=False, clock=clock, busy=lambda: False)
    scene = describe.active(s)
    assert scene.busy() is False
    amb.holdup.hold_until = clock() + 2
    assert scene.busy() is True
    await ambient.stop(s)


async def test_quiet_mode_by_voice_turns_everything_off_once_and_persists(session, fake_db):
    s, rec = session
    amb = await ambient.start(s, run=False, busy=lambda: False)
    for cb in list(_listeners[s.session_id]):                        # what the bridge does on a final transcript
        await cb("stop describing, quiet mode")
    assert ambient.active(s) is None and describe.active(s) is None and not amb.holdup.active
    assert _spoken(rec) == [t("ambient.off", "en")]                 # one line, not describe.off as well
    assert await ambient.load_setting(s.shop_id) is False
    for cb in list(_listeners.get(s.session_id, [])):
        await cb("describe again")
    assert ambient.active(s) is not None and describe.active(s) is not None
    assert _spoken(rec)[-1] == t("ambient.on", "en")
    assert await ambient.load_setting(s.shop_id) is True
    await ambient.stop(s)


async def test_session_start_turns_ambient_on_by_default_but_not_when_switched_off(session, fake_db):
    s, rec = session
    await ambient.on_session_start(s, delay_s=0.0)
    await asyncio.sleep(0.05)
    assert ambient.active(s) is not None and describe.active(s) is not None
    await ambient.stop(s)
    await ambient.save_setting(s.shop_id, False)
    await ambient.on_session_start(s, delay_s=0.0)
    await asyncio.sleep(0.05)
    assert ambient.active(s) is None and describe.active(s) is None


async def test_session_start_does_nothing_when_the_phone_already_left(session, fake_db):
    s, rec = session
    sessions.unregister(s)
    await ambient.on_session_start(s, delay_s=0.0)
    await asyncio.sleep(0.05)
    assert ambient.active(s) is None
    sessions.register(s)


async def test_stop_session_ends_the_loops_without_touching_the_setting(session, fake_db):
    s, rec = session
    await ambient.start(s, run=False)
    await ambient.stop_session(s)
    assert ambient.active(s) is None and describe.active(s) is None
    assert await ambient.load_setting(s.shop_id) is True


async def test_describe_mode_tool_switches_ambient_too(session):
    s, rec = session
    assert await describe.describe_mode(True) == {"say": t("describe.on", "en"), "active": True}
    assert ambient.active(s) is not None and ambient.active(s).holdup.active
    assert await describe.describe_mode(False) == {"say": t("describe.off", "en"), "active": False}
    assert ambient.active(s) is None and describe.active(s) is None


async def test_a_broken_setting_store_still_starts_ambient(session, monkeypatch):
    s, rec = session

    async def boom(shop_id):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(ambient, "load_setting", boom)
    await ambient.on_session_start(s, delay_s=0.0)
    await asyncio.sleep(0.05)
    assert ambient.active(s) is not None
    await ambient.stop(s)


# --- main.py hooks ------------------------------------------------------------------------------------------------


ORIGIN = {"Origin": "https://aira-shop.web.app"}


def _wait(pred, timeout: float = 2.0) -> None:
    """Poll from the test thread (the server runs in TestClient's portal thread)."""
    deadline = time.time() + timeout
    while not pred():
        if time.time() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


HELLO = {"t": "hello", "uid": "u1", "shopId": "sunrise_dairy", "lang": "en", "verbosity": "short", "appVersion": "0.1.0"}


def test_hello_in_real_mode_starts_ambient_and_disconnect_stops_it(monkeypatch, fake_db):
    from live_fakes import FakeBackend
    from aira_live import main as main_mod

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")

    async def fake_prompt(shop_id, lang, verbosity):
        return "prompt"

    monkeypatch.setattr(main_mod, "build_system_prompt", fake_prompt)
    monkeypatch.setattr(main_mod.app.state, "backend_factory", lambda session: FakeBackend())
    calls = {"start": [], "stop": []}

    async def on_session_start(session):
        calls["start"].append(session.session_id)

    async def stop_session(session):
        calls["stop"].append(session.session_id)

    monkeypatch.setitem(sys.modules, "aira_live.tools.ambient",
                        SimpleNamespace(on_session_start=on_session_start, stop_session=stop_session))
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        sid = ws.receive_json()["sessionId"]
    _wait(lambda: calls["stop"])
    assert calls["start"] == [sid] and calls["stop"] == [sid]


def test_hello_in_mock_mode_never_starts_ambient(monkeypatch, fake_db):
    from aira_live import main as main_mod

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    calls = {"start": [], "stop": []}

    async def on_session_start(session):
        calls["start"].append(session.session_id)

    async def stop_session(session):
        calls["stop"].append(session.session_id)

    monkeypatch.setitem(sys.modules, "aira_live.tools.ambient",
                        SimpleNamespace(on_session_start=on_session_start, stop_session=stop_session))
    with TestClient(main_mod.app).websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json(HELLO)
        assert ws.receive_json()["mock"] is True
    _wait(lambda: calls["stop"])
    assert calls["start"] == []
