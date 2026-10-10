"""Camera frames from the phone: a small ring buffer per session that tools can wait on."""
import asyncio
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Frame:
    id: str
    jpeg_b64: str
    w: int
    h: int
    ts: float  # phone clock, epoch ms
    purpose: str


class FrameStore:
    def __init__(self, maxlen: int = 30):
        self._buf: deque[Frame] = deque(maxlen=maxlen)
        self._cond = asyncio.Condition()

    def add(self, frame: Frame) -> None:
        self._buf.append(frame)
        try:
            asyncio.get_running_loop().create_task(self._notify())
        except RuntimeError:  # no running loop (sync callers): waiters re-check on their next wake-up
            pass

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    def latest(self, n: int = 1, purpose: str | None = None) -> list[Frame]:
        items = [f for f in self._buf if purpose is None or f.purpose == purpose]
        return items[-n:] if n > 0 else []

    def last_ts(self, purpose: str | None = None) -> float:
        """The newest timestamp stored (for ``purpose``), or 0 when there is none."""
        return max((f.ts for f in self._buf if purpose is None or f.purpose == purpose), default=0.0)

    async def wait_for(self, count: int, purpose: str, after_ts: float, timeout_s: float) -> list[Frame]:
        """The first ``count`` frames of ``purpose`` newer than ``after_ts``; [] on timeout."""
        def ready() -> list[Frame]:
            return [f for f in self._buf if f.purpose == purpose and f.ts > after_ts]

        try:
            async with asyncio.timeout(timeout_s):
                async with self._cond:
                    await self._cond.wait_for(lambda: len(ready()) >= count)
        except TimeoutError:
            return []
        return ready()[:count]
