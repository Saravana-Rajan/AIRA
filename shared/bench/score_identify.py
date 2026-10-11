"""Identification accuracy on shared/bench/data/identify (numbers for the deck's benchmark slide).

Usage (from backend):
    uv run python ../shared/bench/score_identify.py --dry-run          # offline: labels + photos only
    uv run python ../shared/bench/score_identify.py --device "Pixel 7a"  # real Gemini calls; needs GEMINI_API_KEY

Photos: set B in shared/bench/data/identify/README.md: held-pack shots i01.jpg ... (not the spike photos),
every SKU in candidates.json at least twice, confusion pairs included.

Writes, only after a run in which every call returned:
    shared/bench/results/identify.csv       deck row (metric,value,unit,device,build,ts): product_id_accuracy
    shared/bench/results/identify.json      summary: n, accuracy, abstain, wrong_confident, pairs, errors,
                                     fallbacks, p50_ms, p95_ms
    shared/bench/results/rows/identify.csv  one row per photo (always written after a live run, for debugging)

Abstentions (sku=None) are AIRA saying "show me the label"; they are reported, never guessed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    answering_models, candidate_skus, frame_from_file, load_candidates, load_labels, percentile, timed,
    utc_now_iso, write_csv, write_metrics_csv,
)
from spike_er2 import check_dataset  # noqa: E402  same photo checks as the spike

BENCH = Path(__file__).resolve().parent
DATA = BENCH / "data" / "identify"
RESULTS = BENCH / "results"
REQUIRED = {"file", "sku"}
ROLE = "point"
MIN_PER_SKU = 2  # plan addendum: the golden set covers every SKU at least twice


def _rate(k: int, n: int) -> float | None:
    return round(k / n, 3) if n else None


def score_identify(rows: list[dict]) -> dict:
    """rows: {"truth": sku, "pred": sku | None, "error"?: str}. pred None = AIRA asked for the label.

    A failed call (``error``) is counted in ``n`` and ``errors``; it is neither an abstention nor wrong."""
    n = len(rows)
    ok = [r for r in rows if not r.get("error")]
    correct = sum(1 for r in ok if r["pred"] == r["truth"])
    abstain = sum(1 for r in ok if r["pred"] is None)
    wrong = [r for r in ok if r["pred"] is not None and r["pred"] != r["truth"]]
    pairs = Counter(f'{r["truth"]}->{r["pred"]}' for r in wrong)
    return {"n": n, "accuracy": _rate(correct, n), "abstain": _rate(abstain, n),
            "wrong_confident": _rate(len(wrong), n), "pairs": dict(pairs), "errors": n - len(ok)}


def metric_rows(rows: list[dict], *, device: str, build: str, ts: str) -> list[dict]:
    """Deck row for shared/scripts/bench_table.py. Nothing for an empty set: numbers are never invented."""
    n = len(rows)
    if not n:
        return []
    correct = sum(1 for r in rows if not r.get("error") and r["pred"] == r["truth"])
    return [{"metric": "product_id_accuracy", "value": round(100 * correct / n, 1), "unit": "%",
             "device": device, "build": build, "ts": ts}]


def _models() -> tuple[str, str | None]:
    from aira_live.models import fallback_for, model_for

    return model_for(ROLE), fallback_for(ROLE)


def _print_dataset(rep: dict, labels_path: Path) -> None:
    print(f"labels : {labels_path} ({rep['n']} photos)")
    print("photos per SKU: " + ", ".join(f"{s}={c}" for s, c in rep["coverage"].items()))
    if rep["missing"]:
        print(f"missing files : {rep['missing']}")
    if rep["not_jpeg"]:
        print(f"not JPEG (re-save as .jpg, not HEIC/PNG): {rep['not_jpeg']}")
    if rep["unknown_sku"]:
        print(f"SKUs not in candidates.json: {rep['unknown_sku']}")
    if rep["thin"]:
        print(f"fewer than {MIN_PER_SKU} photos for: {rep['thin']}")


async def run(labels: list[dict], data_dir: Path) -> list[dict]:
    """Identify every labelled photo, one call at a time (so latency is not skewed by concurrency)."""
    from aira_live.vision import identify

    cands = load_candidates()
    rows = []
    with answering_models(identify) as seen:
        for r in labels:
            frame = frame_from_file(Path(data_dir) / r["file"], "identify")
            n_seen = len(seen)
            try:
                res, ms = await timed(identify.identify_product(frame, cands))
                pred, conf, alts, err = res.sku, res.confidence, "|".join(res.alternatives), ""
            except Exception as e:  # record the failure; never turn it into a SKU
                pred, conf, alts, ms, err = None, 0.0, "", float("nan"), repr(e)
            rows.append({"file": r["file"], "truth": r["sku"], "pred": pred, "confidence": conf,
                         "alternatives": alts, "answered_by": seen[-1] if len(seen) > n_seen else "",
                         "ms": round(ms, 1), "error": err})
    return rows


def summarize(rows: list[dict], model: str) -> dict:
    ms = [r["ms"] for r in rows if not r.get("error") and math.isfinite(r["ms"])]
    return {
        **score_identify(rows),
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
        print(f"NOT READY: {labels_path} not found. Follow shared/bench/data/identify/README.md (set B).")
        return 1
    labels = load_labels(labels_path, REQUIRED)
    rep = check_dataset(labels, args.data, candidate_skus(), min_per_sku=MIN_PER_SKU)
    ready = rep["ready"] and not rep["thin"]
    if args.dry_run:
        print("DRY RUN (no Gemini calls)")
        _print_dataset(rep, labels_path)
        try:
            model, fallback = _models()
            print(f"models : {model} (fallback {fallback})")
        except Exception as e:  # backend not installed: the data check still counts
            print(f"models : unavailable ({e!r})")
    if not ready:
        if not args.dry_run:
            _print_dataset(rep, labels_path)
        print("NOT READY: " + ("no labelled photos yet. Follow shared/bench/data/identify/README.md (set B)."
                               if not rep["n"] else "fix the problems above."))
        return 1
    if args.dry_run:
        print(f"READY: {rep['n']} photos = {rep['n']} identify calls.")
        return 0

    model, _ = _models()
    rows = asyncio.run(run(labels, args.data))
    write_csv(args.out / "rows" / "identify.csv", rows)
    summary = summarize(rows, model)
    if summary["errors"]:
        print(json.dumps(summary))
        print(f"NO RESULT: {summary['errors']} of {summary['n']} calls failed (see the 'error' column in "
              f"{args.out / 'rows' / 'identify.csv'}). Check GEMINI_API_KEY and run again; "
              "the deck files were not written.")
        return 1
    ts = utc_now_iso()
    summary.update({"model": model, "device": args.device, "build": args.build, "ts": ts})
    write_metrics_csv(args.out / "identify.csv",
                      metric_rows(rows, device=args.device, build=args.build, ts=ts))
    (args.out / "identify.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    if summary["fallbacks"]:
        print(f"warning: {summary['fallbacks']} of {summary['n']} calls were answered by the fallback "
              "(see 'answered_by').")
    return 0


if __name__ == "__main__":
    sys.exit(main())
