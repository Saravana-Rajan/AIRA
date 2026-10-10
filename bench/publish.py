"""Summarize AIRA bench NDJSON into a p50/p95 CSV per metric and device.

Input rows come from the app's /bench export, one JSON object per line:
  {metric, value, unit, device, build, ts (ISO-8601)}
Output: bench/results/summary.csv (source for the deck's benchmark slide).

Usage: python bench/publish.py <files.ndjson...> [--csv bench/results/summary.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from datetime import datetime

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(BENCH_DIR, "results", "summary.csv")
FIELDS = ["metric", "device", "unit", "n", "p50", "p95", "mean"]
REQUIRED = {"metric": str, "unit": str, "device": str, "build": str, "ts": str}


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    s = sorted(values)
    rank = max(1, min(len(s), math.ceil(p / 100 * len(s))))
    return s[rank - 1]


def _valid(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    for key, typ in REQUIRED.items():
        if not isinstance(row.get(key), typ) or not row[key]:
            return False
    value = row.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    try:
        datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def load_ndjson(paths: list[str]) -> tuple[list[dict], int]:
    """Read NDJSON files; return (valid rows, number of skipped non-empty lines)."""
    rows: list[dict] = []
    skipped = 0
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if _valid(row):
                    rows.append(row)
                else:
                    skipped += 1
    return rows, skipped


def _num(x: float | None) -> float | int | None:
    return int(x) if x is not None and float(x).is_integer() else x


def summarize(rows: list[dict]) -> list[dict]:
    """One summary row per (metric, device, unit), sorted."""
    groups: dict[tuple[str, str, str], list[float]] = {}
    for r in rows:
        groups.setdefault((r["metric"], r["device"], r["unit"]), []).append(float(r["value"]))
    out = []
    for (metric, device, unit), vals in sorted(groups.items()):
        out.append({
            "metric": metric, "device": device, "unit": unit, "n": len(vals),
            "p50": _num(percentile(vals, 50)),
            "p95": _num(percentile(vals, 95)),
            "mean": round(statistics.fmean(vals), 2),
        })
    return out


def write_csv(summary: list[dict], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(summary)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Summarize AIRA bench NDJSON into a p50/p95 CSV.")
    ap.add_argument("files", nargs="+", help="NDJSON files exported from the app's /bench page")
    ap.add_argument("--csv", default=DEFAULT_CSV, help="output CSV (default: bench/results/summary.csv)")
    args = ap.parse_args(argv)

    rows, skipped = load_ndjson(args.files)
    if not rows:
        print(f"no valid rows (skipped={skipped}); nothing written", file=sys.stderr)
        return 1
    summary = summarize(rows)
    write_csv(summary, args.csv)
    for s in summary:
        print(f"{s['metric']:<28} {s['device']:<18} n={s['n']:<4} p50={s['p50']} p95={s['p95']} {s['unit']}")
    print(f"valid={len(rows)} skipped={skipped} csv={args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
