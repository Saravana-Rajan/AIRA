"""Invoice and cash benchmark metrics, plus the shared/bench/score_invoice.py and shared/bench/score_cash.py CLIs.

Fully offline: the scorers re-score saved predictions, and the live path runs against a faked reader.
"""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

from aira_live.vision.cash_models import CashRead
from aira_live.vision.invoice_models import InvoiceLine, InvoiceRead
from aira_live.vision.metrics import (
    CSV_HEADER,
    bench_csv_text,
    cash_csv_rows,
    cash_metrics,
    invoice_csv_rows,
    invoice_metrics,
    latency_stats,
)

REPO = Path(__file__).resolve().parents[3]
BENCH = REPO / "shared" / "bench"
TS = "2026-10-15T12:00:00Z"

INV_LABELS = [
    {"file": "a.jpg", "total": 990, "has_error": False},
    {"file": "b.jpg", "total": 1470, "has_error": False},
    {"file": "c.jpg", "total": 1000, "has_error": True},
    {"file": "d.jpg", "total": 500, "has_error": True},
]

INV_PREDS = {
    "a.jpg": {"agreed": True, "total": 990, "disagreements": []},
    "b.jpg": {"agreed": False, "total": 1470, "disagreements": ["date: x vs y"]},   # false alarm
    "c.jpg": {"agreed": False, "total": 1000, "disagreements": ["arith: line 1"]},  # caught
    "d.jpg": {"agreed": True, "total": 500, "disagreements": []},                    # missed
}


def test_invoice_metrics():
    m = invoice_metrics(INV_LABELS, INV_PREDS)
    assert m == {"n": 4, "missing": 0, "catch_rate": 0.5, "false_alarm_rate": 0.5, "clean_total_accuracy": 1.0}


def test_invoice_metrics_missing_prediction_counts_as_not_flagged():
    m = invoice_metrics(INV_LABELS, {"a.jpg": {"agreed": True, "total": 990, "disagreements": []}})
    assert m["missing"] == 3
    assert m["catch_rate"] == 0.0


CASH_LABELS = [{"file": "x.jpg", "total": 1000}, {"file": "y.jpg", "total": 170},
               {"file": "z.jpg", "total": 600}, {"file": "w.jpg", "total": 50}]

CASH_PREDS = {
    "x.jpg": {"agreed": True, "total": 1000},   # right
    "y.jpg": {"agreed": True, "total": 160},    # wrong but confident
    "z.jpg": {"agreed": False, "total": None},  # abstained
    "w.jpg": {"agreed": True, "total": 50},     # right
}


def test_cash_metrics():
    m = cash_metrics(CASH_LABELS, CASH_PREDS)
    assert m == {"n": 4, "missing": 0, "total_accuracy": 0.5, "abstain_rate": 0.25, "wrong_when_confident": 0.25}


def test_metrics_empty_labels_are_zero_not_crash():
    assert invoice_metrics([], {}) == {"n": 0, "missing": 0, "catch_rate": 0.0,
                                       "false_alarm_rate": 0.0, "clean_total_accuracy": 0.0}
    assert cash_metrics([], {}) == {"n": 0, "missing": 0, "total_accuracy": 0.0,
                                    "abstain_rate": 0.0, "wrong_when_confident": 0.0}


def test_latency_stats_nearest_rank_ms():
    assert latency_stats([]) == {}
    assert latency_stats([300.0, 100.0, 200.0]) == {"latency_p50_ms": 200.0, "latency_p95_ms": 300.0}
    assert latency_stats([1234.56]) == {"latency_p50_ms": 1234.6, "latency_p95_ms": 1234.6}


# --- benchmark CSV rows (shared/scripts/bench_table.py format) ----------------------------------

def _row(metric, value, unit):
    return {"metric": metric, "value": value, "unit": unit, "device": "Pixel 7a", "build": "abc123", "ts": TS}


def test_invoice_csv_rows_rates_in_percent_and_one_latency_row_per_bill():
    rows = invoice_csv_rows(INV_LABELS, INV_PREDS, [1200.44, 900.0], device="Pixel 7a", build="abc123", ts=TS)
    assert rows == [
        _row("invoice_catch_rate", 50.0, "%"),
        _row("invoice_false_alarm_rate", 50.0, "%"),
        _row("invoice_clean_total_accuracy", 100.0, "%"),
        _row("invoice_check_latency_ms", 1200.4, "ms"),
        _row("invoice_check_latency_ms", 900.0, "ms"),
    ]
    assert all(list(r) == CSV_HEADER for r in rows)


def test_invoice_csv_rows_never_report_a_rate_without_data():
    # No bill with a planted error, and AIRA agreed on no clean bill: catch rate and total accuracy are unknown.
    clean_only = [{"file": "a.jpg", "total": 990, "has_error": False}]
    rows = invoice_csv_rows(clean_only, {"a.jpg": {"agreed": False, "total": None}}, [],
                            device="", build="", ts=TS)
    assert [r["metric"] for r in rows] == ["invoice_false_alarm_rate"]
    assert rows[0]["value"] == 100.0
    assert invoice_csv_rows([], {}, [], device="", build="", ts=TS) == []


def test_invoice_csv_rows_skip_rates_when_no_predictions_at_all():
    # A crashed or empty run must not show up in the deck as "0% caught".
    assert invoice_csv_rows(INV_LABELS, {}, [], device="", build="", ts=TS) == []


def test_cash_csv_rows():
    rows = cash_csv_rows(CASH_LABELS, CASH_PREDS, [800.0], device="Pixel 7a", build="abc123", ts=TS)
    assert rows == [
        _row("cash_total_accuracy", 50.0, "%"),
        _row("cash_abstain_rate", 25.0, "%"),
        _row("cash_wrong_when_confident", 25.0, "%"),
        _row("cash_read_latency_ms", 800.0, "ms"),
    ]
    assert cash_csv_rows([], {}, [], device="", build="", ts=TS) == []
    assert cash_csv_rows(CASH_LABELS, {}, [], device="", build="", ts=TS) == []


def test_bench_csv_text_always_has_the_header():
    assert bench_csv_text([]) == "metric,value,unit,device,build,ts\n"
    text = bench_csv_text([_row("cash_total_accuracy", 50.0, "%")])
    assert text.splitlines()[1] == f"cash_total_accuracy,50.0,%,Pixel 7a,abc123,{TS}"


def test_percent_values_are_rounded():
    labels = [{"file": f"{i}.jpg", "total": 10} for i in range(3)]
    preds = {"0.jpg": {"agreed": True, "total": 10}, "1.jpg": {"agreed": False, "total": None},
             "2.jpg": {"agreed": False, "total": None}}
    rows = cash_csv_rows(labels, preds, [], device="", build="", ts=TS)
    assert rows[0]["value"] == 33.33 and rows[1]["value"] == 66.67


# --- scorer CLIs -------------------------------------------------------------------------

def _load_scorer(name: str):
    spec = importlib.util.spec_from_file_location(f"_bench_{name}", BENCH / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_bench_table():
    spec = importlib.util.spec_from_file_location("_bench_table", REPO / "shared" / "scripts" / "bench_table.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tiny_jpeg(w: int = 640, h: int = 480) -> bytes:
    """Smallest bytes the bench JPEG header check accepts (SOI, APP0, SOF0, EOI). A test input, not a photo."""
    app0 = b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = (b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + h.to_bytes(2, "big")
            + w.to_bytes(2, "big") + b"\x03" + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def _example_labels(kind: str) -> list[dict]:
    return json.loads((BENCH / "data" / kind / "labels.example.json").read_text(encoding="utf-8"))


def _dataset(tmp_path: Path, kind: str, with_photos: bool = True) -> Path:
    data = tmp_path / kind
    data.mkdir()
    labels = _example_labels(kind)
    (data / "labels.json").write_text(json.dumps(labels), encoding="utf-8")
    if with_photos:
        for lab in labels:
            (data / lab["file"]).write_bytes(tiny_jpeg())
    return data


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def test_example_labels_have_the_documented_fields():
    for lab in _example_labels("invoice"):
        assert {"file", "supplier", "total", "has_error", "error_fields", "lines"} <= set(lab)
    for lab in _example_labels("cash"):
        assert {"file", "notes", "total", "overlapping"} <= set(lab)
        assert lab["total"] == sum(n["denomination"] * n["count"] for n in lab["notes"])


def test_score_invoice_rescores_saved_predictions(tmp_path, capsys):
    mod = _load_scorer("score_invoice")
    data = _dataset(tmp_path, "invoice", with_photos=False)
    preds = {"inv_001.jpg": {"agreed": True, "total": 990, "disagreements": []},
             "inv_021.jpg": {"agreed": False, "total": 1000, "disagreements": ["arith: line 1"]}}
    (tmp_path / "preds.json").write_text(json.dumps(preds))
    out, out_csv = tmp_path / "results" / "invoice.json", tmp_path / "results" / "invoice.csv"
    rc = mod.main(["--data", str(data), "--predictions", str(tmp_path / "preds.json"),
                   "--out", str(out), "--csv", str(out_csv), "--device", "Pixel 7a", "--build", "abc123"])
    assert rc == 0
    m = json.loads(capsys.readouterr().out)
    assert m == {"n": 2, "missing": 0, "catch_rate": 1.0, "false_alarm_rate": 0.0, "clean_total_accuracy": 1.0}
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["metrics"] == m and saved["predictions"] == preds

    header, rows = _read_csv(out_csv)
    assert header == ["metric", "value", "unit", "device", "build", "ts"]
    assert [(r["metric"], r["value"], r["unit"], r["device"], r["build"]) for r in rows] == [
        ("invoice_catch_rate", "100.0", "%", "Pixel 7a", "abc123"),
        ("invoice_false_alarm_rate", "0.0", "%", "Pixel 7a", "abc123"),
        ("invoice_clean_total_accuracy", "100.0", "%", "Pixel 7a", "abc123"),
    ]
    assert rows[0]["ts"].endswith("Z")


def test_score_invoice_rescores_its_own_results_file_with_latency(tmp_path, capsys):
    mod = _load_scorer("score_invoice")
    data = _dataset(tmp_path, "invoice", with_photos=False)
    prev = {"metrics": {}, "latencies_ms": {"inv_001.jpg": 1500.0, "inv_021.jpg": 2500.0},
            "predictions": {"inv_001.jpg": {"agreed": True, "total": 990, "disagreements": []},
                            "inv_021.jpg": {"agreed": True, "total": 1000, "disagreements": []}}}
    (tmp_path / "prev.json").write_text(json.dumps(prev))
    out_csv = tmp_path / "invoice.csv"
    rc = mod.main(["--data", str(data), "--predictions", str(tmp_path / "prev.json"),
                   "--out", str(tmp_path / "invoice.json"), "--csv", str(out_csv)])
    assert rc == 0
    m = json.loads(capsys.readouterr().out)
    assert m["catch_rate"] == 0.0
    assert m["latency_p50_ms"] == 1500.0 and m["latency_p95_ms"] == 2500.0  # nearest rank, as in bench_table
    _, rows = _read_csv(out_csv)
    assert [r["value"] for r in rows if r["metric"] == "invoice_check_latency_ms"] == ["1500.0", "2500.0"]


def test_score_cash_rescores_saved_predictions(tmp_path, capsys):
    mod = _load_scorer("score_cash")
    data = _dataset(tmp_path, "cash", with_photos=False)
    preds = {"cash_001.jpg": {"agreed": True, "total": 1000},
             "cash_002.jpg": {"agreed": False, "total": None}}
    (tmp_path / "preds.json").write_text(json.dumps(preds))
    out_csv = tmp_path / "cash.csv"
    rc = mod.main(["--data", str(data), "--predictions", str(tmp_path / "preds.json"),
                   "--out", str(tmp_path / "cash.json"), "--csv", str(out_csv)])
    assert rc == 0
    m = json.loads(capsys.readouterr().out)
    assert m == {"n": 2, "missing": 0, "total_accuracy": 0.5, "abstain_rate": 0.5, "wrong_when_confident": 0.0}
    _, rows = _read_csv(out_csv)
    assert {r["metric"]: r["value"] for r in rows} == {
        "cash_total_accuracy": "50.0", "cash_abstain_rate": "50.0", "cash_wrong_when_confident": "0.0"}


@pytest.mark.skipif(not (REPO / "shared" / "scripts" / "bench_table.py").exists(), reason="bench_table.py not merged yet")
def test_scorer_csv_feeds_the_deck_table(tmp_path, capsys):
    data = _dataset(tmp_path, "invoice", with_photos=False)
    prev = {"metrics": {}, "latencies_ms": {"inv_001.jpg": 1500.0, "inv_021.jpg": 2500.0},
            "predictions": {"inv_001.jpg": {"agreed": True, "total": 990, "disagreements": []},
                            "inv_021.jpg": {"agreed": False, "total": None, "disagreements": ["x"]}}}
    (tmp_path / "prev.json").write_text(json.dumps(prev))
    out_csv = tmp_path / "invoice.csv"
    assert _load_scorer("score_invoice").main(
        ["--data", str(data), "--predictions", str(tmp_path / "prev.json"),
         "--out", str(tmp_path / "invoice.json"), "--csv", str(out_csv), "--device", "Pixel 7a"]) == 0
    table = _load_bench_table()
    summary = {s["metric"]: s for s in table.summarize(table.load_rows([out_csv]))}
    assert summary["invoice_catch_rate"]["result"] == "100.0%"
    assert summary["invoice_check_latency"]["result"] == "p50 1500 ms · p95 2500 ms"
    assert summary["invoice_check_latency"]["device"] == "Pixel 7a"


@pytest.mark.parametrize("name", ["score_invoice", "score_cash"])
@pytest.mark.parametrize("bad", [
    "[1, 2]",                                   # a list, not a {file: prediction} map
    '{"inv_001.jpg": "agreed"}',                # a prediction that is not an object
    '{"metrics": {}, "predictions": [1]}',      # a results file whose predictions are not a map
    "{not json",                                # not JSON at all
])
def test_rescoring_a_malformed_predictions_file_fails_cleanly(tmp_path, capsys, name, bad):
    kind = "invoice" if name == "score_invoice" else "cash"
    data = _dataset(tmp_path, kind, with_photos=False)
    (tmp_path / "preds.json").write_text(bad, encoding="utf-8")
    out_csv = tmp_path / f"{kind}.csv"
    rc = _load_scorer(name).main(["--data", str(data), "--predictions", str(tmp_path / "preds.json"),
                                  "--out", str(tmp_path / f"{kind}.json"), "--csv", str(out_csv)])
    assert rc == 1
    assert "BAD PREDICTIONS" in capsys.readouterr().out
    assert not out_csv.exists()  # nothing half-scored reaches the deck table


def test_score_invoice_without_labels_is_not_ready(tmp_path, capsys):
    rc = _load_scorer("score_invoice").main(["--data", str(tmp_path)])
    assert rc == 1
    assert "NOT READY" in capsys.readouterr().out


def test_score_cash_rejects_labels_without_required_fields(tmp_path, capsys):
    (tmp_path / "labels.json").write_text(json.dumps([{"file": "cash_001.jpg"}]))
    rc = _load_scorer("score_cash").main(["--data", str(tmp_path), "--dry-run"])
    assert rc == 1
    assert "total" in capsys.readouterr().out


def test_dry_run_reports_missing_and_non_jpeg_photos(tmp_path, capsys):
    data = _dataset(tmp_path, "cash", with_photos=False)
    (data / "cash_001.jpg").write_bytes(b"\x89PNG\r\n\x1a\n not a jpeg")
    rc = _load_scorer("score_cash").main(["--data", str(data), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "cash_002.jpg" in out and "cash_001.jpg" in out and "NOT READY" in out


def test_dry_run_ready_makes_no_model_calls(tmp_path, capsys, monkeypatch):
    import aira_live.vision.invoice as invoice_mod

    async def boom(frames):  # pragma: no cover - must never run
        raise AssertionError("dry run must not read")

    monkeypatch.setattr(invoice_mod, "read_invoice", boom)
    data = _dataset(tmp_path, "invoice")
    rc = _load_scorer("score_invoice").main(["--data", str(data), "--dry-run"])
    assert rc == 0
    assert "READY" in capsys.readouterr().out


def test_dry_run_warns_about_a_clean_bill_whose_lines_do_not_add_up(tmp_path, capsys):
    data = tmp_path / "invoice"
    data.mkdir()
    labels = [{"file": "inv_001.jpg", "total": 1000, "has_error": False,
               "lines": [{"name": "Sakthi Curd 1L", "qty": 30, "unit": "L", "unit_price": 33, "line_total": 990}]},
              {"file": "inv_002.jpg", "total": 1000, "has_error": True,
               "lines": [{"name": "Sakthi Curd 1L", "qty": 30, "unit": "L", "unit_price": 33, "line_total": 990}]}]
    (data / "labels.json").write_text(json.dumps(labels))
    for lab in labels:
        (data / lab["file"]).write_bytes(tiny_jpeg())
    rc = _load_scorer("score_invoice").main(["--data", str(data), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0  # a warning, not a blocker: the bill itself may be wrong
    assert "do not add up" in out and "inv_001.jpg" in out and "inv_002.jpg" not in out.split("do not add up")[1]


def test_dry_run_blocks_a_cash_label_whose_total_is_not_the_sum_of_notes(tmp_path, capsys):
    data = tmp_path / "cash"
    data.mkdir()
    (data / "labels.json").write_text(json.dumps(
        [{"file": "cash_001.jpg", "notes": [{"denomination": 500, "count": 2}], "total": 1500}]))
    (data / "cash_001.jpg").write_bytes(tiny_jpeg())
    rc = _load_scorer("score_cash").main(["--data", str(data), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "not the sum of its notes" in out and "NOT READY" in out


def test_live_run_needs_an_api_key(tmp_path, capsys, monkeypatch):
    import aira_live.vision.cash as cash_mod

    async def boom(frames):  # pragma: no cover - must never run
        raise AssertionError("no key: must not read")

    monkeypatch.setattr(cash_mod, "read_cash", boom)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    data = _dataset(tmp_path, "cash")
    rc = _load_scorer("score_cash").main(["--data", str(data), "--out", str(tmp_path / "c.json"),
                                          "--csv", str(tmp_path / "c.csv")])
    assert rc == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().out
    assert not (tmp_path / "c.csv").exists()


def test_live_invoice_run_reads_each_photo_once(tmp_path, capsys, monkeypatch):
    import aira_live.vision.invoice as invoice_mod

    seen = []

    async def fake_read_invoice(frames):
        (frame,) = frames
        seen.append(frame)
        bad = frame.id == "inv_021"
        return InvoiceRead(supplier="Sakthi Dairy", date=None,
                           lines=[InvoiceLine(name="Sakthi Curd 1L", qty=30, unit="L", unit_price=33,
                                              line_total=1000 if bad else 990)],
                           total=1000 if bad else 990, agreed=not bad,
                           disagreements=["line 1: 30 x 33 = 990, not 1000"] if bad else [])

    monkeypatch.setattr(invoice_mod, "read_invoice", fake_read_invoice)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    data = _dataset(tmp_path, "invoice")
    out, out_csv = tmp_path / "invoice.json", tmp_path / "invoice.csv"
    rc = _load_scorer("score_invoice").main(["--data", str(data), "--out", str(out), "--csv", str(out_csv)])
    assert rc == 0
    assert [f.purpose for f in seen] == ["invoice", "invoice"]
    assert (seen[0].w, seen[0].h) == (640, 480)
    assert seen[0].ts > 1e12  # Frame.ts is epoch milliseconds
    m = json.loads(capsys.readouterr().out)
    assert m["catch_rate"] == 1.0 and m["false_alarm_rate"] == 0.0
    assert {"latency_p50_ms", "latency_p95_ms"} <= set(m)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["predictions"]["inv_021.jpg"]["agreed"] is False
    assert set(saved["latencies_ms"]) == {"inv_001.jpg", "inv_021.jpg"}
    _, rows = _read_csv(out_csv)
    assert sum(r["metric"] == "invoice_check_latency_ms" for r in rows) == 2


def test_live_cash_run_uses_cash_frames(tmp_path, capsys, monkeypatch):
    import aira_live.vision.cash as cash_mod

    async def fake_read_cash(frames):
        assert frames[0].purpose == "cash"
        if frames[0].id == "cash_001":
            return CashRead(notes=[{"denomination": 500, "count": 2}], total=1000, agreed=True, confidence=0.9)
        return CashRead(notes=[], total=None, agreed=False, confidence=0.0, hint_key="cash.spread_notes")

    monkeypatch.setattr(cash_mod, "read_cash", fake_read_cash)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    data = _dataset(tmp_path, "cash")
    rc = _load_scorer("score_cash").main(["--data", str(data), "--out", str(tmp_path / "cash.json"),
                                          "--csv", str(tmp_path / "cash.csv")])
    assert rc == 0
    m = json.loads(capsys.readouterr().out)
    assert (m["total_accuracy"], m["abstain_rate"], m["wrong_when_confident"]) == (0.5, 0.5, 0.0)
    saved = json.loads((tmp_path / "cash.json").read_text(encoding="utf-8"))
    assert saved["predictions"]["cash_002.jpg"]["hint_key"] == "cash.spread_notes"


def test_scorers_never_mention_counterfeit_detection():
    for name in ("score_invoice.py", "score_cash.py"):
        text = (BENCH / name).read_text(encoding="utf-8").lower()
        assert "counterfeit" not in text and "fake note" not in text


def _bill(lines, total):
    return {"file": "x.jpg", "total": total, "has_error": False,
            "lines": [{"name": f"item {i}", "line_total": lt} for i, lt in enumerate(lines)]}


def test_label_with_paise_lines_adds_up():
    mod = _load_scorer("score_invoice")
    assert sum([110.1, 220.2]) != 330.3             # the float trap the check must not fall into
    assert mod._lines_add_up(_bill([110.1, 220.2], 330.3)) is True
    assert mod._lines_add_up(_bill([22.5, 44.35, 10.1], 76.95)) is True
    assert mod._lines_add_up(_bill(["110.10", "220.20"], "330.30")) is True


def test_label_lines_use_the_same_one_rupee_tolerance_as_the_reader():
    mod = _load_scorer("score_invoice")
    assert mod._lines_add_up(_bill([990, 480], 1470.5)) is True    # the bill rounded its total
    assert mod._lines_add_up(_bill([990, 480], 1471)) is True
    assert mod._lines_add_up(_bill([990, 480], 1475)) is False
    assert mod._lines_add_up(_bill([990, "abc"], 1470)) is False
    assert mod._lines_add_up({"file": "x.jpg", "total": 990, "has_error": False}) is True


def test_dataset_check_does_not_flag_a_clean_paise_bill(tmp_path):
    mod = _load_scorer("score_invoice")
    rep = mod.check_dataset([_bill([10.1, 20.2], 30.3)], tmp_path)
    assert rep["clean_lines_dont_add_up"] == []
