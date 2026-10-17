"""Pure benchmark metrics for invoice and cash reading (the deck's Benchmarking slide).

Used by shared/bench/score_invoice.py and shared/bench/score_cash.py. ``*_metrics`` give the JSON summary; ``*_csv_rows``
give rows in the cross-team benchmark CSV format that shared/scripts/bench_table.py turns into the deck table.
A rate with nothing to measure (no bills with planted errors, no predictions at all) is left out of the CSV,
so the deck says "pending" instead of showing a made-up 0%.
"""
import csv
import io
import math

CSV_HEADER = ["metric", "value", "unit", "device", "build", "ts"]


def bench_csv_text(rows: list[dict]) -> str:
    """The rows as benchmark CSV text. The header is always written, so an empty run still loads cleanly."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_HEADER, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def _nearest_rank(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[max(1, math.ceil(q / 100 * len(s))) - 1]


def latency_stats(latencies_ms: list[float]) -> dict:
    """p50/p95 in ms (nearest rank, the same rule as shared/scripts/bench_table.py); {} when nothing was timed."""
    if not latencies_ms:
        return {}
    return {"latency_p50_ms": round(_nearest_rank(latencies_ms, 50), 1),
            "latency_p95_ms": round(_nearest_rank(latencies_ms, 95), 1)}


def invoice_metrics(labels: list[dict], preds: dict[str, dict]) -> dict:
    """catch_rate: share of bills with a planted error that AIRA flagged (agreed=False).
    false_alarm_rate: share of clean bills flagged. clean_total_accuracy: among clean bills AIRA agreed on,
    the share whose total is right. A missing prediction counts as "not flagged"."""
    missing = sum(1 for l in labels if l["file"] not in preds)

    def flagged(l: dict) -> bool:
        p = preds.get(l["file"])
        return p is not None and not p.get("agreed", False)

    errs = [l for l in labels if l.get("has_error")]
    clean = [l for l in labels if not l.get("has_error")]
    clean_agreed = [l for l in clean if l["file"] in preds and preds[l["file"]].get("agreed")]
    return {
        "n": len(labels),
        "missing": missing,
        "catch_rate": _rate(sum(flagged(l) for l in errs), len(errs)),
        "false_alarm_rate": _rate(sum(flagged(l) for l in clean), len(clean)),
        "clean_total_accuracy": _rate(sum(preds[l["file"]].get("total") == l["total"] for l in clean_agreed),
                                      len(clean_agreed)),
    }


def cash_metrics(labels: list[dict], preds: dict[str, dict]) -> dict:
    """total_accuracy: agreed and right. abstain_rate: agreed=False (AIRA asked to show the cash again).
    wrong_when_confident: agreed but the total is wrong (the dangerous case). All over n labels."""
    present = [l for l in labels if l["file"] in preds]
    right = sum(1 for l in present if preds[l["file"]].get("agreed") and preds[l["file"]].get("total") == l["total"])
    abstain = sum(1 for l in present if not preds[l["file"]].get("agreed"))
    wrong = sum(1 for l in present if preds[l["file"]].get("agreed") and preds[l["file"]].get("total") != l["total"])
    n = len(labels)
    return {
        "n": n,
        "missing": n - len(present),
        "total_accuracy": _rate(right, n),
        "abstain_rate": _rate(abstain, n),
        "wrong_when_confident": _rate(wrong, n),
    }


def _csv_row(metric: str, value: float, unit: str, device: str, build: str, ts: str) -> dict:
    return {"metric": metric, "value": value, "unit": unit, "device": device, "build": build, "ts": ts}


def _percent(rate: float) -> float:
    return round(rate * 100, 2)


def _latency_rows(metric: str, latencies_ms: list[float], device: str, build: str, ts: str) -> list[dict]:
    return [_csv_row(metric, round(ms, 1), "ms", device, build, ts) for ms in latencies_ms]


def invoice_csv_rows(labels: list[dict], preds: dict[str, dict], latencies_ms: list[float], *,
                     device: str, build: str, ts: str) -> list[dict]:
    """Benchmark CSV rows: catch / false-alarm / clean-total rates in %, one ``invoice_check_latency_ms`` row
    per bill read. A rate whose denominator is empty, or any rate when nothing was predicted, is left out."""
    m = invoice_metrics(labels, preds)
    rows: list[dict] = []
    if any(l["file"] in preds for l in labels):
        errs = [l for l in labels if l.get("has_error")]
        clean = [l for l in labels if not l.get("has_error")]
        clean_agreed = [l for l in clean if l["file"] in preds and preds[l["file"]].get("agreed")]
        for metric, key, den in (("invoice_catch_rate", "catch_rate", errs),
                                 ("invoice_false_alarm_rate", "false_alarm_rate", clean),
                                 ("invoice_clean_total_accuracy", "clean_total_accuracy", clean_agreed)):
            if den:
                rows.append(_csv_row(metric, _percent(m[key]), "%", device, build, ts))
    return rows + _latency_rows("invoice_check_latency_ms", latencies_ms, device, build, ts)


def cash_csv_rows(labels: list[dict], preds: dict[str, dict], latencies_ms: list[float], *,
                  device: str, build: str, ts: str) -> list[dict]:
    """Benchmark CSV rows: total accuracy, abstain and wrong-when-confident rates in %, one
    ``cash_read_latency_ms`` row per photo read. Rates are left out when nothing was predicted."""
    m = cash_metrics(labels, preds)
    rows: list[dict] = []
    if any(l["file"] in preds for l in labels):
        rows = [_csv_row(f"cash_{key}", _percent(m[key]), "%", device, build, ts)
                for key in ("total_accuracy", "abstain_rate", "wrong_when_confident")]
    return rows + _latency_rows("cash_read_latency_ms", latencies_ms, device, build, ts)
