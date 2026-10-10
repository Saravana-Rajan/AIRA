"""Shared helpers for AIRA vision benchmarks.

Run with the services/live venv:
    cd services/live && uv run python ../../bench/<script>.py ...

aira_live imports are lazy, so dataset checks (--dry-run) work without the
backend installed.
"""
from __future__ import annotations

import base64
import csv
import json
import math
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aira_live.frames import Frame
    from aira_live.vision.identify import ProductRef

BENCH = Path(__file__).resolve().parent
CANDIDATES = BENCH / "data" / "identify" / "candidates.json"

# JPEG start-of-frame markers that carry the image size (not DHT C4, JPG C8, DAC CC).
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def load_labels(path: Path, required: set[str]) -> list[dict]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path} must be a JSON list of rows")
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ValueError(f"{path} row {i} is not an object")
        missing = required - set(r)
        if missing:
            raise ValueError(f"{path} row {i} missing {sorted(missing)}")
    return rows


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG header, or None if the bytes are not a readable JPEG
    (e.g. a HEIC or PNG saved with a .jpg name)."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # standalone markers
            i += 2
            continue
        if marker in (0xD9, 0xDA):  # end of image / start of scan before any SOF
            return None
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in _SOF:
            if i + 9 > len(data):
                return None
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return (w, h) if w and h else None
        if seg_len < 2:
            return None
        i += 2 + seg_len
    return None


def frame_from_file(path: Path, purpose: str) -> "Frame":
    from aira_live.frames import Frame

    data = Path(path).read_bytes()
    w, h = jpeg_size(data) or (0, 0)
    return Frame(id=Path(path).stem, jpeg_b64=base64.b64encode(data).decode(), w=w, h=h,
                 ts=int(time.time() * 1000), purpose=purpose)


def percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("no values")
    v = sorted(values)
    if len(v) == 1:
        return float(v[0])
    k = (len(v) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return float(v[lo] + (v[hi] - v[lo]) * (k - lo))


async def timed(coro) -> tuple[Any, float]:
    t0 = time.perf_counter()
    result = await coro
    return result, (time.perf_counter() - t0) * 1000


def write_csv(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# Cross-team benchmark CSV (saravana/03, read by scripts/bench_table.py for the deck).
METRIC_HEADER = ["metric", "value", "unit", "device", "build", "ts"]


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp for the `ts` column, e.g. 2026-10-13T10:00:00Z."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_metrics_csv(path: Path, rows: list[dict]) -> None:
    """Write metric,value,unit,device,build,ts rows. Every value must be a real, finite number."""
    for r in rows:
        v = r.get("value")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"metric {r.get('metric')!r}: value {v!r} is not a finite number")
        if not r.get("metric") or not r.get("unit"):
            raise ValueError(f"metric and unit are required: {r!r}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=METRIC_HEADER, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


@contextmanager
def answering_models(module):
    """Record the "_model" of every generate_json reply that `module` receives (``""`` if unknown).

    generate_json retries and then falls back to Flash inside one call, and the vision results do not
    say which model answered. The spy is removed on exit."""
    seen: list[str] = []
    original = module.generate_json

    async def spy(*args, **kwargs):
        data = await original(*args, **kwargs)
        model = data.get("_model") if isinstance(data, dict) else None
        seen.append(model if isinstance(model, str) else "")
        return data

    module.generate_json = spy
    try:
        yield seen
    finally:
        module.generate_json = original


def _candidate_rows(path: Path = CANDIDATES) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def candidate_skus(path: Path = CANDIDATES) -> list[str]:
    """SKU ids from candidates.json, without importing aira_live."""
    return [c["sku"] for c in _candidate_rows(path)]


def load_candidates(path: Path = CANDIDATES) -> list["ProductRef"]:
    from aira_live.vision.identify import ProductRef

    return [ProductRef(**c) for c in _candidate_rows(path)]
