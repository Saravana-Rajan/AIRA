"""Counting accuracy on shared/bench/data/count (numbers for the deck's benchmark slide).

Usage (from backend):
    uv run python ../shared/bench/score_count.py --dry-run          # offline: labels + photos only
    uv run python ../shared/bench/score_count.py --device "Pixel 7a"  # real Gemini calls; needs GEMINI_API_KEY

Writes, only after a run in which every call returned:
    shared/bench/results/count.csv       deck rows (metric,value,unit,device,build,ts) for shared/scripts/bench_table.py:
                                  count_accuracy, count_abstain_rate, er2_count_latency_ms
                                  (+ count_fallback_latency_ms for calls the Flash fallback answered)
    shared/bench/results/count.json      summary: n, exact, within1, mae, abstain, errors, fallbacks, p50_ms, p95_ms
    shared/bench/results/rows/count.csv  one row per photo (always written after a live run, for debugging)

Abstentions (needs_retake) are AIRA saying "show me again"; they are reported, never guessed.
Photo checklist: shared/bench/data/count/README.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    answering_models, frame_from_file, jpeg_size, load_labels, percentile, timed, utc_now_iso,
    write_csv, write_metrics_csv,
)

BENCH = Path(__file__).resolve().parent
DATA = BENCH / "data" / "count"
RESULTS = BENCH / "results"
REQUIRED = {"file", "label", "count"}
ROLE = "count"


def _rate(k: int, n: int) -> float | None:
    return round(k / n, 3) if n else None


def _tally(rows: list[dict]) -> dict:
    failed = [r for r in rows if r.get("error")]
    answered = [r for r in rows if not r.get("error") and r["pred"] is not None]
    return {"n": len(rows), "failed": len(failed), "answered": answered,
            "abstained": len(rows) - len(failed) - len(answered),
            "exact": sum(1 for r in answered if r["pred"] == r["truth"]),
            "within1": sum(1 for r in answered if abs(r["pred"] - r["truth"]) <= 1)}


def score_counts(rows: list[dict]) -> dict:
    """rows: {"truth": int, "pred": int | None, "error"?: str}. pred None = AIRA asked for a retake.

    A failed call (``error``) is counted in ``n`` and ``errors`` but is not an abstention."""
    t = _tally(rows)
    n, answered = t["n"], t["answered"]
    mae = sum(abs(r["pred"] - r["truth"]) for r in answered) / len(answered) if answered else None
    return {"n": n, "exact": _rate(t["exact"], n), "within1": _rate(t["within1"], n),
            "mae": round(mae, 3) if mae is not None else None,
            "abstain": _rate(t["abstained"], n), "errors": t["failed"]}


def _ok_ms(r: dict) -> bool:
    return not r.get("error") and isinstance(r.get("ms"), (int, float)) and math.isfinite(r["ms"])


def metric_rows(rows: list[dict], *, er2_model: str, device: str, build: str, ts: str) -> list[dict]:
    """Deck rows for shared/scripts/bench_table.py. Nothing for an empty set: numbers are never invented.

    er2_count_latency_ms only has calls ER 2 really answered; calls the fallback answered go to
    count_fallback_latency_ms so their wait is shown, not hidden. Unknown answering model: skipped."""
    t = _tally(rows)
    n = t["n"]
    if not n:
        return []

    def row(metric: str, value: float, unit: str) -> dict:
        return {"metric": metric, "value": value, "unit": unit, "device": device, "build": build, "ts": ts}

    out = [row("count_accuracy", round(100 * t["exact"] / n, 1), "%"),
           row("count_abstain_rate", round(100 * t["abstained"] / n, 1), "%")]
    for r in rows:
        if not _ok_ms(r) or not r.get("answered_by"):
            continue
        metric = "er2_count_latency_ms" if r["answered_by"] == er2_model else "count_fallback_latency_ms"
        out.append(row(metric, float(r["ms"]), "ms"))
    return out


def check_dataset(rows: list[dict], data_dir: Path) -> dict:
    """Offline checks before spending Gemini calls. Indexes in bad_count / bad_label are label rows."""
    data_dir = Path(data_dir)
    missing, not_jpeg, bad_count, bad_label = [], [], [], []
    for i, r in enumerate(rows):
        p = data_dir / str(r["file"])
        if not p.is_file():
            if r["file"] not in missing:
                missing.append(r["file"])
        elif jpeg_size(p.read_bytes()) is None and r["file"] not in not_jpeg:
            not_jpeg.append(r["file"])
        c = r["count"]
        if isinstance(c, bool) or not isinstance(c, int) or c < 0:
            bad_count.append(i)
        if not isinstance(r["label"], str) or not r["label"].strip():
            bad_label.append(i)
    return {"n": len(rows), "missing": missing, "not_jpeg": not_jpeg, "bad_count": bad_count,
            "bad_label": bad_label,
            "ready": bool(rows) and not (missing or not_jpeg or bad_count or bad_label)}


def _models() -> tuple[str, str | None, str]:
    from aira_live.models import DEFAULTS, fallback_for, model_for

    return model_for(ROLE), fallback_for(ROLE), DEFAULTS[ROLE]


def _print_dataset(rep: dict, labels_path: Path) -> None:
    print(f"labels : {labels_path} ({rep['n']} photos)")
    if rep["missing"]:
        print(f"missing files : {rep['missing']}")
    if rep["not_jpeg"]:
        print(f"not JPEG (re-save as .jpg, not HEIC/PNG): {rep['not_jpeg']}")
    if rep["bad_count"]:
        print(f"rows whose count is not a whole number >= 0: {rep['bad_count']}")
    if rep["bad_label"]:
        print(f"rows with an empty label: {rep['bad_label']}")


async def run(labels: list[dict], data_dir: Path) -> list[dict]:
    """Count every labelled photo, one call at a time (so latency is not skewed by concurrency)."""
    from aira_live.vision import count

    rows = []
    with answering_models(count, "generate_count_json") as seen:
        for r in labels:
            frame = frame_from_file(Path(data_dir) / r["file"], "count")
            n_seen = len(seen)
            try:
                res, ms = await timed(count.count_in_frame(frame, r["label"]))
                pred = None if res.needs_retake else res.count
                raw, conf, hint, err = res.count, res.confidence, res.aim_hint, ""
            except Exception as e:  # record the failure; never turn it into a number
                pred, raw, conf, hint, ms, err = None, None, 0.0, "", float("nan"), repr(e)
            rows.append({"file": r["file"], "label": r["label"], "truth": r["count"], "pred": pred,
                         "raw_count": raw, "confidence": conf, "aim_hint": hint,
                         "answered_by": seen[-1] if len(seen) > n_seen else "",
                         "ms": round(ms, 1), "error": err})
    return rows


def summarize(rows: list[dict], model: str) -> dict:
    ms = [r["ms"] for r in rows if _ok_ms(r)]
    return {
        **score_counts(rows),
        "p50_ms": round(percentile(ms, 50)) if ms else None,
        "p95_ms": round(percentile(ms, 95)) if ms else None,
        # Calls the configured model did not answer (generate_json fell back). Unknown ("") is not counted.
        "fallbacks": sum(1 for r in rows if r.get("answered_by") and r["answered_by"] != model),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--out", type=Path, default=RESULTS, help="results folder")
    ap.add_argument("--dry-run", action="store_true", help="check photos and labels; no Gemini calls")
    ap.add_argument("--device", default="", help="phone that took the photos, e.g. 'Pixel 7a'")
    ap.add_argument("--build", default=os.environ.get("AIRA_BUILD", "dev"), help="build or git sha")
    args = ap.parse_args(argv)

    labels_path = args.data / "labels.json"
    if not labels_path.is_file():
        print(f"NOT READY: {labels_path} not found. Follow shared/bench/data/count/README.md.")
        return 1
    labels = load_labels(labels_path, REQUIRED)
    rep = check_dataset(labels, args.data)
    if args.dry_run:
        print("DRY RUN (no Gemini calls)")
        _print_dataset(rep, labels_path)
        try:
            model, fallback, _ = _models()
            print(f"models : {model} (fallback {fallback})")
        except Exception as e:  # backend not installed: the data check still counts
            print(f"models : unavailable ({e!r})")
    if not rep["ready"]:
        if not args.dry_run:
            _print_dataset(rep, labels_path)
        print("NOT READY: " + ("no labelled photos yet. Follow shared/bench/data/count/README.md."
                               if not rep["n"] else "fix the problems above."))
        return 1
    if args.dry_run:
        print(f"READY: {rep['n']} photos = {rep['n']} count calls.")
        return 0

    model, _, er2_model = _models()
    rows = asyncio.run(run(labels, args.data))
    write_csv(args.out / "rows" / "count.csv", rows)
    summary = summarize(rows, model)
    if summary["errors"]:
        print(json.dumps(summary))
        print(f"NO RESULT: {summary['errors']} of {summary['n']} calls failed (see the 'error' column in "
              f"{args.out / 'rows' / 'count.csv'}). Check GEMINI_API_KEY and run again; "
              "the deck files were not written.")
        return 1
    ts = utc_now_iso()
    summary.update({"model": model, "device": args.device, "build": args.build, "ts": ts})
    write_metrics_csv(args.out / "count.csv",
                      metric_rows(rows, er2_model=er2_model, device=args.device, build=args.build, ts=ts))
    (args.out / "count.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    if summary["fallbacks"]:
        print(f"warning: {summary['fallbacks']} of {summary['n']} calls were answered by the fallback "
              "(see 'answered_by'); their latency is under count_fallback_latency, not er2_count_latency.")
    if model != er2_model:
        print(f"note: role '{ROLE}' is set to {model}, not ER 2; er2_count_latency only has ER 2 answers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
