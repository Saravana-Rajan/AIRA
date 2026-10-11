"""Where camera frames come from. Each source is an async iterator of ``(jpeg_bytes, epoch_seconds)``.

- ``PhoneSource``: a second phone opened at ``/camera`` (shop-camera mode). It sends a normal ``hello`` whose
  ``uid`` is ``camera:<name>`` and then ``frame`` messages (purpose ``ask``) at a low rate. The contracts stay
  frozen: nothing new goes over the wire. ``main.py`` gives such a session no Live bridge (no Gemini Live cost);
  this source reads its ``FrameStore``.
- ``RtspSource``: any RTSP/ONVIF IP camera through an ffmpeg subprocess (``-rtsp_transport tcp``, one frame
  every N seconds, scaled to 768 px wide, MJPEG on stdout). The URL comes from env ``CAMERA_<NAME>_URL``
  (Secret Manager on Cloud Run); it is never written to the config doc and never logged. A dead stream
  reconnects with exponential backoff.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from aira_live import sessions
from aira_live.session import Session

log = logging.getLogger(__name__)

Frame = tuple[bytes, float]
CAMERA_UID_PREFIX = "camera:"
PHONE_PURPOSE = "ask"
DEFAULT_BACKOFF: tuple[float, ...] = (1, 2, 4, 8, 16, 30)
DEFAULT_WIDTH = 768
READ_CHUNK = 65536
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


class CameraSource(Protocol):
    name: str

    def frames(self) -> AsyncIterator[Frame]: ...

    async def close(self) -> None: ...


# ---------- the camera role on /ws ----------

def camera_uid(name: str) -> str:
    return CAMERA_UID_PREFIX + name


def camera_name(uid: str) -> str | None:
    """``"camera:front"`` -> ``"front"``; ``None`` for an ordinary (owner) uid."""
    if isinstance(uid, str) and uid.startswith(CAMERA_UID_PREFIX) and len(uid) > len(CAMERA_UID_PREFIX):
        return uid[len(CAMERA_UID_PREFIX):]
    return None


def is_camera_session(session: Session) -> bool:
    return camera_name(session.uid) is not None


class PhoneSource:
    """Frames from the shop-camera phone session named ``name`` in ``shop_id``; waits while it is not connected."""

    def __init__(self, name: str, shop_id: str, *, purpose: str = PHONE_PURPOSE, poll_s: float = 0.5,
                 find_sessions: Callable[[str], list[tuple[Session, Any]]] = sessions.by_shop):
        self.name = name
        self.shop_id = shop_id
        self.purpose = purpose
        self.poll_s = poll_s
        self._find = find_sessions
        self._closed = False

    def _session(self) -> Session | None:
        uid = camera_uid(self.name)
        for s, _bridge in self._find(self.shop_id):
            if s.uid == uid:
                return s
        return None

    async def frames(self) -> AsyncIterator[Frame]:
        last_ts = 0.0
        while not self._closed:
            s = self._session()
            if s is None:
                await asyncio.sleep(self.poll_s)
                continue
            got = await s.frames.wait_for(count=1, purpose=self.purpose, after_ts=last_ts, timeout_s=self.poll_s * 4)
            if not got:
                continue
            f = got[0]
            last_ts = f.ts
            try:
                data = base64.b64decode(f.jpeg_b64)
            except Exception:
                continue
            yield data, f.ts / 1000.0   # the phone sends epoch milliseconds

    async def close(self) -> None:
        self._closed = True


# ---------- RTSP through ffmpeg ----------

class JpegSplitter:
    """Cuts whole JPEGs (SOI..EOI) out of an MJPEG byte stream fed in arbitrary chunks."""

    def __init__(self, max_buffer: int = 8_000_000):
        self._buf = bytearray()
        self.max_buffer = max_buffer

    def pending(self) -> bytes:
        return bytes(self._buf)

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buf += chunk
        out: list[bytes] = []
        while True:
            start = self._buf.find(SOI)
            if start < 0:
                self._buf.clear()
                break
            if start:
                del self._buf[:start]
            end = self._buf.find(EOI, 2)
            if end < 0:
                break
            out.append(bytes(self._buf[:end + 2]))
            del self._buf[:end + 2]
        if len(self._buf) > self.max_buffer:   # a stream with no EOI: drop it rather than grow forever
            del self._buf[:-self.max_buffer]
        return out


class RtspSource:
    def __init__(self, name: str, url_env: str, *, interval_s: float = 5.0, width: int = DEFAULT_WIDTH,
                 spawn: Callable[[list[str]], Awaitable[Any]] | None = None,
                 backoff: tuple[float, ...] = DEFAULT_BACKOFF,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.time):
        self.name = name
        self.url_env = url_env
        self.interval_s = interval_s
        self.width = width
        self._spawn = spawn or _spawn_ffmpeg
        self._backoff = backoff
        self._sleep = sleep
        self._clock = clock
        self._proc: Any = None
        self._closed = False
        self._warned_env = False

    @staticmethod
    def ffmpeg_args(url: str, interval_s: float = 5.0, width: int = DEFAULT_WIDTH) -> list[str]:
        period = max(1, int(round(interval_s)))
        return [
            "ffmpeg", "-nostdin", "-loglevel", "error", "-hide_banner",
            "-rtsp_transport", "tcp", "-i", url,
            "-vf", f"fps=1/{period},scale={width}:-2",
            "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "-",
        ]

    @staticmethod
    def describe(args: list[str]) -> str:
        """The command for a log line, with the input URL replaced (it usually carries a password)."""
        safe = list(args)
        if "-i" in safe:
            safe[safe.index("-i") + 1] = "<url>"
        return " ".join(safe)

    async def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if getattr(proc, "returncode", None) is None:
                proc.kill()
            await proc.wait()
        except Exception:
            pass

    async def frames(self) -> AsyncIterator[Frame]:
        attempt = 0
        while not self._closed:
            url = os.getenv(self.url_env) or None
            if not url:
                if not self._warned_env:
                    self._warned_env = True
                    log.warning("camera %s: env %s is not set; waiting", self.name, self.url_env)
                await self._sleep(self._backoff[-1])
                continue
            args = self.ffmpeg_args(url, self.interval_s, self.width)
            try:
                self._proc = await self._spawn(args)
            except Exception as exc:
                log.warning("camera %s: cannot start ffmpeg (%s); retrying", self.name, type(exc).__name__)
                await self._sleep(self._backoff[min(attempt, len(self._backoff) - 1)])
                attempt += 1
                continue
            log.info("camera %s: ffmpeg started: %s", self.name, self.describe(args))
            splitter = JpegSplitter()
            got_any = False
            try:
                while not self._closed:
                    chunk = await self._proc.stdout.read(READ_CHUNK)
                    if not chunk:
                        break
                    for jpeg in splitter.feed(chunk):
                        got_any = True
                        attempt = 0
                        yield jpeg, self._clock()
            finally:
                await self._kill()
            if self._closed:
                break
            delay = self._backoff[min(attempt, len(self._backoff) - 1)]
            log.warning("camera %s: stream ended (%s); reconnecting in %ss", self.name,
                        "after frames" if got_any else "no frames", delay)
            await self._sleep(delay)
            attempt += 1

    async def close(self) -> None:
        self._closed = True
        await self._kill()


async def _spawn_ffmpeg(args: list[str]) -> Any:
    return await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
