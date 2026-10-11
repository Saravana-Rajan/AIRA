"""The shop-watch loop: motion-gated frames are routed by mode to a notice (alerts event + spoken text).

Everything is injected: a scripted counter in place of ``vision.count.count_in_frame``, recorders in place of
``analytics.events.publish`` and ``sessions.notify``, a fixed clock and a fixed owner language.
"""
from __future__ import annotations

import asyncio
import io
from datetime import datetime

import pytest
from PIL import Image

from aira_live.cameras import watch
from aira_live.cameras.config import parse_cameras
from aira_live.cameras.watch import Watcher, is_after_hours
from aira_live.i18n import t
from aira_live.vision.count import CountResult
from aira_live.vision.gemini import VisionError


def jpeg(color=(200, 200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, format="JPEG")
    return buf.getvalue()


def cam(modes, **extra):
    return parse_cameras([{"name": "front", "source": "phone", "modes": modes, **extra}])[0]


class Rec:
    def __init__(self):
        self.events = []
        self.said = []

    async def publish(self, topic, payload):
        self.events.append((topic, payload))
        return "mock"

    async def notify(self, shop_id, text, cue="ack"):
        self.said.append((shop_id, text, cue))
        return 1


def counter_returning(*results):
    script = list(results)
    calls = []

    async def counter(frame, label):
        calls.append(label)
        r = script.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    counter.calls = calls
    return counter


def seen(n, conf=0.9):
    return CountResult(count=n, confidence=conf, needs_retake=False, aim_hint="")


NOON = datetime(2026, 10, 11, 12, 0)
NIGHT = datetime(2026, 10, 11, 23, 30)


def make(cfg, counter=None, rec=None, lang="ta", clock=lambda: NOON, products=None):
    rec = rec or Rec()
    w = Watcher(cfg, source=None, counter=counter or counter_returning(), publish=rec.publish, notify=rec.notify,
                lang_for=lambda shop: lang, clock=clock,
                products=products or (lambda shop: asyncio.sleep(0, result=[])))
    return w, rec


# ---------- delivery ----------

async def test_delivery_mode_counts_and_speaks_when_packs_appear():
    w, rec = make(cam(["delivery"]), counter_returning(seen(12)))
    fired = await w.handle(jpeg(), 1.0)
    assert fired == ["delivery"]
    assert rec.said == [("sunrise_dairy", t("camera.delivery", "ta", camera="front", n=12), "warn")]
    topic, payload = rec.events[0]
    assert topic == "alerts"
    assert payload["shop_id"] == "sunrise_dairy" and payload["kind"] == "camera.delivery"
    assert payload["camera"] == "front" and payload["message"] == rec.said[0][1]


async def test_delivery_mode_stays_quiet_when_nothing_is_seen():
    w, rec = make(cam(["delivery"]), counter_returning(seen(0), CountResult(count=None, confidence=0, needs_retake=True, aim_hint="x")))
    assert await w.handle(jpeg(), 1.0) == []
    assert await w.handle(jpeg(), 2.0) == []
    assert rec.said == [] and rec.events == []


# ---------- shelf ----------

async def products(shop):
    return [("aavin_milk_500ml", {"name": "Aavin Milk 500 ml", "reorderThreshold": 5}),
            ("sakthi_curd_200g", {"name": "Sakthi Curd 200 g", "reorderThreshold": 3})]


async def test_shelf_mode_checks_each_catalog_product_against_its_reorder_level():
    counter = counter_returning(seen(2), seen(10))
    w, rec = make(cam(["shelf"]), counter, products=products, lang="en")
    assert await w.handle(jpeg(), 1.0) == ["shelf"]
    assert counter.calls == ["Aavin Milk 500 ml", "Sakthi Curd 200 g"]
    assert rec.said[0][1] == t("camera.low_stock", "en", camera="front", product="Aavin Milk 500 ml", left=2)
    assert len(rec.said) == 1 and rec.events[0][1]["kind"] == "camera.shelf"


async def test_shelf_mode_never_invents_a_count():
    counter = counter_returning(CountResult(count=None, confidence=0, needs_retake=True, aim_hint="vision.aim.move_back"), seen(10))
    w, rec = make(cam(["shelf"]), counter, products=products)
    assert await w.handle(jpeg(), 1.0) == []
    assert rec.said == []


# ---------- counter ----------

async def test_counter_mode_tells_the_owner_someone_is_waiting_once_per_cooldown():
    clock = {"t": 100.0}
    counter = counter_returning(seen(1), seen(1), seen(1))
    w, rec = make(cam(["counter"]), counter, lang="hi")
    w.now = lambda: clock["t"]
    assert await w.handle(jpeg(), 1.0) == ["counter"]
    assert rec.said[0][1] == t("camera.customer", "hi", camera="front")
    assert await w.handle(jpeg(), 2.0) == []          # within the cooldown: no nagging
    clock["t"] += watch.COOLDOWN_S + 1
    assert await w.handle(jpeg(), 3.0) == ["counter"]


# ---------- security ----------

def test_after_hours_window_wraps_midnight():
    assert is_after_hours(NIGHT, ("22:00", "06:00")) is True
    assert is_after_hours(datetime(2026, 10, 11, 3, 0), ("22:00", "06:00")) is True
    assert is_after_hours(NOON, ("22:00", "06:00")) is False
    assert is_after_hours(datetime(2026, 10, 11, 22, 0), ("22:00", "06:00")) is True
    assert is_after_hours(datetime(2026, 10, 11, 6, 0), ("22:00", "06:00")) is False
    assert is_after_hours(datetime(2026, 10, 11, 14, 0), ("13:00", "15:00")) is True   # a same-day window


async def test_security_mode_alerts_on_motion_after_hours_without_calling_gemini():
    counter = counter_returning()
    w, rec = make(cam(["security"]), counter, clock=lambda: NIGHT)
    assert await w.handle(jpeg(), 1.0) == ["security"]
    assert counter.calls == []
    assert rec.said[0] == ("sunrise_dairy", t("camera.motion", "ta", camera="front"), "warn")
    assert rec.events[0][1]["kind"] == "camera.security"


async def test_security_mode_is_silent_during_the_day():
    w, rec = make(cam(["security"]), clock=lambda: NOON)
    assert await w.handle(jpeg(), 1.0) == []
    assert rec.said == []


# ---------- robustness ----------

async def test_a_vision_failure_skips_that_mode_and_keeps_the_others():
    counter = counter_returning(VisionError("model down"), seen(1))
    w, rec = make(cam(["delivery", "counter"]), counter)
    assert await w.handle(jpeg(), 1.0) == ["counter"]


async def test_a_notify_failure_never_stops_the_watcher():
    async def bad_notify(shop, text, cue="ack"):
        raise RuntimeError("socket gone")

    rec = Rec()
    w = Watcher(cam(["security"]), source=None, counter=counter_returning(), publish=rec.publish, notify=bad_notify,
                lang_for=lambda s: "en", clock=lambda: NIGHT, products=products)
    assert await w.handle(jpeg(), 1.0) == ["security"]
    assert rec.events  # the event was still written


async def test_run_pulls_frames_through_the_gate_and_stops_with_the_source():
    class Src:
        name = "front"
        closed = False

        async def frames(self):
            for i, f in enumerate([jpeg(), jpeg(), jpeg((0, 0, 0))]):   # second frame is identical: gated out
                yield f, float(i)

        async def close(self):
            self.closed = True

    src = Src()
    rec = Rec()
    w = Watcher(cam(["security"]), source=src, counter=counter_returning(), publish=rec.publish, notify=rec.notify,
                lang_for=lambda s: "en", clock=lambda: NIGHT, products=products)
    handled = []
    original = w.handle

    async def spy(jpeg, ts):
        handled.append(ts)
        return await original(jpeg, ts)

    w.handle = spy
    await w.run()
    assert handled == [0.0, 2.0]          # frame 1 was identical to frame 0: gated out
    assert len(rec.said) == 1             # the second alert is inside the cooldown
    assert src.closed is True


async def test_handle_never_raises_on_undecodable_frame_bytes():
    w, rec = make(cam(["delivery"]), counter_returning(seen(3)))
    assert await w.handle(b"not a jpeg", 1.0) == ["delivery"]   # width/height unknown, the count still ran


# ---------- owner language ----------

def test_owner_lang_follows_the_live_session_and_ignores_camera_sessions(mk_session, monkeypatch):
    from aira_live import sessions
    from aira_live.cameras.sources import camera_uid
    from aira_live.session import Session

    owner, rec = mk_session(lang="ta")
    cam_s = Session(camera_uid("front"), "sunrise_dairy", "en", "short", rec)
    monkeypatch.setattr(sessions, "by_shop", lambda shop: [(cam_s, None), (owner, None)])
    assert watch.owner_lang("sunrise_dairy") == "ta"
    monkeypatch.setattr(sessions, "by_shop", lambda shop: [(cam_s, None)])
    assert watch.owner_lang("sunrise_dairy") == "en"


def test_i18n_keys_exist_in_all_three_languages():
    for key in ("camera.delivery", "camera.low_stock", "camera.customer", "camera.motion"):
        for lang in ("ta", "hi", "en"):
            text = t(key, lang, camera="front", n=1, product="x", left=1)
            assert text != key and "{" not in text, (key, lang, text)
