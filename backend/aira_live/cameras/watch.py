"""The shop-watch loop: one task per configured camera, four modes, two outputs.

For every frame that passes the motion gate, each of the camera's modes runs:

- ``delivery``  ER 2 counts packs at the delivery spot (``vision.count.count_in_frame``). Packs seen -> "a
                delivery seems to have come, about N packs, shall I count?". The number is a hint, never a
                stock movement: counting a delivery still goes through the normal confirm flow.
- ``shelf``     each catalog product is counted on the shelf; at or below its ``reorderThreshold`` -> "only
                about N left". A count the model could not make (``count=None``) says nothing.
- ``counter``   a person at the counter -> "someone is waiting", once per ``COOLDOWN_S``.
- ``security``  motion inside the after-hours window -> "movement in the shop after hours". No Gemini call.

Outputs: an append-only ``alerts`` event (``analytics.events.publish``) and a short spoken notice to the
owner's live sessions (``sessions.notify``) as English facts (camera name, exact counts, product name); Gemini
Live says them in whatever language the owner is speaking. Each kind repeats at most once per ``COOLDOWN_S``.
Nothing here writes stock, money or state.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from PIL import Image

from aira_live import remote_config, sessions
from aira_live.analytics import events
from aira_live.cameras.config import CameraConfig, parse_cameras
from aira_live.cameras.motion import MotionGate
from aira_live.cameras.sources import CameraSource, PhoneSource, RtspSource
from aira_live.frames import Frame

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
COOLDOWN_S = 120.0           # one notice per kind per camera in this window
SHELF_MAX_PRODUCTS = 4       # catalog products counted per shelf frame (one Gemini call each)
DELIVERY_LABEL = "milk crate, dairy product pack or carton"
COUNTER_LABEL = "person standing at the shop counter"
# English facts per mode; every number is exact and Live keeps it when phrasing in the owner's language.
NOTICE_EN = {
    "delivery": "Camera {camera}: a delivery seems to have come. About {n} packs seen. Shall I count them?",
    "shelf": "Camera {camera}: low stock, only about {left} {product} left on the shelf.",
    "counter": "Camera {camera}: someone is waiting at the counter.",
    "security": "Camera {camera}: movement in the shop after hours.",
}


def now_ist() -> datetime:
    return datetime.now(IST)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def is_after_hours(now: datetime, window: tuple[str, str]) -> bool:
    """True inside ``[start, end)``; a window whose end is before its start wraps midnight."""
    cur = now.hour * 60 + now.minute
    start, end = _minutes(window[0]), _minutes(window[1])
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


async def list_products(shop_id: str) -> list[tuple[str, dict]]:
    from aira_live.business import paths
    from aira_live.business.store import get_store

    return await get_store().list(paths.products(shop_id))


def frame_from(jpeg: bytes, ts: float, camera: str) -> Frame:
    """Wrap raw JPEG bytes as the ``Frame`` the vision tools take. Size is read when decodable, else 0x0."""
    w = h = 0
    try:
        with Image.open(io.BytesIO(jpeg)) as img:
            w, h = img.size
    except Exception:
        pass
    return Frame(id=f"cam-{camera}-{int(ts * 1000)}", jpeg_b64=base64.b64encode(jpeg).decode(), w=w, h=h,
                 ts=ts * 1000.0, purpose="count")


def _count_in_frame():
    from aira_live.vision.count import count_in_frame

    return count_in_frame


class Watcher:
    def __init__(self, cfg: CameraConfig, source: CameraSource | None, gate: MotionGate | None = None, *,
                 counter: Callable | None = None,
                 publish: Callable[[str, dict], Awaitable[str]] = events.publish,
                 notify: Callable[..., Awaitable[int]] = sessions.notify,
                 clock: Callable[[], datetime] = now_ist,
                 products: Callable[[str], Awaitable[list[tuple[str, dict]]]] = list_products):
        self.cfg = cfg
        self.source = source
        self.gate = gate or MotionGate(max_per_minute=cfg.max_per_minute)
        self._counter = counter
        self._publish = publish
        self._notify = notify
        self._clock = clock
        self._products = products
        self.now: Callable[[], float] = time.monotonic
        self._last: dict[str, float] = {}   # kind -> monotonic time of the last notice

    # ---------- the loop ----------

    async def run(self) -> None:
        assert self.source is not None, "run() needs a source"
        log.info("camera %s: watching (%s) via %s", self.cfg.name, ",".join(self.cfg.modes), self.cfg.source)
        try:
            async for jpeg, ts in self.source.frames():
                if not self.gate.allow(jpeg):
                    continue
                try:
                    await self.handle(jpeg, ts)
                except Exception:
                    log.exception("camera %s: handler failed", self.cfg.name)
        finally:
            await self.source.close()

    async def handle(self, jpeg: bytes, ts: float) -> list[str]:
        """Run every mode on one gated frame; returns the modes that produced a notice. Never raises."""
        frame = frame_from(jpeg, ts, self.cfg.name)
        fired: list[str] = []
        for mode in self.cfg.modes:
            try:
                ok = await getattr(self, f"_mode_{mode}")(frame)
            except Exception as exc:
                log.warning("camera %s: %s mode failed: %s", self.cfg.name, mode, type(exc).__name__)
                continue
            if ok:
                fired.append(mode)
        return fired

    # ---------- modes ----------

    async def _count(self, frame: Frame, label: str):
        counter = self._counter or _count_in_frame()
        return await counter(frame, label)

    async def _mode_delivery(self, frame: Frame) -> bool:
        r = await self._count(frame, DELIVERY_LABEL)
        if r.count is None or r.count < 1:
            return False
        return await self._notice("delivery", n=r.count)

    async def _mode_shelf(self, frame: Frame) -> bool:
        fired = False
        for sku, p in (await self._products(self.cfg.shop_id))[:SHELF_MAX_PRODUCTS]:
            name = p.get("name", sku)
            r = await self._count(frame, name)
            if r.count is None:          # the model could not count: say nothing, never invent
                continue
            if r.count <= float(p.get("reorderThreshold") or 0):
                fired = await self._notice("shelf", kind_suffix=sku, product=name, left=r.count) or fired
        return fired

    async def _mode_counter(self, frame: Frame) -> bool:
        r = await self._count(frame, COUNTER_LABEL)
        if r.count is None or r.count < 1:
            return False
        return await self._notice("counter")

    async def _mode_security(self, frame: Frame) -> bool:
        if not is_after_hours(self._clock(), self.cfg.after_hours):
            return False
        return await self._notice("security")

    # ---------- outputs ----------

    async def _notice(self, mode: str, *, kind_suffix: str = "", **kw) -> bool:
        key = f"{mode}:{kind_suffix}"
        now = self.now()
        last = self._last.get(key)
        if last is not None and now - last < COOLDOWN_S:
            return False
        self._last[key] = now
        message = NOTICE_EN[mode].format(camera=self.cfg.name, **kw)
        try:
            await self._publish("alerts", {"shop_id": self.cfg.shop_id, "kind": f"camera.{mode}",
                                           "message": message, "camera": self.cfg.name, **kw})
        except Exception:
            log.exception("camera %s: alert event failed", self.cfg.name)
        try:
            await self._notify(self.cfg.shop_id, message, cue="warn")
        except Exception:
            log.warning("camera %s: notify failed", self.cfg.name, exc_info=True)
        return True


# ---------- startup ----------

def build_source(cfg: CameraConfig) -> CameraSource:
    if cfg.source == "rtsp":
        return RtspSource(cfg.name, cfg.url_env or "", interval_s=cfg.interval_s)
    return PhoneSource(cfg.name, cfg.shop_id)


async def start_if_configured() -> list[asyncio.Task]:
    """Read the ``cameras`` list and start one watcher task per enabled camera. Default: none, nothing runs."""
    await remote_config.refresh()
    cams = parse_cameras(remote_config.cameras())
    if not cams:
        log.info("shop cameras: none configured (off)")
        return []
    tasks = []
    for cfg in cams:
        watcher = Watcher(cfg, build_source(cfg))
        tasks.append(asyncio.create_task(watcher.run(), name=f"camera:{cfg.name}"))
    log.info("shop cameras: watching %s", ", ".join(c.name for c in cams))
    return tasks


async def stop(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
