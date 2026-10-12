"""Hand guidance loop (spec §2 step 6). OWNER: Sarmitha (sarmitha/03).

1. ``product_find`` starts a loop: the app streams continuous ``guide`` frames (about 1 fps).
2. Each tick points ER 2 at the target on the newest frame and sends a ``target`` message; the app plays tones.
3. The app reports ``fingertip_in_target``; AIRA identifies the touched pack and speaks the verdict.

The loop only guides and verifies a pick; it never writes stock or money. Below the confidence thresholds AIRA
says "Not sure" and never counts a guess. ``main.py`` calls ``handle_client_event`` for every client ``event``
and ``stop_guidance`` on a ``stop`` message and on disconnect.
"""
import asyncio
import logging
import re
import time

from aira_live import monitor
from aira_live.contracts import RequestFrameMsg, TargetMsg
from aira_live.i18n import t
from aira_live.memory import episodic
from aira_live.sessions import notify
from aira_live.vision.gemini import VisionError
from aira_live.vision.identify import ProductRef, identify_free_text, identify_product, next_hint
from aira_live.vision.point import point_to

log = logging.getLogger(__name__)

MIN_POINT_CONF = 0.3   # send a `target` only at or above this
MIN_ID_CONF = 0.6      # count a pick only at or above this
LOST_AFTER = 3         # ticks without a target before "I can't see …" (spoken once per loss)
MAX_GUIDE_S = 120.0    # an unattended loop stops itself: no endless ER 2 calls
FREE_PREFIX = "free:"  # sku of a free-text item ("water bottle"): not a catalog product, verified by its words

_ACTIVE: dict[str, "GuidanceLoop"] = {}
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ml|l|kg|g)\b", re.IGNORECASE)


def is_free_text(ref: ProductRef | None) -> bool:
    """True for a ref made by ``products.free_ref``: an item named by the owner, not a catalog SKU."""
    return ref is not None and ref.sku.startswith(FREE_PREFIX)


def _item(ref: ProductRef | None) -> str:
    """Spoken label: name plus pack, without saying the size twice ("Sakthi Curd 1 L", not "… 1 L 1 L pack")."""
    if ref is None:
        return ""
    name, pack = ref.name.strip(), ref.pack.strip()
    if not pack or pack.casefold() in name.casefold():
        return name
    size = _SIZE.match(pack)
    if size and re.search(rf"(?<![\d.]){re.escape(size.group(1))}\s*{size.group(2)}\b", name, re.IGNORECASE):
        return name
    return f"{name} {pack}"


class GuidanceLoop:
    def __init__(self, session, ref: ProductRef, needed: int, candidates: list[ProductRef],
                 interval_s: float = 1.0, frame_timeout_s: float = 3.0, max_s: float = MAX_GUIDE_S):
        self.session, self.ref, self.candidates = session, ref, candidates
        self.needed, self.picked = max(1, needed), 0
        self.interval_s, self.frame_timeout_s, self.max_s = interval_s, frame_timeout_s, max_s
        self.last_ts, self.misses, self.active = 0.0, 0, True
        self.last_point: tuple[int, int] | None = None  # where the target was last seen (the monitor's "found")
        self.last_box: tuple[int, int, int, int] | None = None
        self.free_text = is_free_text(ref)
        self.task: asyncio.Task | None = None
        self._touch_busy = False

    async def step(self) -> bool:
        """One tick: newest unseen guide frame -> point -> `target`. Returns False once guidance has stopped."""
        if not self.active:
            return False
        s = self.session
        fresh = await s.frames.wait_for(count=1, purpose="guide", after_ts=self.last_ts,
                                        timeout_s=self.frame_timeout_s)
        hit = False
        if fresh and self.active:
            newest = s.frames.latest(1, purpose="guide")
            frame = newest[-1] if newest and newest[-1].ts > self.last_ts else fresh[-1]
            self.last_ts = frame.ts
            try:
                res = await point_to(frame, _item(self.ref))
            except VisionError as exc:
                log.warning("guidance: point failed for %s: %s", self.ref.sku, exc)
                res = None
            hit = self.active and res is not None and res.confidence >= MIN_POINT_CONF
            if self.active:  # the monitor and the phone overlay first: the target box (green once in hand)
                from aira_live.vision.listing import publish_boxes

                seen = [{"label": self.ref.name, "point": list(res.point), "box": list(res.box) if res.box else None,
                         "confidence": res.confidence}] if hit else []
                await publish_boxes(s, seen, "guidance", target=self.ref.name, found=hit, frame_id=frame.id)
            if hit and self.active:  # then the `target` the phone's hand guidance acts on
                self.last_point, self.last_box = res.point, res.box
                await s.send(TargetMsg(frame_id=frame.id, label=self.ref.name, point=res.point,
                                       box=res.box, confidence=res.confidence))
        if not self.active:
            return False
        if hit:
            self.misses = 0
        else:
            self.misses += 1
            if self.misses == LOST_AFTER:
                monitor.guidance_lost(s, self.ref.name)
                await notify(s.shop_id, t("guide.lost", s.lang, name=self.ref.name), cue="unsure")
        return self.active

    def _found(self) -> None:
        """Tell the monitor the owner's hand is on the target (a verified pick)."""
        monitor.guidance_found(self.session, self.ref.name, self.last_point, self.last_box)

    async def run(self) -> None:
        """Tick until stopped; a broken tick is logged and the loop goes on. Stops itself after ``max_s``."""
        deadline = time.monotonic() + self.max_s
        while self.active:
            if time.monotonic() >= deadline:
                if await self._finish():
                    s = self.session
                    await notify(s.shop_id, t("guide.timeout", s.lang), cue="warn")
                return
            try:
                if not await self.step():
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("guidance step failed for %s", self.ref.sku)
            await asyncio.sleep(self.interval_s)

    async def on_touch(self) -> dict | None:
        """Fingertip is on the target: identify the touched pack and speak the verdict.

        Ignored (returns None) while another touch is being checked, and after guidance has stopped.
        """
        if not self.active or self._touch_busy:
            return None
        self._touch_busy = True
        try:
            return await self._verify_touch()
        finally:
            self._touch_busy = False

    async def _verify_touch(self) -> dict | None:
        s = self.session
        frames = s.frames.latest(1, purpose="guide")
        if not frames:
            return await self._say(t("camera.none", s.lang), "warn")
        if self.free_text:
            return await self._verify_free_text(frames[-1])
        try:
            res = await identify_product(frames[-1], self.candidates)
        except VisionError as exc:
            log.warning("guidance: identify failed for %s: %s", self.ref.sku, exc)
            return await self._say(t("live.unavailable", s.lang), "warn") if self.active else None
        if not self.active:
            return None
        got = next((c for c in self.candidates if c.sku == res.sku), None)
        if got is None or res.confidence < MIN_ID_CONF:
            key = "pick.unsure_label" if next_hint(res, self.candidates) == "vision.aim.show_label" else "pick.unsure"
            return await self._say(t(key, s.lang), "unsure")
        if got.sku != self.ref.sku:
            return await self._say(t("pick.wrong", s.lang, got=_item(got), want=_item(self.ref)), "mismatch")
        self.picked += 1
        self._found()
        remaining = self.needed - self.picked
        if remaining > 0:
            return await self._say(t("pick.ok_more", s.lang, item=_item(self.ref), n=remaining), "match")
        await self._finish()
        await episodic.log(s.shop_id, "pick", f"Picked {self.needed} x {_item(self.ref)}", "product", self.ref.sku)
        return await self._say(t("pick.ok_done", s.lang, item=_item(self.ref), n=self.needed), "match")

    async def _verify_free_text(self, frame) -> dict | None:
        """Free-text item (spec §4): ask ER 2 whether the touched object is what the owner named.

        "That's the water bottle" only on a confident match; otherwise what it looks like, or "not sure".
        """
        s = self.session
        item = _item(self.ref)
        try:
            res = await identify_free_text(frame, item)
        except VisionError as exc:
            log.warning("guidance: free-text identify failed for %r: %s", item, exc)
            return await self._say(t("live.unavailable", s.lang), "warn") if self.active else None
        if not self.active:
            return None
        if not res.match:
            if res.seen and res.confidence >= MIN_ID_CONF:
                return await self._say(t("pick.free_wrong", s.lang, seen=res.seen, item=item), "mismatch")
            return await self._say(t("pick.unsure", s.lang), "unsure")
        self.picked += 1
        self._found()
        remaining = self.needed - self.picked
        if remaining > 0:
            return await self._say(t("pick.ok_more", s.lang, item=item, n=remaining), "match")
        await self._finish()
        await episodic.log(s.shop_id, "pick", f"Picked {self.needed} x {item}", "free_text", item)
        return await self._say(t("pick.free_ok", s.lang, item=item), "match")

    async def _say(self, text: str, cue: str) -> dict:
        await notify(self.session.shop_id, text, cue=cue)
        return {"say": text, "picked": self.picked, "needed": self.needed}

    async def _finish(self) -> bool:
        """Stop this loop and unregister it, unless a newer loop already replaced it. True if it was running."""
        sid = self.session.session_id
        if _ACTIVE.get(sid) is self:
            del _ACTIVE[sid]
        was_active = self.active
        await self.stop()
        return was_active

    async def stop(self) -> None:
        """Stop ticking and ask the app to stop the guide frames. Safe to call twice and on a closed socket."""
        if not self.active:
            return
        self.active = False
        if self.task is not None and self.task is not asyncio.current_task() and not self.task.done():
            self.task.cancel()
        try:
            await self.session.send(RequestFrameMsg(purpose="guide", stop=True))
        except Exception:
            log.warning("guidance: could not send stop to session %s", self.session.session_id, exc_info=True)


async def start(session, ref: ProductRef, needed: int, candidates: list[ProductRef], interval_s: float = 1.0,
                frame_timeout_s: float = 3.0, run: bool = True, max_s: float = MAX_GUIDE_S) -> GuidanceLoop:
    """Replace any guidance of this session: ask the app for continuous guide frames and start the loop."""
    await stop_guidance(session)
    loop = GuidanceLoop(session, ref, needed, candidates, interval_s, frame_timeout_s, max_s)
    await session.send(RequestFrameMsg(purpose="guide", continuous=True))
    _ACTIVE[session.session_id] = loop
    if run:
        loop.task = asyncio.create_task(loop.run(), name=f"guidance-{session.session_id}")
    return loop


def active(session) -> GuidanceLoop | None:
    """The running guidance loop of ``session``, or None."""
    return _ACTIVE.get(session.session_id)


async def handle_client_event(session, name: str, data: dict | None) -> None:
    """Cross-team hook: main.py calls this for every client `event` message. Only `fingertip_in_target` matters."""
    if name != "fingertip_in_target":
        return
    loop = _ACTIVE.get(session.session_id)
    if loop is None or not loop.active:
        return
    await loop.on_touch()


async def stop_guidance(session) -> None:
    """Cross-team hook: main.py calls this on a `stop` message and on disconnect. Safe to call twice."""
    loop = _ACTIVE.pop(session.session_id, None)
    if loop is not None:
        await loop.stop()
