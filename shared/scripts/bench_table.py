#!/usr/bin/env python3
"""shared/bench/results/*.csv -> the deck's benchmark table (markdown) and an optional merged CSV.

Every scorer writes the header: metric,value,unit,device,build,ts
  unit "ms"   -> one row per trial -> reported as p50 / p95 (nearest rank)
  unit "%"    -> one row per run   -> reported as the mean, e.g. "96.0%"
  unit "₹"    -> one row per run   -> reported as the mean, e.g. "₹1.23"
  other ("s") -> one row per run   -> reported as the mean, e.g. "21.50 s"

Numbers are never invented. Every metric in EXPECTED_METRICS (spec §7) gets a row; one with
no measured data reads "pending". A metric name ending in "_ms" with unit "ms" is reported
under the name without the suffix (first_audio_ms -> first_audio).

Usage (from the repo root):
  python3 shared/scripts/bench_table.py > bench-table.md
  python3 shared/scripts/bench_table.py --merge shared/bench/merged.csv
  python3 shared/scripts/bench_table.py shared/bench/results/user_study.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

HEADER = ["metric", "value", "unit", "device", "build", "ts"]
RESULTS_DIR = Path(__file__).resolve().parents[2] / "shared" / "bench" / "results"

# Slide 12 rows, in slide order (spec §7 metrics + the user-study task completion).
EXPECTED_METRICS: tuple[str, ...] = (
    "count_accuracy",
    "count_abstain_rate",
    "invoice_catch_rate",
    "cash_total_accuracy",
    "product_id_accuracy",
    "guidance_time_to_touch",
    "soundbox_parse_accuracy",
    "sale_time_with_aira",
    "sale_time_baseline",
    "task_completion",
    "first_audio",
    "er2_count_latency",
    "invoice_check_latency",
    "cost_per_sale",
    "uptime",
)

PENDING = "pending"
NO_DEVICE = "—"


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value with at least q% of values at or below it."""
    if not values:
        raise ValueError("no values")
    if not 0 <= q <= 100:
        raise ValueError(f"percentile must be in 0..100, got {q}")
    s = sorted(values)
    k = max(1, math.ceil(q / 100 * len(s)))
    return s[k - 1]


def _parse_value(raw: str | None) -> float:
    try:
        v = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError(f"value {raw!r} is not a number") from None
    if not math.isfinite(v):
        raise ValueError(f"value {raw!r} is not a finite number")
    return v


def load_rows(paths: list[Path]) -> list[dict]:
    """Read benchmark CSVs. Raises ValueError on a wrong header or any row without a real number."""
    rows: list[dict] = []
    for p in paths:
        p = Path(p)
        with p.open(newline="", encoding="utf-8-sig") as f:  # -sig: a CSV saved by Excel starts with a BOM
            reader = csv.DictReader(f)
            if reader.fieldnames != HEADER:
                raise ValueError(f"{p}: header must be {','.join(HEADER)}")
            for row in reader:
                where = f"{p}:{reader.line_num}"
                if None in row or any(row.get(k) is None for k in HEADER):
                    raise ValueError(f"{where}: expected {len(HEADER)} columns")
                if not row["metric"].strip() or not row["unit"].strip():
                    raise ValueError(f"{where}: metric and unit are required")
                try:
                    _parse_value(row["value"])
                except ValueError as e:
                    raise ValueError(f"{where}: {e}") from None
                rows.append(row)
    return rows


def _metric_name(metric: str, unit: str) -> str:
    metric = metric.strip()
    if unit == "ms" and metric.endswith("_ms") and len(metric) > 3:
        return metric[:-3]
    return metric


def _format(vals: list[float], unit: str) -> str:
    if unit == "ms":
        return f"p50 {percentile(vals, 50):.0f} ms · p95 {percentile(vals, 95):.0f} ms"
    mean = sum(vals) / len(vals)
    if unit == "%":
        return f"{mean:.1f}%"
    if unit == "₹":
        return f"₹{mean:.2f}"
    return f"{mean:.2f} {unit}"


def summarize(rows: list[dict]) -> list[dict]:
    """One entry per (metric, unit) with measured data, sorted by metric then unit."""
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    devices: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in rows:
        unit = r["unit"].strip()
        key = (_metric_name(r["metric"], unit), unit)
        groups[key].append(_parse_value(r["value"]))
        if r["device"].strip():
            devices[key].add(r["device"].strip())
    summary: list[dict] = []
    for (metric, unit), vals in sorted(groups.items()):
        summary.append({
            "metric": metric,
            "unit": unit,
            "result": _format(vals, unit),
            "n": len(vals),
            "device": ", ".join(sorted(devices[(metric, unit)])) or NO_DEVICE,
        })
    return summary


def _cell(text: object) -> str:
    return str(text).replace("|", r"\|").replace("\n", " ")


def to_markdown(summary: list[dict], expected: tuple[str, ...] | list[str] = EXPECTED_METRICS) -> str:
    """Markdown table: every expected metric in order ("pending" if unmeasured), then any extras."""
    by_metric: dict[str, list[dict]] = defaultdict(list)
    for s in summary:
        by_metric[s["metric"]].append(s)
    ordered: list[dict] = []
    for metric in expected:
        ordered += by_metric.pop(metric, None) or [
            {"metric": metric, "result": PENDING, "n": 0, "device": NO_DEVICE}
        ]
    for metric in sorted(by_metric):
        ordered += by_metric[metric]
    lines = ["| Metric | Result | n | Device |", "|---|---|---|---|"]
    lines += [
        f"| {_cell(s['metric'])} | {_cell(s['result'])} | {s['n']} | {_cell(s['device'])} |"
        for s in ordered
    ]
    return "\n".join(lines)


def _load_default() -> list[dict]:
    """Load shared/bench/results/*.csv, skipping (with a warning) any file not in the benchmark format."""
    rows: list[dict] = []
    for p in sorted(RESULTS_DIR.glob("*.csv")):
        try:
            rows += load_rows([p])
        except ValueError as e:
            print(f"skipped {p.name}: {e}", file=sys.stderr)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AIRA benchmark table (deck slide 12)")
    ap.add_argument("paths", nargs="*", type=Path,
                    help="benchmark CSVs (default: shared/bench/results/*.csv)")
    ap.add_argument("--merge", type=Path, metavar="OUT",
                    help="also write every loaded row to one CSV")
    a = ap.parse_args(argv)

    if a.paths:
        try:
            rows = load_rows(a.paths)
        except (OSError, ValueError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    else:
        rows = _load_default()

    if not rows:
        print(f"no benchmark CSV rows found in {RESULTS_DIR}; every metric is {PENDING}",
              file=sys.stderr)

    if a.merge:
        a.merge.parent.mkdir(parents=True, exist_ok=True)
        with a.merge.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HEADER, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    print(to_markdown(summarize(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
