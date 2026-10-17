"""Read the cash in Sam's hand twice, with different output shapes; code agrees and totals.

Read A (role "read") groups the money by denomination. Read B (role "verify", a different model
generation) lists every piece on its own. Both look at the same original colour frame, because note
colour matters for telling denominations apart. No model ever decides the total, and nothing here
judges whether money is genuine.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable

from aira_live.frames import Frame
from aira_live.vision.cash_merge import HINT_SHOW_AGAIN, HINT_SPREAD, VALID_DENOMINATIONS, merge_cash
from aira_live.vision.cash_models import CashRead
from aira_live.vision.gemini import generate_json, image_part

log = logging.getLogger(__name__)

__all__ = ["PROMPT_A", "PROMPT_B", "SCHEMA_A", "SCHEMA_B", "VALID_DENOMINATIONS", "CashRead", "read_cash"]

SCHEMA_A = ('{"items": [{"denomination": integer, "count": integer, "kind": "note"|"coin"}], '
            '"overlapping": boolean, "confidence": number 0-1}')
SCHEMA_B = '{"pieces": [integer, ...], "overlapping": boolean, "confidence": number 0-1}'
PROMPT_A = (
    "Count the Indian rupee banknotes and coins visible in this photo. Group them by denomination. "
    "Indian notes: 10, 20, 50, 100, 200, 500, 2000. Coins: 1, 2, 5, 10, 20. Report every note even if it is "
    "a 2000 note. Set overlapping=true if any notes cover each other so a denomination is hidden. "
    "Do not judge whether money is genuine. Return JSON only: " + SCHEMA_A
)
PROMPT_B = (
    "List every individual piece of Indian money in this image, one entry per note or coin, giving only its "
    "face value in rupees. If two notes overlap so you cannot see one clearly, set overlapping=true. "
    "Never guess a hidden value and never comment on authenticity. Return JSON only: " + SCHEMA_B
)

THINKING = "medium"
TIMEOUT_S = 6.0


def _best(frames: list[Frame]) -> Frame:
    return max(frames, key=lambda f: (f.w * f.h, f.ts))


def _nothing(hint: str = HINT_SHOW_AGAIN) -> CashRead:
    return CashRead(notes=[], total=None, agreed=False, confidence=0.0, hint_key=hint)


async def _read_a(f: Frame) -> dict:
    return await generate_json("read", [PROMPT_A, image_part(f.jpeg_b64)], SCHEMA_A,
                               thinking=THINKING, timeout_s=TIMEOUT_S)


async def _read_b(f: Frame) -> dict:
    return await generate_json("verify", [PROMPT_B, image_part(f.jpeg_b64)], SCHEMA_B,
                               thinking=THINKING, timeout_s=TIMEOUT_S)


async def _safe(name: str, coro: Awaitable[dict]) -> dict | None:
    try:
        raw = await coro
    except Exception as exc:  # noqa: BLE001 - model/network/image errors become "show again", never an exception
        log.warning("vision.cash read %s failed: %r", name, exc)
        return None
    if not isinstance(raw, dict):
        log.warning("vision.cash read %s returned %s, not a JSON object", name, type(raw).__name__)
        return None
    return raw


async def read_cash(frames: list[Frame]) -> CashRead:
    if not frames:
        return _nothing()
    f = _best(frames)
    raw_a, raw_b = await asyncio.gather(_safe("A", _read_a(f)), _safe("B", _read_b(f)))
    if raw_a is None and raw_b is None:
        return _nothing()
    try:
        merged = merge_cash(raw_a or {}, raw_b or {})
    except Exception as exc:  # noqa: BLE001 - defensive: merge_cash is built never to raise
        log.warning("vision.cash merge failed: %r", exc)
        return _nothing()
    if raw_a is None or raw_b is None:
        # One read only is never enough to trust a total. Overlap seen by the working read is still worth saying.
        hint = HINT_SPREAD if merged.hint_key == HINT_SPREAD else HINT_SHOW_AGAIN
        return merged.model_copy(update={"agreed": False, "total": None, "hint_key": hint})
    return merged
