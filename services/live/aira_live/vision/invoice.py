"""Read a supplier bill twice, independently, and let code decide whether to trust it."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from aira_live.frames import Frame
from aira_live.vision.gemini import generate_json, image_part
from aira_live.vision.invoice_merge import merge_reads
from aira_live.vision.invoice_models import InvoiceLine, InvoiceRead

enhance_for_reading: Callable[[str], str] | None
try:
    from aira_live.vision.imaging import enhance_for_reading
except ImportError:  # Pillow missing: the service still boots; read B then runs on the original image
    enhance_for_reading = None

log = logging.getLogger(__name__)

__all__ = ["INVOICE_SCHEMA_HINT", "PROMPT_A", "PROMPT_B", "InvoiceLine", "InvoiceRead", "read_invoice"]

INVOICE_SCHEMA_HINT = (
    '{"supplier": string|null, "date": "YYYY-MM-DD"|null, "handwritten": boolean, '
    '"lines": [{"name": string, "qty": string, "unit": string, "unit_price": string, "line_total": string}], '
    '"total": string|null}'
)
PROMPT_A = (
    "You are reading a supplier invoice handed to a small Indian dairy shop (products like Aavin milk, "
    "Sakthi curd, Arun ice cream). Extract exactly what is printed or written. Copy every number as written, "
    "including mistakes; never compute, round or correct. Use null for anything you cannot read. "
    "Set handwritten=true if the quantities or prices are handwritten. Return JSON only: " + INVOICE_SCHEMA_HINT
)
PROMPT_B = (
    "Transcribe this bill as a table, row by row from top to bottom. For each item row give the product name, "
    "the quantity with its unit, the price per unit and the row amount, digit for digit as they appear "
    "(Tamil or Hindi digits are fine). Also give the shop/supplier name at the top, the bill date and the grand "
    "total at the bottom. Do not fix arithmetic. If a value is unclear write null. "
    "Say whether the figures are handwritten. Return JSON only: " + INVOICE_SCHEMA_HINT
)

THINKING = "medium"
TIMEOUT_S = 8.0
NO_IMAGE = "no image: hold the bill in front of the camera"
UNREADABLE = "could not read the bill: hold it flat and retake"


def _best(frames: list[Frame]) -> Frame:
    return max(frames, key=lambda f: (f.w * f.h, f.ts))


def _empty(reason: str) -> InvoiceRead:
    return InvoiceRead(supplier=None, date=None, lines=[], total=None, agreed=False, disagreements=[reason])


def _enhanced_or_original(jpeg_b64: str) -> str:
    if enhance_for_reading is None:
        log.warning("vision.invoice: Pillow not installed; read B uses the original image")
        return jpeg_b64
    try:
        return enhance_for_reading(jpeg_b64)
    except Exception:  # noqa: BLE001 - undecodable image: read B still runs, on the original
        return jpeg_b64


async def _safe(coro: Awaitable[dict]) -> tuple[dict | None, str | None]:
    try:
        raw = await coro
    except Exception as exc:  # noqa: BLE001 - model/network errors become a disagreement, never an exception
        return None, type(exc).__name__
    if not isinstance(raw, dict):
        return None, "InvalidResponse"
    return raw, None


async def _read_a(f: Frame) -> dict:
    return await generate_json("read", [PROMPT_A, image_part(f.jpeg_b64)], INVOICE_SCHEMA_HINT,
                               thinking=THINKING, timeout_s=TIMEOUT_S)


async def _read_b(f: Frame) -> dict:
    enhanced = await asyncio.to_thread(_enhanced_or_original, f.jpeg_b64)  # keep the event loop free
    return await generate_json("verify", [PROMPT_B, image_part(enhanced)], INVOICE_SCHEMA_HINT,
                               thinking=THINKING, timeout_s=TIMEOUT_S)


async def read_invoice(frames: list[Frame]) -> InvoiceRead:
    if not frames:
        return _empty(NO_IMAGE)
    f = _best(frames)
    (raw_a, err_a), (raw_b, err_b) = await asyncio.gather(_safe(_read_a(f)), _safe(_read_b(f)))
    if raw_a is None and raw_b is None:
        return _empty(UNREADABLE)
    try:
        if raw_a is not None and raw_b is not None:
            return merge_reads(raw_a, raw_b)
        only = raw_a if raw_a is not None else raw_b
        single = merge_reads(only, only)
    except Exception:  # noqa: BLE001 - malformed model output never escapes to the caller
        return _empty(UNREADABLE)
    reason = f"second read failed ({err_a or err_b}): confirm every line"
    return single.model_copy(update={"agreed": False, "disagreements": [reason, *single.disagreements]})
