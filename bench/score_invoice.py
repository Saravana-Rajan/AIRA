"""Invoice benchmark: discrepancy catch rate, false alarms, clean-bill total accuracy, invoice-check latency.

Reads every labelled bill photo once with read_invoice (two independent Gemini reads, checked in code),
then scores the results against bench/data/invoice/labels.json (spec section 7).

Usage (from services/live):
    uv run python ../../bench/score_invoice.py --dry-run                     # offline: photos + labels only
    uv run python ../../bench/score_invoice.py --device "Pixel 7a"
        # real Gemini calls, needs GEMINI_API_KEY; Pillow (a service dependency) gives read B its enhanced copy
    uv run python ../../bench/score_invoice.py --predictions ../../bench/results/invoice.json
        # re-score saved results offline

Writes bench/results/invoice.json (metrics, predictions, latencies) and bench/results/invoice.csv
(benchmark CSV for scripts/bench_table.py). Both are git-ignored. Photo checklist: bench/data/invoice/README.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from importlib.util import find_spec
from pathlib import Path

BENCH = Path(__file__).resolve().parent
for _p in (BENCH, BENCH.parent / "services" / "live"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from common import frame_from_file, jpeg_size, load_labels, timed  # noqa: E402

from aira_live.vision.invoice_merge import TOLERANCE  # noqa: E402
from aira_live.vision.metrics import bench_csv_text, invoice_csv_rows, invoice_metrics, latency_stats  # noqa: E402

REQUIRED = {"file", "total", "has_error"}
MAX_BYTES = 1_000_000
TARGET_BILLS, TARGET_ERRORS = 30, 10
README = "bench/data/invoice/README.md"


def _lines_add_up(label: dict) -> bool:
    """True when the label has no lines, or its line totals add up to its total within the reader's ₹1
    tolerance. Decimal, not float: 110.10 + 220.20 must equal 330.30."""
    lines = label.get("lines") or []
    if not lines:
        return True
    try:
        line_sum = sum((Decimal(str(l["line_total"])) for l in lines), Decimal("0"))
        return abs(line_sum - Decimal(str(label["total"]))) <= TOLERANCE
    except (KeyError, TypeError, ArithmeticError):
        return False


def check_dataset(rows: list[dict], data_dir: Path) -> dict:
    """Offline checks before spending Gemini calls: photos exist and are JPEGs; labels look sane."""
    missing, not_jpeg, too_big, arith = [], [], [], []
    for r in rows:
        if not r["has_error"] and not _lines_add_up(r):
            arith.append(r["file"])
        p = data_dir / r["file"]
        if not p.is_file():
            missing.append(r["file"])
            continue
        data = p.read_bytes()
        if jpeg_size(data) is None:
            not_jpeg.append(r["file"])
        if len(data) > MAX_BYTES:
            too_big.append(r["file"])
    errors = sum(1 for r in rows if r["has_error"])
    return {"n": len(rows), "errors": errors, "clean": len(rows) - errors, "missing": missing,
            "not_jpeg": not_jpeg, "too_big": too_big, "clean_lines_dont_add_up": arith,
            "ready": bool(rows) and not missing and not not_jpeg}


def _print_dataset(rep: dict, labels_path: Path) -> None:
    print(f"labels : {labels_path} ({rep['n']} bills: {rep['errors']} with planted errors, {rep['clean']} clean;"
          f" target {TARGET_BILLS} with {TARGET_ERRORS} errors)")
    if rep["missing"]:
        print(f"missing files : {rep['missing']}")
    if rep["not_jpeg"]:
        print(f"not JPEG (re-save as .jpg, not HEIC/PNG): {rep['not_jpeg']}")
    if rep["too_big"]:
        print(f"warning: over 1 MB, please shrink: {rep['too_big']}")
    if rep["clean_lines_dont_add_up"]:
        print("warning: has_error=false but the labelled line totals do not add up to total "
              f"(fix the label, or mark the bill has_error=true): {rep['clean_lines_dont_add_up']}")
    if rep["n"] and not rep["errors"]:
        print("warning: no bill with a planted error, so the catch rate cannot be measured")


async def predict(rows: list[dict], data_dir: Path) -> tuple[dict, dict, dict]:
    """One read_invoice per photo, in order (sequential, so each latency is a real single-bill wait).
    A crash leaves the prediction missing, which scores as "not flagged", never as a catch."""
    from aira_live.vision import invoice as invoice_mod

    preds: dict[str, dict] = {}
    latencies: dict[str, float] = {}
    errors: dict[str, str] = {}
    for lab in rows:
        frame = frame_from_file(data_dir / lab["file"], "invoice")
        try:
            result, ms = await timed(invoice_mod.read_invoice([frame]))
        except Exception as e:  # read_invoice should never raise; if it does, record it and go on
            errors[lab["file"]] = repr(e)
            print(f"error  : {lab['file']}: {e!r}", file=sys.stderr)
            continue
        preds[lab["file"]] = result.model_dump()
        latencies[lab["file"]] = round(ms, 1)
    return preds, latencies, errors


def load_predictions(path: Path) -> tuple[dict, dict]:
    """Saved predictions: a {file: InvoiceRead dict} map, or a results file this script wrote.
    Raises ValueError for anything else, so a wrong file is never scored as "every bill missing"."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON ({e})") from None
    lat: object = {}
    if isinstance(doc, dict) and "metrics" in doc and "predictions" in doc:
        doc, lat = doc["predictions"], doc.get("latencies_ms") or {}
    if not isinstance(doc, dict) or not all(isinstance(p, dict) for p in doc.values()):
        raise ValueError(f"{path} must map each bill's file name to its saved InvoiceRead object")
    if not isinstance(lat, dict) or not all(
            isinstance(ms, (int, float)) and not isinstance(ms, bool) for ms in lat.values()):
        raise ValueError(f"{path}: latencies_ms must map file names to milliseconds")
    return doc, lat


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=BENCH / "data" / "invoice")
    ap.add_argument("--predictions", type=Path, help="re-score saved predictions; no Gemini calls")
    ap.add_argument("--out", type=Path, default=BENCH / "results" / "invoice.json")
    ap.add_argument("--csv", type=Path, default=BENCH / "results" / "invoice.csv")
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
        try:
            preds, lat = load_predictions(args.predictions)
        except (OSError, ValueError) as e:
            print(f"BAD PREDICTIONS: {e}")
            return 1
    else:
        rep = check_dataset(labels, args.data)
        if args.dry_run or not rep["ready"]:
            print("DRY RUN (no Gemini calls)" if args.dry_run else "Photo set check:")
            _print_dataset(rep, labels_path)
            if rep["ready"]:
                print(f"READY: {rep['n']} bills = {rep['n']} invoice checks (2 Gemini reads each).")
                return 0
            print("NOT READY: no labelled bills yet." if not rep["n"] else "NOT READY: fix the problems above.")
            return 1
        if not os.getenv("GEMINI_API_KEY"):
            print("NO KEY: set GEMINI_API_KEY for a live run, or pass --predictions to re-score saved results.")
            return 1
        if find_spec("PIL") is None:
            print("warning: Pillow is not installed, so read B runs on the original photo instead of an "
                  "enhanced copy. Run `uv sync` in services/live to install it.",
                  file=sys.stderr)
        preds, lat, errors = asyncio.run(predict(labels, args.data))

    latencies = [lat[lab["file"]] for lab in labels if lab["file"] in lat]
    metrics = {**invoice_metrics(labels, preds), **latency_stats(latencies)}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = invoice_csv_rows(labels, preds, latencies, device=args.device, build=args.build, ts=ts)

    print(json.dumps(metrics, indent=2))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"metrics": metrics, "predictions": preds, "latencies_ms": lat,
                                    "errors": errors}, indent=2, ensure_ascii=False), encoding="utf-8")
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.csv.write_text(bench_csv_text(rows), encoding="utf-8")
    if errors:
        print(f"warning: {len(errors)} bills raised an error and count as missing (not flagged)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
