"""A cheap motion gate: only frames that changed, and at most N per minute, go on to Gemini.

Each JPEG is downscaled to a tiny grayscale image (32x24 by default) and compared with the previous one by
mean absolute pixel difference. Below ``threshold`` the frame is "the same" and dropped. The reference frame
is updated on every decodable frame, so a slow change (daylight fading) never accumulates into a false alarm,
and a frame refused by the per-minute budget still becomes the reference.

Pillow only (the service has no numpy): ``ImageChops.difference`` + ``ImageStat`` is plenty at 32x24.
"""
from __future__ import annotations

import io
import logging
import time
from collections import deque
from collections.abc import Callable

from PIL import Image, ImageChops, ImageStat

log = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 12.0        # mean absolute gray difference (0..255) that counts as motion
DEFAULT_SIZE = (32, 24)
WINDOW_S = 60.0


def gray_small(jpeg: bytes, size: tuple[int, int] = DEFAULT_SIZE) -> Image.Image:
    """Decode a JPEG to a tiny grayscale image. Raises on undecodable bytes."""
    with Image.open(io.BytesIO(jpeg)) as img:
        return img.convert("L").resize(size, Image.Resampling.BILINEAR)


def mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    return float(ImageStat.Stat(ImageChops.difference(a, b)).mean[0])


class MotionGate:
    def __init__(self, threshold: float = DEFAULT_THRESHOLD, max_per_minute: int = 6,
                 size: tuple[int, int] = DEFAULT_SIZE, clock: Callable[[], float] = time.monotonic):
        self.threshold = threshold
        self.max_per_minute = max(1, int(max_per_minute))
        self.size = size
        self._clock = clock
        self._prev: Image.Image | None = None
        self._passed: deque[float] = deque()
        self._warned = False

    def reset(self) -> None:
        self._prev = None
        self._passed.clear()

    def _budget_left(self, now: float) -> bool:
        while self._passed and now - self._passed[0] >= WINDOW_S:
            self._passed.popleft()
        return len(self._passed) < self.max_per_minute

    def score(self, jpeg: bytes) -> float | None:
        """Mean absolute difference against the reference frame (``None`` for the first frame); updates the
        reference. Returns ``-1`` for undecodable bytes."""
        try:
            cur = gray_small(jpeg, self.size)
        except Exception:
            if not self._warned:
                self._warned = True
                log.warning("motion gate: undecodable frame bytes (logged once)")
            return -1.0
        prev, self._prev = self._prev, cur
        return None if prev is None else mean_abs_diff(prev, cur)

    def allow(self, jpeg: bytes, now: float | None = None) -> bool:
        """True when the frame changed enough and the per-minute budget allows it."""
        score = self.score(jpeg)
        if score is not None and score < 0:
            return False
        changed = score is None or score >= self.threshold
        if not changed:
            return False
        t = self._clock() if now is None else now
        if not self._budget_left(t):
            return False
        self._passed.append(t)
        return True
