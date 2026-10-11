"""Opt-in real-model tests. Everything in this folder talks to Gemini and is skipped unless

    AIRA_LIVE_TESTS=1 and GEMINI_API_KEY are both set,

so the normal ``uv run pytest -q`` (and CI) stays offline. Shared here: the skip rule, synthetic image
helpers (every image is drawn in the test; nothing is a photo of a real shop) and a latency table that is
printed at the end of the run.
"""
from __future__ import annotations

import base64
import io
import os
import time
from dataclasses import dataclass, field

import pytest
from PIL import Image, ImageDraw, ImageFont

from aira_live.frames import Frame

ENABLED = os.getenv("AIRA_LIVE_TESTS") == "1" and bool(os.getenv("GEMINI_API_KEY"))
SKIP_REASON = "real Gemini models: set AIRA_LIVE_TESTS=1 and GEMINI_API_KEY"

_LATENCIES: list[tuple[str, float]] = []


def pytest_collection_modifyitems(config, items):
    if ENABLED:
        return
    here = os.path.dirname(__file__)
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if str(item.fspath).startswith(here):
            item.add_marker(skip)


def pytest_terminal_summary(terminalreporter):
    if not _LATENCIES:
        return
    terminalreporter.section("real-model latencies")
    for name, ms in _LATENCIES:
        terminalreporter.write_line(f"{name:<48} {ms:8.0f} ms")


@dataclass
class Latency:
    """``with lat.measure("name"):`` records and prints the wall-clock time of a block."""

    entries: list[tuple[str, float]] = field(default_factory=list)

    def record(self, name: str, seconds: float) -> float:
        ms = seconds * 1000.0
        self.entries.append((name, ms))
        _LATENCIES.append((name, ms))
        print(f"[latency] {name}: {ms:.0f} ms")
        return ms

    def measure(self, name: str):
        return _Timer(self, name)


class _Timer:
    def __init__(self, lat: Latency, name: str):
        self._lat, self._name, self._t0 = lat, name, 0.0

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self._lat.record(self._name, time.perf_counter() - self._t0)
        return False


@pytest.fixture
def lat() -> Latency:
    return Latency()


@pytest.fixture(autouse=True)
def pinned_models(monkeypatch):
    """Models come from ``models.DEFAULTS`` (or an explicit ``AIRA_MODEL_<ROLE>``), never from Firestore.

    The remote-config lookup would otherwise kick off a Firestore refresh in the background, and these tests
    run without a GCP project.
    """
    from aira_live import remote_config

    monkeypatch.setattr(remote_config, "override_for_role", lambda role: None)


# ---------- synthetic images ----------
SYNTHETIC_BANNER = "SYNTHETIC TEST IMAGE - drawn by a test, not a photo"


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default font
        return ImageFont.load_default()


def to_jpeg_b64(img: Image.Image, quality: int = 92) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def to_frame(img: Image.Image, purpose: str, frame_id: str = "synthetic") -> Frame:
    return Frame(id=frame_id, jpeg_b64=to_jpeg_b64(img), w=img.width, h=img.height, ts=1.0, purpose=purpose)


def to_norm(x: float, y: float, img: Image.Image) -> tuple[int, int]:
    """Pixel (x, y) -> AIRA's [y, x] in 0..1000."""
    return round(y * 1000 / img.height), round(x * 1000 / img.width)


@dataclass(frozen=True)
class Drawn:
    """One drawn item: its label and centre as AIRA's normalised [y, x]."""

    label: str
    yx: tuple[int, int]


def draw_items(n: int, label: str = "blue circle", size: tuple[int, int] = (1024, 768),
               odd_one: str | None = None) -> tuple[Image.Image, list[Drawn]]:
    """A white canvas with ``n`` well-separated blue circles on a grid, plus an optional single red square.

    Items are spread on a grid with wide gaps so none touches the frame edge or another item. The banner at
    the top says the image is synthetic.
    """
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.text((16, 12), SYNTHETIC_BANNER, fill="black", font=font(22))
    cols = 4
    rows = (n + (1 if odd_one else 0) + cols - 1) // cols
    top, bottom = 70, size[1] - 40
    cell_w = size[0] / cols
    cell_h = (bottom - top) / max(rows, 1)
    r = int(min(cell_w, cell_h) * 0.22)
    items: list[Drawn] = []
    slots = [(c, rw) for rw in range(rows) for c in range(cols)]
    for i in range(n):
        c, rw = slots[i]
        cx, cy = cell_w * (c + 0.5), top + cell_h * (rw + 0.5)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(30, 90, 220), outline="black", width=3)
        items.append(Drawn(label, to_norm(cx, cy, img)))
    if odd_one:
        c, rw = slots[n]
        cx, cy = cell_w * (c + 0.5), top + cell_h * (rw + 0.5)
        d.rectangle((cx - r, cy - r, cx + r, cy + r), fill=(220, 40, 40), outline="black", width=3)
        items.append(Drawn(odd_one, to_norm(cx, cy, img)))
    return img, items


BILL_LINES: list[tuple[str, int, str, int]] = [
    # (name, qty, unit, unit_price) -> line_total = qty * unit_price
    ("Aavin Milk 500 ml", 10, "pack", 28),
    ("Sakthi Curd 200 g", 5, "cup", 20),
    ("Arun Ice Cream 100 ml", 4, "cup", 30),
]
BILL_SUPPLIER = "Test Dairy Supplies (synthetic)"
BILL_DATE = "2026-10-11"


def bill_total() -> int:
    return sum(q * p for _, q, _, p in BILL_LINES)


def draw_bill(size: tuple[int, int] = (900, 700)) -> Image.Image:
    """A clean, printed-looking bill rendered with PIL text. Arithmetic is correct by construction."""
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    big, body, small = font(34), font(26), font(18)
    d.text((40, 20), SYNTHETIC_BANNER, fill="gray", font=small)
    d.text((40, 60), BILL_SUPPLIER, fill="black", font=big)
    d.text((40, 110), f"Invoice No: T-0001    Date: {BILL_DATE}", fill="black", font=body)
    d.text((40, 150), "To: Murugan Dairy", fill="black", font=body)
    y = 220
    d.line((40, y - 10, size[0] - 40, y - 10), fill="black", width=2)
    d.text((40, y), "Item", fill="black", font=body)
    d.text((440, y), "Qty", fill="black", font=body)
    d.text((560, y), "Rate", fill="black", font=body)
    d.text((700, y), "Amount", fill="black", font=body)
    y += 40
    d.line((40, y - 5, size[0] - 40, y - 5), fill="black", width=2)
    for name, qty, unit, price in BILL_LINES:
        d.text((40, y), name, fill="black", font=body)
        d.text((440, y), f"{qty} {unit}", fill="black", font=body)
        d.text((560, y), f"{price}.00", fill="black", font=body)
        d.text((700, y), f"{qty * price}.00", fill="black", font=body)
        y += 44
    d.line((40, y + 5, size[0] - 40, y + 5), fill="black", width=2)
    y += 25
    d.text((560, y), "TOTAL", fill="black", font=big)
    d.text((700, y), f"Rs {bill_total()}.00", fill="black", font=big)
    return img


@pytest.fixture
def synth():
    """The synthetic-image helpers as one object (tests cannot import a conftest under importlib mode)."""
    from types import SimpleNamespace

    return SimpleNamespace(draw_items=draw_items, draw_bill=draw_bill, to_frame=to_frame, to_jpeg_b64=to_jpeg_b64,
                           BILL_LINES=BILL_LINES, BILL_SUPPLIER=BILL_SUPPLIER, BILL_DATE=BILL_DATE,
                           bill_total=bill_total)
