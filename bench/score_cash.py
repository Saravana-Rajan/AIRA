"""Cash benchmark: cash-total accuracy, abstain rate, wrong-when-confident rate and read latency.

Reads every labelled cash photo once with read_cash (two independent Gemini reads, totals computed in
code), then scores the results against bench/data/cash/labels.json (spec section 7). AIRA reads
denominations and adds them up; it makes no claim about whether a note is genuine.

Usage (from services/live):
    uv run python ../../bench/score_cash.py --dry-run                        # offline: photos + labels only
    uv run python ../../bench/score_cash.py --device "Pixel 7a"              # real Gemini calls, needs GEMINI_API_KEY
    uv run python ../../bench/score_cash.py --predictions ../../bench/results/cash.json   # re-score offline

Writes bench/results/cash.json (metrics, predictions, latencies) and bench/results/cash.csv
(benchmark CSV for scripts/bench_table.py). Both are git-ignored. Photo checklist: bench/data/cash/README.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BENCH = Path(__file__).resolve().parent
for _p in (BENCH, BENCH.parent / "services" / "live"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from common import frame_from_file, jpeg_size, load_labels, timed  # noqa: E402

from aira_live.vision.metrics import bench_csv_text, cash_csv_rows, cash_metrics, latency_stats  # noqa: E402

REQUIRED = {"file", "total"}
MAX_BYTES = 1_000_000
TARGET_PHOTOS, TARGET_OVERLAPPING = 40, 8
README = "bench/data/cash/README.md"


def _notes_add_up(label: dict) -> bool:
    """True when the label lists no notes, or its notes add up to its total."""
    notes = label.get("notes") or []
    if not notes:
        return True
    try:
        return sum(int(n["denomination"]) * int(n["count"]) for n in notes) == int(label["total"])
    except (KeyError, TypeError, ValueError):
        return False


def check_dataset(rows: list[dict], data_dir: Path) -> dict:
    """Offline checks before spending Gemini calls: photos exist and are JPEGs; label totals add up."""
    missing, not_jpeg, too_big, bad_total = [], [], [], []
    for r in rows:
        if not _notes_add_up(r):
            bad_total.append(r["file"])
        p = data_dir / r["file"]
        if not p.is_file():
            missing.append(r["file"])
            continue
        data = p.read_bytes()
        if jpeg_size(data) is None:
            not_jpeg.append(r["file"])
        if len(data) > MAX_BYTES:
            too_big.append(r["file"])
    return {"n": len(rows), "overlapping": sum(1 for r in rows if r.get("overlapping")), "missing": missing,
            "not_jpeg": not_jpeg, "too_big": too_big, "total_not_sum_of_notes": bad_total,
            "ready": bool(rows) and not missing and not not_jpeg and not bad_total}


def _print_dataset(rep: dict, labels_path: Path) -> None:
    print(f"labels : {labels_path} ({rep['n']} photos, {rep['overlapping']} with overlapping notes;"
          f" target {TARGET_PHOTOS} with {TARGET_OVERLAPPING} overlapping)")
    if rep["missing"]:
        print(f"missing files : {rep['missing']}")
    if rep["not_jpeg"]:
        print(f"not JPEG (re-save as .jpg, not HEIC/PNG): {rep['not_jpeg']}")
    if rep["total_not_sum_of_notes"]:
        print(f"label total is not the sum of its notes: {rep['total_not_sum_of_notes']}")
    if rep["too_big"]:
        print(f"warning: over 1 MB, please shrink: {rep['too_big']}")


async def predict(rows: list[dict], data_dir: Path) -> tuple[dict, dict, dict]:
    """One read_cash per photo, in order (sequential, so each latency is a real single-read wait).
    A crash leaves the prediction missing, which never scores as right."""
    from aira_live.vision import cash as cash_mod

    preds: dict[str, dict] = {}
    latencies: dict[str, float] = {}
    errors: dict[str, str] = {}
    for lab in rows:
        frame = frame_from_file(data_dir / lab["file"], "cash")
        try:
            result, ms = await timed(cash_mod.read_cash([frame]))
        except Exception as e:  # read_cash should never raise; if it does, record it and go on
            errors[lab["file"]] = repr(e)
            print(f"error  : {lab['file']}: {e!r}", file=sys.stderr)
            continue
        preds[lab["file"]] = result.model_dump()
        latencies[lab["file"]] = round(ms, 1)
    return preds, latencies, errors


def load_predictions(path: Path) -> tuple[dict, dict]:
    """Saved predictions: a {file: CashRead dict} map, or a results file this script wrote."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and isinstance(doc.get("predictions"), dict) and "metrics" in doc:
        return doc["predictions"], doc.get("latencies_ms") or {}
    return doc, {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=BENCH / "data" / "cash")
    ap.add_argument("--predictions", type=Path, help="re-score saved predictions; no Gemini calls")
    ap.add_argument("--out", type=Path, default=BENCH / "results" / "cash.json")
    ap.add_argument("--csv", type=Path, default=BENCH / "results" / "cash.csv")
    ap.add_argument("--device", default="", help="phone that took the photos, e.g. 'Pixel 7a'")
    ap.add_argument("--build", default="", help="git sha or version of the code under test")
    ap.add_argument("--dry-run", action="store_true", help="check photos and labels; no Gemini calls")
    args = ap.parse_args(argv)

    labels_path = args.data / "labels.json"
    if not labels_path.is_file():
        print(f"NOT READY: {labels_path} not found. Follow {README}.")
        return 1
    try:
        labels = load_labels(labels_path, REQUIRED)
    except ValueError as e:
        print(f"NOT READY: {e}")
        return 1

    errors: dict[str, str] = {}
    if args.predictions:
        preds, lat = load_predictions(args.predictions)
    else:
        rep = check_dataset(labels, args.data)
        if args.dry_run or not rep["ready"]:
            print("DRY RUN (no Gemini calls)" if args.dry_run else "Photo set check:")
            _print_dataset(rep, labels_path)
            if rep["ready"]:
                print(f"READY: {rep['n']} photos = {rep['n']} cash reads (2 Gemini reads each).")
                return 0
            print("NOT READY: no labelled photos yet." if not rep["n"] else "NOT READY: fix the problems above.")
            return 1
        if not os.getenv("GEMINI_API_KEY"):
            print("NO KEY: set GEMINI_API_KEY for a live run, or pass --predictions to re-score saved results.")
            return 1
        preds, lat, errors = asyncio.run(predict(labels, args.data))

    latencies = [lat[lab["file"]] for lab in labels if lab["file"] in lat]
    metrics = {**cash_metrics(labels, preds), **latency_stats(latencies)}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = cash_csv_rows(labels, preds, latencies, device=args.device, build=args.build, ts=ts)

    print(json.dumps(metrics, indent=2))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"metrics": metrics, "predictions": preds, "latencies_ms": lat,
                                    "errors": errors}, indent=2, ensure_ascii=False), encoding="utf-8")
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.csv.write_text(bench_csv_text(rows), encoding="utf-8")
    if errors:
        print(f"warning: {len(errors)} photos raised an error and count as missing (not right)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
