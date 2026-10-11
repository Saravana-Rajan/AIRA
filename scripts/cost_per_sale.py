#!/usr/bin/env python3
"""Estimate the Gemini API cost per AIRA sale, delivery and shop-month (feeds deck slide 10).

Usage (from the repo root):
    python3 scripts/cost_per_sale.py --rate <USD->INR rate on the day you quote it>
    python3 scripts/cost_per_sale.py --rate 88 --sales-per-day 60 --deliveries-per-day 2

--rate is required: the script never assumes an exchange rate.

Prices: Gemini API paid tier, https://ai.google.dev/gemini-api/docs/pricing, checked 2026-10-11:
  - gemini-robotics-er-2-preview  $1.00 in / $5.00 out per 1M tokens (promotional, through 2026-12-31)
  - gemini-3.8-flash and gemini-3.6-flash  $0.75 in / $3.75 out per 1M tokens (through 2026-12-31)
  - gemini-3.8-live audio  $0.005 per minute in / $0.018 per minute out
Thinking tokens are billed at the output rate, so every vision call adds a thinking allowance that follows
the thinking level the code asks for (vision/count.py "high", cash.py and invoice.py "medium", identify.py
and point.py "low"). THINKING_TOKENS are planning allowances, not measurements: replace them with the
usage_metadata.thoughts_token_count logged by the bench runs before quoting.
Model IDs live only in services/live/aira_live/models.py; this script prices the roles, not the IDs. Read B
runs gemini-3.6-flash, priced the same as 3.8 Flash on the page above (FLASH_B kept separate in case that
changes). Cloud Run / Firestore / Hosting are shared across shops and excluded here (say so on the slide).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

PRICE_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing (checked 2026-10-11)"
USD_PER_1M = {
    "er2_in": 1.00, "er2_out": 5.00,          # gemini-robotics-er-2-preview, through 2026-12-31
    "flash_a_in": 0.75, "flash_a_out": 3.75,  # gemini-3.8-flash (read A), through 2026-12-31
    "flash_b_in": 0.75, "flash_b_out": 3.75,  # gemini-3.6-flash (read B), through 2026-12-31
}
LIVE_AUDIO_IN_PER_MIN = 0.005   # USD, gemini-3.8-live audio input
LIVE_AUDIO_OUT_PER_MIN = 0.018  # USD, gemini-3.8-live audio output
IMAGE_TOKENS = 1300             # one <=640 px JPEG + prompt
OUT_TOKENS = 200                # the JSON answer
# Thinking allowance per call, billed as output tokens. Keys follow the thinking level each role asks for.
THINKING_TOKENS = {
    "count": 2000,     # ER 2, thinking "high" (vision/count.py)
    "identify": 300,   # ER 2, thinking "low" (vision/identify.py)
    "point": 300,      # ER 2, thinking "low" (vision/point.py)
    "read": 800,       # Flash, thinking "medium" (vision/cash.py, vision/invoice.py)
}


def er2_call(role: str = "identify", image_tokens: int = IMAGE_TOKENS, out_tokens: int = OUT_TOKENS) -> float:
    """One Robotics-ER 2 call for `role` ("count", "identify" or "point"), thinking included."""
    out = out_tokens + THINKING_TOKENS[role]
    return (image_tokens * USD_PER_1M["er2_in"] + out * USD_PER_1M["er2_out"]) / 1e6


def flash_call(read: str = "a", image_tokens: int = IMAGE_TOKENS, out_tokens: int = OUT_TOKENS) -> float:
    """One Flash read ("a" = gemini-3.8-flash, "b" = gemini-3.6-flash) with the "medium" thinking allowance."""
    out = out_tokens + THINKING_TOKENS["read"]
    return (image_tokens * USD_PER_1M[f"flash_{read}_in"] + out * USD_PER_1M[f"flash_{read}_out"]) / 1e6


def two_reads() -> float:
    """Read A (3.8 Flash) plus the independent read B (3.6 Flash), as cash.py and invoice.py do."""
    return flash_call("a") + flash_call("b")


@dataclass(frozen=True)
class SaleProfile:
    talk_in_min: float = 0.75   # customer + owner speech heard by Live
    talk_out_min: float = 0.25  # AIRA speech
    er2_checks: int = 1         # "Sakthi curd 1 L" check of the pack in hand (identify)
    cash_reads: int = 1         # one cash check = two independent Flash reads (A + B)


@dataclass(frozen=True)
class DeliveryProfile:
    packs: int = 30             # one-by-one count frames while stocking (ER 2, thinking high)
    invoice_reads: int = 1      # one bill check = two independent Flash reads (A + B)
    cash_reads: int = 1         # one cash check = two independent Flash reads (A + B)
    talk_in_min: float = 2.0
    talk_out_min: float = 1.0


def _live_usd(talk_in_min: float, talk_out_min: float) -> float:
    return talk_in_min * LIVE_AUDIO_IN_PER_MIN + talk_out_min * LIVE_AUDIO_OUT_PER_MIN


def usd_per_sale(p: SaleProfile = SaleProfile()) -> float:
    return (_live_usd(p.talk_in_min, p.talk_out_min)
            + p.er2_checks * er2_call("identify")
            + p.cash_reads * two_reads())


def usd_per_delivery(p: DeliveryProfile = DeliveryProfile()) -> float:
    return (
        _live_usd(p.talk_in_min, p.talk_out_min)
        + p.packs * er2_call("count")
        + (p.invoice_reads + p.cash_reads) * two_reads()
    )


def monthly_usd(sales_per_day: int, deliveries_per_day: int, days: int = 30) -> float:
    return days * (sales_per_day * usd_per_sale() + deliveries_per_day * usd_per_delivery())


def to_inr(usd: float, rate: float) -> float:
    return usd * rate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AIRA cost estimate (Gemini API only)")
    ap.add_argument("--rate", type=float, required=True, help="USD->INR rate on the day you quote it (required)")
    ap.add_argument("--sales-per-day", type=int, default=60)
    ap.add_argument("--deliveries-per-day", type=int, default=2)
    a = ap.parse_args(argv)
    sale, delivery = usd_per_sale(), usd_per_delivery()
    month = monthly_usd(a.sales_per_day, a.deliveries_per_day)
    print(f"Per sale: ${sale:.4f} = ₹{to_inr(sale, a.rate):.2f}")
    print(f"Per delivery: ${delivery:.4f} = ₹{to_inr(delivery, a.rate):.2f}")
    print(
        f"Per shop-month ({a.sales_per_day} sales/day, {a.deliveries_per_day} deliveries/day): "
        f"${month:.2f} = ₹{to_inr(month, a.rate):,.0f}"
    )
    print("Gemini API only; Cloud Run/Firestore/Hosting are shared across shops and excluded. "
          "Thinking tokens included as allowances; prices from " + PRICE_SOURCE + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
