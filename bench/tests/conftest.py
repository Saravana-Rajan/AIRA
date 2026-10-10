"""Test setup for bench/ scripts: puts bench/ and services/live/ on sys.path.

Run from services/live so the real aira_live modules and their deps are importable:
    cd services/live && uv run pytest ../../bench/tests -q
Every test is offline: anything that would call Gemini is monkeypatched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
REPO = BENCH.parent
for p in (BENCH, REPO / "services" / "live"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def tiny_jpeg(w: int = 640, h: int = 480) -> bytes:
    """Smallest byte string our JPEG header parser accepts (SOI, APP0, SOF0, EOI). A test input, not a photo."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = (b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + h.to_bytes(2, "big")
            + w.to_bytes(2, "big") + b"\x03" + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


@pytest.fixture
def make_jpeg():
    return tiny_jpeg


@pytest.fixture(autouse=True)
def _no_model_env(monkeypatch):
    """A developer's AIRA_MODEL_* overrides must not leak into the spike tests."""
    for role in ("POINT", "COUNT", "READ", "VERIFY", "LIVE"):
        monkeypatch.delenv(f"AIRA_MODEL_{role}", raising=False)
