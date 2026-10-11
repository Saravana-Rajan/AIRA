"""D0 spike: is Gemini Robotics-ER 2 fast and accurate enough for identify + point?

Runs identify_product on every labelled pack photo twice: once with the
configured 'point' model (ER 2) and once forced to its Flash fallback. Then it
prints p50/p95 latency, accuracy and abstain rate for each pass and a
KEEP / SWITCH decision for the 'point' role. Counting stays on ER 2.

Usage (from backend, real Gemini calls, needs GEMINI_API_KEY):
    uv run python ../shared/bench/spike_er2.py
    uv run python ../shared/bench/spike_er2.py --data ../shared/bench/data/packs --out ../shared/bench/results/spike_er2.csv
Check the photo set without calling Gemini:
    uv run python ../shared/bench/spike_er2.py --dry-run

Photo checklist: shared/bench/data/identify/README.md.
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
    answering_models, candidate_skus, frame_from_file, jpeg_size, load_candidates, load_labels,
    percentile, timed, write_csv,
)

BENCH = Path(__file__).resolve().parent
ROLE = "point"
ENV_OVERRIDE = "AIRA_MODEL_POINT"

# Decision rule. Sam waits for the answer while holding the pack, so latency
# comes first: if ER 2's median identify call is slower than 3 s (and Flash is
# faster), switch. Even when ER 2 is fast enough, switch if Flash is clearly more
# accurate (10+ points). Never switch to a Flash that failed every call (_live).
THRESHOLD_MS = 3000
ACCURACY_MARGIN = 0.10
MIN_PER_SKU = 2


def decide(er2_p50_ms: float, er2_accuracy: float | None = None,
           flash_accuracy: float | None = None, flash_p50_ms: float | None = None) -> str:
    """'KEEP' ER 2 for the point role, or 'SWITCH' it to the Flash fallback.

    When `flash_p50_ms` is given, latency alone justifies a switch only if Flash is faster."""
    if er2_p50_ms > THRESHOLD_MS and (flash_p50_ms is None or flash_p50_ms < er2_p50_ms):
        return "SWITCH"
    if (er2_accuracy is not None and flash_accuracy is not None
            and round(flash_accuracy - er2_accuracy, 3) >= ACCURACY_MARGIN):
        return "SWITCH"
    return "KEEP"


def check_dataset(rows: list[dict], data_dir: Path, skus: list[str],
                  min_per_sku: int = MIN_PER_SKU) -> dict:
    """Offline checks before spending Gemini calls: files exist, are JPEGs, SKUs are known,
    and every SKU has at least `min_per_sku` photos (a warning, not a blocker)."""
    data_dir = Path(data_dir)
    known = set(skus)
    missing, not_jpeg, unknown = [], [], []
    for r in rows:
        p = data_dir / r["file"]
        if not p.is_file():
            missing.append(r["file"])
        elif jpeg_size(p.read_bytes()) is None:
            not_jpeg.append(r["file"])
        if r["sku"] not in known and r["sku"] not in unknown:
            unknown.append(r["sku"])
    counts = Counter(r["sku"] for r in rows)
    coverage = {s: counts.get(s, 0) for s in skus}
    return {
        "n": len(rows),
        "missing": missing,
        "not_jpeg": not_jpeg,
        "unknown_sku": unknown,
        "coverage": coverage,
        "thin": [s for s, c in coverage.items() if c < min_per_sku],
        "ready": bool(rows) and not (missing or not_jpeg or unknown),
    }


def _models() -> tuple[str, str | None]:
    from aira_live.models import fallback_for, model_for

    return model_for(ROLE), fallback_for(ROLE)


async def run_pass(label: str, override: str | None, rows: list[dict], data_dir: Path) -> list[dict]:
    """One identify pass over every photo. `override` forces AIRA_MODEL_POINT for the pass;
    the caller's environment is restored afterwards."""
    from aira_live.models import model_for
    from aira_live.vision import identify

    before = os.environ.get(ENV_OVERRIDE)
    if override:
        os.environ[ENV_OVERRIDE] = override
    else:
        os.environ.pop(ENV_OVERRIDE, None)
    try:
        model = model_for(ROLE)
        cands = load_candidates()
        out = []
        # A timed-out ER 2 call that Flash answered inside generate_json must not be scored as ER 2.
        with answering_models(identify) as seen:
            for r in rows:
                frame = frame_from_file(Path(data_dir) / r["file"], "identify")
                n_seen = len(seen)
                try:
                    res, ms = await timed(identify.identify_product(frame, cands))
                    pred, conf, err = res.sku, res.confidence, ""
                except Exception as e:  # record failures instead of stopping the spike
                    pred, conf, ms, err = None, 0.0, float("nan"), repr(e)
                answered_by = seen[-1] if len(seen) > n_seen else ""  # "" = unknown / no reply
                out.append({"pass": label, "model": model, "answered_by": answered_by, "file": r["file"],
                            "truth": r["sku"], "pred": pred, "confidence": conf, "ms": round(ms, 1),
                            "error": err})
        return out
    finally:
        if before is None:
            os.environ.pop(ENV_OVERRIDE, None)
        else:
            os.environ[ENV_OVERRIDE] = before


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r["error"]]
    ms = [r["ms"] for r in ok if not math.isnan(r["ms"])]
    n = len(rows)

    def rate(k: int) -> float | None:
        return round(k / n, 3) if n else None

    return {
        "n": n,
        "p50_ms": round(percentile(ms, 50)) if ms else None,
        "p95_ms": round(percentile(ms, 95)) if ms else None,
        "accuracy": rate(sum(1 for r in ok if r["pred"] == r["truth"])),
        "abstain": rate(sum(1 for r in ok if r["pred"] is None)),
        "wrong": rate(sum(1 for r in ok if r["pred"] is not None and r["pred"] != r["truth"])),
        "errors": n - len(ok),
        # Calls the requested model did not answer (generate_json fell back). Unknown ("") is not counted.
        "fallbacks": sum(1 for r in ok if r.get("answered_by") and r["answered_by"] != r["model"]),
    }


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
        print(f"warning: fewer than {MIN_PER_SKU} photos for {rep['thin']}")


def _dry_run(rows: list[dict], data_dir: Path, labels_path: Path) -> int:
    print("DRY RUN (no Gemini calls)")
    rep = check_dataset(rows, data_dir, candidate_skus())
    _print_dataset(rep, labels_path)
    try:
        er2, flash = _models()
        print(f"models : er2 pass -> {er2} | flash pass -> {flash}")
    except Exception as e:  # backend not installed yet: the data check still counts
        print(f"models : unavailable ({e!r})")
    if rep["ready"]:
        print(f"READY: {rep['n']} photos x 2 passes = {2 * rep['n']} identify calls.")
        return 0
    if not rep["n"]:
        print("NOT READY: no labelled photos yet. Follow shared/bench/data/identify/README.md.")
    else:
        print("NOT READY: fix the problems above.")
    return 1


async def _live(rows: list[dict], data_dir: Path, out: Path) -> int:
    _, flash_model = _models()
    if not flash_model:
        print(f"NO DECISION: role '{ROLE}' has no Flash fallback configured.")
        return 1
    er2 = await run_pass("er2", None, rows, data_dir)
    flash = await run_pass("flash", flash_model, rows, data_dir)
    s_er2, s_flash = summarize(er2), summarize(flash)
    flash_failed = s_flash["p50_ms"] is None  # no Flash call returned
    decision = None
    if s_er2["p50_ms"] is not None:
        decision = decide(s_er2["p50_ms"], s_er2["accuracy"], s_flash["accuracy"], s_flash["p50_ms"])
        if decision == "SWITCH" and flash_failed:
            decision = None  # never point production at a model that did not answer once
    write_csv(out, er2 + flash)
    out.with_suffix(".json").write_text(json.dumps(
        {"er2": {"model": er2[0]["model"] if er2 else None, **s_er2},
         "flash": {"model": flash_model, **s_flash},
         "threshold_ms": THRESHOLD_MS, "accuracy_margin": ACCURACY_MARGIN,
         "decision": decision}, indent=2), encoding="utf-8")
    print(f"ER 2  : {s_er2}")
    print(f"Flash : {s_flash}")
    print(f"rows  : {out}")
    if s_er2["fallbacks"]:
        print(f"warning: {s_er2['fallbacks']} of {s_er2['n']} ER 2 calls were answered by the fallback "
              "(see the 'answered_by' column); the ER 2 numbers include those answers and their wait.")
    if flash_failed:
        print(f"warning: every Flash call failed ({flash_model}); the fallback for role '{ROLE}' "
              "is not working. Check the 'error' column.")
    if decision is None:
        if s_er2["p50_ms"] is None:
            print("NO DECISION: every ER 2 call failed. Check GEMINI_API_KEY and the 'error' column.")
        else:
            print("NO DECISION: ER 2 is too slow, but every Flash call failed, so there is nothing "
                  "to switch to. Fix the Flash errors and run the spike again.")
        return 1
    if decision == "KEEP" and s_er2["p50_ms"] > THRESHOLD_MS:
        print(f"note: ER 2 p50 is over {THRESHOLD_MS} ms, but Flash is slower ({s_flash['p50_ms']} ms).")
    if decision == "KEEP":
        print(f"DECISION: KEEP ER 2 for identify + point (role '{ROLE}').")
    else:
        print(f"DECISION: SWITCH identify + point to {flash_model}: set pointing_model={flash_model} "
              "in the Firestore doc config/runtime (database 'aira'), e.g. "
              "`uv run python -m aira_live.remote_config set pointing_model fallback`, "
              f"or env {ENV_OVERRIDE} on aira-live. Counting stays on ER 2 (role 'count').")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=BENCH / "data" / "packs")
    ap.add_argument("--out", type=Path, default=BENCH / "results" / "spike_er2.csv")
    ap.add_argument("--dry-run", action="store_true", help="check photos and labels; no Gemini calls")
    args = ap.parse_args(argv)

    labels_path = args.data / "labels.json"
    if not labels_path.is_file():
        print(f"NOT READY: {labels_path} not found. Follow shared/bench/data/identify/README.md.")
        return 1
    rows = load_labels(labels_path, {"file", "sku"})
    if args.dry_run:
        return _dry_run(rows, args.data, labels_path)
    rep = check_dataset(rows, args.data, candidate_skus())
    if not rep["ready"]:
        _print_dataset(rep, labels_path)
        print("NOT READY: run with --dry-run and fix the photo set first.")
        return 1
    return asyncio.run(_live(rows, args.data, args.out))


if __name__ == "__main__":
    sys.exit(main())
