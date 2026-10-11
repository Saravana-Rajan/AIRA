#!/usr/bin/env python3
"""Score AIRA hand-guidance trials: seconds from guidance start to touching the correct pack (spec §7).

Usage (from the repo root; stdlib only):
    python3 shared/bench/score_guidance.py shared/bench/data/guidance/trials.json
    python3 shared/bench/score_guidance.py shared/bench/data/guidance/trials.json --out shared/bench/results --device "Pixel 7a"

Trial log (written by the app bench runner, ishwarya/03):
  {"trials": [{"trial": 1, "product": "sakthi_curd_1l", "startedAtMs": 0, "touchedAtMs": 8400, "correctItem": true}]}
touchedAtMs = null means the trial timed out. The spec target is 20 trials.

Prints the summary: trials, successes, successRate, wrongItem, timeouts, p50S, p95S, meanS
(seconds, correct touches only) and meetsSampleSize.

With --out, also writes (never for the *.example.json format example):
    <out>/guidance.csv   deck rows (metric,value,unit,device,build,ts) for shared/scripts/bench_table.py:
                         guidance_time_to_touch (ms, one row per correct touch), guidance_success_rate (%)
    <out>/guidance.json  the summary plus device, build, ts and source
Collection checklist: shared/bench/data/guidance/README.md.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import utc_now_iso, write_metrics_csv  # noqa: E402

SAMPLE_TARGET = 20
METRIC_TIME = "guidance_time_to_touch"
METRIC_SUCCESS = "guidance_success_rate"
EXAMPLE_SUFFIX = ".example.json"


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (the rule shared/scripts/bench_table.py uses); None when nothing was measured."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, math.ceil(p / 100 * len(ordered)) - 1)
    return ordered[k]


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _outcomes(trials: list[dict]) -> tuple[list[int], int, int]:
    """(ms to touch for each correct touch, wrong-item touches, timeouts). Raises ValueError on a bad trial."""
    times_ms: list[int] = []
    wrong = timeouts = 0
    for i, tr in enumerate(trials, start=1):
        if not isinstance(tr, dict):
            raise ValueError(f"trial {i}: expected an object, got {type(tr).__name__}")
        name = f"trial {tr.get('trial', i)}"
        if not _is_int(tr.get("startedAtMs")):
            raise ValueError(f"{name}: startedAtMs must be an integer (ms)")
        if "touchedAtMs" not in tr or not (tr["touchedAtMs"] is None or _is_int(tr["touchedAtMs"])):
            raise ValueError(f"{name}: touchedAtMs must be an integer (ms) or null")
        if not isinstance(tr.get("correctItem"), bool):
            raise ValueError(f"{name}: correctItem must be true or false")
        if tr["touchedAtMs"] is None:
            timeouts += 1
            continue
        if tr["touchedAtMs"] < tr["startedAtMs"]:
            raise ValueError(f"{name}: touched before start")
        if not tr["correctItem"]:
            wrong += 1
            continue
        times_ms.append(tr["touchedAtMs"] - tr["startedAtMs"])
    return times_ms, wrong, timeouts


def score(trials: list[dict]) -> dict:
    times_ms, wrong, timeouts = _outcomes(trials)
    times = [ms / 1000.0 for ms in times_ms]
    n = len(trials)
    return {
        "trials": n,
        "successes": len(times),
        "successRate": round(len(times) / n, 3) if n else 0.0,
        "wrongItem": wrong,
        "timeouts": timeouts,
        "p50S": percentile(times, 50),
        "p95S": percentile(times, 95),
        "meanS": round(sum(times) / len(times), 2) if times else None,
        "meetsSampleSize": n >= SAMPLE_TARGET,
    }


def metric_rows(trials: list[dict], *, device: str, build: str, ts: str) -> list[dict]:
    """Deck rows: one guidance_time_to_touch row (ms) per correct touch and one guidance_success_rate (%).

    Wrong-item touches and timeouts have no time; they show up only in the success rate."""
    if not trials:
        return []
    times_ms, _, _ = _outcomes(trials)
    base = {"device": device, "build": build, "ts": ts}
    rows = [{"metric": METRIC_TIME, "value": ms, "unit": "ms", **base} for ms in times_ms]
    rows.append({"metric": METRIC_SUCCESS, "value": round(100 * len(times_ms) / len(trials), 1),
                 "unit": "%", **base})
    return rows


def _load_trials(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("trials"), list):
        raise ValueError(f'{path}: expected {{"trials": [...]}}')
    return data["trials"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("trials", type=Path, help="trial log JSON, e.g. shared/bench/data/guidance/trials.json")
    ap.add_argument("--out", type=Path, help="results folder: also write guidance.csv and guidance.json")
    ap.add_argument("--device", default="", help="phone used for the trials, e.g. 'Pixel 7a'")
    ap.add_argument("--build", default=os.environ.get("AIRA_BUILD", "dev"), help="build or git sha")
    args = ap.parse_args(argv)

    if args.out and args.trials.name.endswith(EXAMPLE_SUFFIX):
        print(f"error: {args.trials.name} is the format example, not measurements; record real trials "
              "in shared/bench/data/guidance/trials.json (see README.md). Nothing was written.", file=sys.stderr)
        return 2
    try:
        trials = _load_trials(args.trials)
        summary = score(trials)
    except (OSError, ValueError) as e:  # json.JSONDecodeError is a ValueError
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.out:
        ts = utc_now_iso()
        write_metrics_csv(args.out / "guidance.csv",
                          metric_rows(trials, device=args.device, build=args.build, ts=ts))
        record = {**summary, "device": args.device, "build": args.build, "ts": ts, "source": str(args.trials)}
        (args.out / "guidance.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if not summary["meetsSampleSize"]:
        print(f"note: {summary['trials']} trials; the spec target is {SAMPLE_TARGET}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
