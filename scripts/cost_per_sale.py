#!/usr/bin/env python3
"""Estimate the Gemini API cost per AIRA sale, delivery and shop-month (feeds deck slide 10).

Usage (from the repo root):
    python3 scripts/cost_per_sale.py --rate <USD->INR rate on the day you quote it>
    python3 scripts/cost_per_sale.py --rate 88 --sales-per-day 60 --deliveries-per-day 2

Prices: Gemini API paid tier, promotional rates valid through 2026-12-31. Re-check them at
https://ai.google.dev/gemini-api/docs/pricing before quoting. Model IDs live only in
services/live/aira_live/models.py; this script prices the roles (Live audio, Robotics-ER 2,
Flash reads), not the IDs. Cloud Run / Firestore / Hosting are shared across shops and
excluded here (say so on the slide).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

USD_PER_1M = {"er2_in": 1.00, "er2_out": 5.00, "flash_in": 0.75, "flash_out": 3.75}
LIVE_AUDIO_IN_PER_MIN = 0.005   # USD, Live model audio input
LIVE_AUDIO_OUT_PER_MIN = 0.018  # USD, Live model audio output
IMAGE_TOKENS = 1300             # one <=640 px JPEG + prompt
OUT_TOKENS = 200


def er2_call(image_tokens: int = IMAGE_TOKENS, out_tokens: int = OUT_TOKENS) -> float:
    return (image_tokens * USD_PER_1M["er2_in"] + out_tokens * USD_PER_1M["er2_out"]) / 1e6


def flash_call(image_tokens: int = IMAGE_TOKENS, out_tokens: int = OUT_TOKENS) -> float:
    return (image_tokens * USD_PER_1M["flash_in"] + out_tokens * USD_PER_1M["flash_out"]) / 1e6


@dataclass(frozen=True)
class SaleProfile:
    talk_in_min: float = 0.75   # customer + owner speech heard by Live
    talk_out_min: float = 0.25  # AIRA speech
    er2_checks: int = 1         # "Sakthi curd 1 L" check of the pack in hand
    cash_reads: int = 2         # two independent cash reads


@dataclass(frozen=True)
class DeliveryProfile:
    packs: int = 30             # one-by-one identification while stocking
    invoice_reads: int = 2
    cash_reads: int = 2
    talk_in_min: float = 2.0
    talk_out_min: float = 1.0


def _live_usd(talk_in_min: float, talk_out_min: float) -> float:
    return talk_in_min * LIVE_AUDIO_IN_PER_MIN + talk_out_min * LIVE_AUDIO_OUT_PER_MIN


def usd_per_sale(p: SaleProfile = SaleProfile()) -> float:
    return _live_usd(p.talk_in_min, p.talk_out_min) + p.er2_checks * er2_call() + p.cash_reads * flash_call()


def usd_per_delivery(p: DeliveryProfile = DeliveryProfile()) -> float:
    return (
        _live_usd(p.talk_in_min, p.talk_out_min)
        + p.packs * er2_call()
        + (p.invoice_reads + p.cash_reads) * flash_call()
    )


def monthly_usd(sales_per_day: int, deliveries_per_day: int, days: int = 30) -> float:
    return days * (sales_per_day * usd_per_sale() + deliveries_per_day * usd_per_delivery())


def to_inr(usd: float, rate: float) -> float:
    return usd * rate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AIRA cost estimate (Gemini API only)")
    ap.add_argument("--rate", type=float, default=88.0, help="USD->INR rate on the day you quote it")
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
    print("Gemini API only; Cloud Run/Firestore/Hosting are shared across shops and excluded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
