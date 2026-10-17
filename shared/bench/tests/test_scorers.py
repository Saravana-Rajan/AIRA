"""Scorers for counting and identification (plan sarmitha/01 Task 6). Every test is offline."""
import csv
import importlib.util
import json
import math

import aira_live.models as models_mod
import aira_live.vision.count as count_mod
import aira_live.vision.identify as identify_mod
import common
import pytest
import score_count
import score_identify
from score_count import score_counts
from score_identify import score_identify as score_id

BENCH = common.BENCH
BENCH_TABLE = BENCH.parent / "scripts" / "bench_table.py"
ER2 = models_mod.DEFAULTS["count"]
FLASH = models_mod.fallback_for("count")


# ---- pure scoring -----------------------------------------------------------------

def test_score_counts():
    rows = [
        {"truth": 12, "pred": 12},
        {"truth": 10, "pred": 9},
        {"truth": 8, "pred": None},   # abstained (needs retake)
        {"truth": 20, "pred": 17},
    ]
    s = score_counts(rows)
    assert s["n"] == 4
    assert s["exact"] == 0.25
    assert s["within1"] == 0.5
    assert s["abstain"] == 0.25
    assert s["mae"] == pytest.approx((0 + 1 + 3) / 3, abs=1e-3)
    assert s["errors"] == 0


def test_score_counts_all_abstain_has_no_mae():
    s = score_counts([{"truth": 5, "pred": None}, {"truth": 7, "pred": None}])
    assert s == {"n": 2, "exact": 0.0, "within1": 0.0, "mae": None, "abstain": 1.0, "errors": 0}


def test_score_counts_empty_never_invents_numbers():
    assert score_counts([]) == {"n": 0, "exact": None, "within1": None, "mae": None, "abstain": None,
                                "errors": 0}


def test_score_counts_failed_calls_are_not_abstentions():
    """A call that failed (VisionError, network) is not AIRA choosing to say "show me again"."""
    s = score_counts([{"truth": 5, "pred": 5}, {"truth": 7, "pred": None, "error": "VisionError()"}])
    assert s["n"] == 2
    assert s["exact"] == 0.5
    assert s["abstain"] == 0.0
    assert s["errors"] == 1


def test_score_identify():
    rows = [
        {"truth": "aavin_milk_1l", "pred": "aavin_milk_1l"},
        {"truth": "aavin_milk_1l", "pred": None},
        {"truth": "sakthi_curd_1l", "pred": "sakthi_curd_500ml"},
        {"truth": "arun_icecream_box", "pred": "arun_icecream_box"},
    ]
    s = score_id(rows)
    assert s["n"] == 4
    assert s["accuracy"] == 0.5
    assert s["abstain"] == 0.25
    assert s["wrong_confident"] == 0.25
    assert s["pairs"] == {"sakthi_curd_1l->sakthi_curd_500ml": 1}
    assert s["errors"] == 0


def test_score_identify_empty_never_invents_numbers():
    assert score_id([]) == {"n": 0, "accuracy": None, "abstain": None, "wrong_confident": None,
                            "pairs": {}, "errors": 0}


def test_score_identify_failed_calls_are_not_abstentions():
    s = score_id([{"truth": "aavin_milk_1l", "pred": "aavin_milk_1l"},
                  {"truth": "aavin_milk_1l", "pred": None, "error": "VisionError()"}])
    assert (s["accuracy"], s["abstain"], s["wrong_confident"], s["errors"]) == (0.5, 0.0, 0.0, 1)


# ---- shipped label files ---------------------------------------------------------

@pytest.mark.parametrize("name,required", [("count", {"file", "label", "count"}),
                                           ("identify", {"file", "sku"})])
def test_label_files_are_valid_and_not_fabricated(name, required):
    """No fabricated data: every shipped row names a photo that really exists."""
    rows = common.load_labels(BENCH / "data" / name / "labels.json", required)
    for r in rows:
        assert (BENCH / "data" / name / r["file"]).is_file()


# ---- deck rows (metric,value,unit,device,build,ts) ------------------------------

def test_count_metric_rows_for_the_deck():
    rows = [
        {"truth": 12, "pred": 12, "ms": 900.0, "answered_by": ER2, "error": ""},
        {"truth": 10, "pred": None, "ms": 1100.0, "answered_by": ER2, "error": ""},
        {"truth": 8, "pred": 8, "ms": 14000.0, "answered_by": FLASH, "error": ""},
        {"truth": 6, "pred": 6, "ms": 800.0, "answered_by": "", "error": ""},  # answering model unknown
    ]
    out = score_count.metric_rows(rows, er2_model=ER2, device="Pixel 7a", build="b1",
                                  ts="2026-10-13T10:00:00Z")
    by = [(r["metric"], r["value"], r["unit"]) for r in out]
    assert ("count_accuracy", 75.0, "%") in by
    assert ("count_abstain_rate", 25.0, "%") in by
    # ER 2 latency only from calls ER 2 really answered; fallback answers are shown, not hidden.
    assert [v for m, v, _ in by if m == "er2_count_latency_ms"] == [900.0, 1100.0]
    assert [v for m, v, _ in by if m == "count_fallback_latency_ms"] == [14000.0]
    assert all(r["device"] == "Pixel 7a" and r["build"] == "b1" and r["ts"] == "2026-10-13T10:00:00Z"
               for r in out)


def test_metric_rows_empty_set_writes_nothing():
    assert score_count.metric_rows([], er2_model=ER2, device="", build="b", ts="t") == []
    assert score_identify.metric_rows([], device="", build="b", ts="t") == []


def test_identify_metric_rows_for_the_deck():
    rows = [{"truth": "aavin_milk_1l", "pred": "aavin_milk_1l"},
            {"truth": "aavin_milk_1l", "pred": None},
            {"truth": "aavin_ghee_200ml", "pred": "aavin_ghee_200ml"},
            {"truth": "aavin_paneer_200g", "pred": "aavin_butter_100g"}]
    out = score_identify.metric_rows(rows, device="", build="b1", ts="2026-10-13T10:00:00Z")
    assert [(r["metric"], r["value"], r["unit"]) for r in out] == [("product_id_accuracy", 50.0, "%")]


def test_write_metrics_csv_rejects_non_numbers(tmp_path):
    good = [{"metric": "count_accuracy", "value": 95.0, "unit": "%", "device": "", "build": "b", "ts": "t"}]
    p = tmp_path / "r" / "m.csv"
    common.write_metrics_csv(p, good)
    assert p.read_text(encoding="utf-8").splitlines() == [",".join(common.METRIC_HEADER),
                                                          "count_accuracy,95.0,%,,b,t"]
    for bad in (None, float("nan"), float("inf"), "95", True):
        with pytest.raises(ValueError):
            common.write_metrics_csv(tmp_path / "bad.csv", [{**good[0], "value": bad}])


def test_utc_now_iso_is_iso8601_utc():
    ts = common.utc_now_iso()
    assert ts.endswith("Z") and "T" in ts and len(ts) == len("2026-10-13T10:00:00Z")


# ---- count CLI ----------------------------------------------------------------------

@pytest.fixture
def no_count_calls(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("must not call Gemini")

    monkeypatch.setattr(count_mod, "generate_count_json", boom)


def _count_set(tmp_path, make_jpeg, counts=(3, 2)):
    rows = []
    for i, c in enumerate(counts, 1):
        name = f"c{i:02d}.jpg"
        (tmp_path / name).write_bytes(make_jpeg())
        rows.append({"file": name, "label": "Sakthi curd 1 L pack", "count": c})
    (tmp_path / "labels.json").write_text(json.dumps(rows))
    return rows


def test_count_check_dataset_reports_problems(tmp_path, make_jpeg):
    (tmp_path / "c01.jpg").write_bytes(make_jpeg())
    (tmp_path / "c02.jpg").write_bytes(b"\x89PNG\r\n\x1a\n not a jpeg")
    rows = [{"file": "c01.jpg", "label": "Aavin milk 500 ml pouch", "count": 4},
            {"file": "c02.jpg", "label": "Aavin milk 1 L pouch", "count": 5},
            {"file": "c03.jpg", "label": "Arun ice cream box", "count": 6},
            {"file": "c01.jpg", "label": "Aavin milk 500 ml pouch", "count": -1},
            {"file": "c01.jpg", "label": "", "count": 3},
            {"file": "c01.jpg", "label": "Aavin milk 500 ml pouch", "count": "12"},
            {"file": "c01.jpg", "label": "Aavin milk 500 ml pouch", "count": True}]
    rep = score_count.check_dataset(rows, tmp_path)
    assert rep["n"] == 7
    assert rep["missing"] == ["c03.jpg"]
    assert rep["not_jpeg"] == ["c02.jpg"]
    assert rep["bad_count"] == [3, 5, 6]
    assert rep["bad_label"] == [4]
    assert rep["ready"] is False


def test_count_check_dataset_empty_is_not_ready(tmp_path):
    assert score_count.check_dataset([], tmp_path)["ready"] is False


def test_count_dry_run_makes_no_calls_and_writes_nothing(tmp_path, make_jpeg, no_count_calls, capsys):
    _count_set(tmp_path, make_jpeg)
    out = tmp_path / "results"
    assert score_count.main(["--dry-run", "--data", str(tmp_path), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "DRY RUN" in text and "READY" in text and models_mod.model_for("count") in text
    assert not out.exists()


def test_count_without_photos_is_not_ready(tmp_path, no_count_calls, capsys):
    (tmp_path / "labels.json").write_text("[]")
    out = tmp_path / "results"
    assert score_count.main(["--data", str(tmp_path), "--out", str(out)]) == 1
    assert "NOT READY" in capsys.readouterr().out
    assert not out.exists()


def test_count_without_labels_file_is_not_ready(tmp_path, no_count_calls, capsys):
    assert score_count.main(["--dry-run", "--data", str(tmp_path)]) == 1
    assert "labels.json" in capsys.readouterr().out


def _points(n):
    return [{"point": [200 + 60 * i, 500], "label": "pack", "cut": False} for i in range(n)]


def test_count_live_run_writes_deck_csv_summary_and_rows(tmp_path, make_jpeg, monkeypatch, capsys):
    _count_set(tmp_path, make_jpeg, counts=(3, 4))
    replies = [{"items": _points(3), "_model": ER2}, {"items": _points(5), "_model": FLASH}]
    calls = []

    async def fake_generate_json(parts, thinking="medium", timeout_s=25.0):
        calls.append((thinking, timeout_s))
        return replies.pop(0)

    monkeypatch.setattr(count_mod, "generate_count_json", fake_generate_json)
    out = tmp_path / "results"
    code = score_count.main(["--data", str(tmp_path), "--out", str(out), "--device", "Pixel 7a",
                             "--build", "b1"])
    text = capsys.readouterr().out
    assert code == 0
    assert calls == [("medium", 25.0)] * 2
    assert count_mod.generate_count_json is fake_generate_json  # spy removed

    summary = json.loads((out / "count.json").read_text())
    assert (summary["n"], summary["exact"], summary["within1"], summary["abstain"]) == (2, 0.5, 1.0, 0.0)
    assert summary["mae"] == 0.5
    assert summary["errors"] == 0 and summary["fallbacks"] == 1
    assert summary["model"] == models_mod.model_for("count")
    assert {"p50_ms", "p95_ms"} <= set(summary)

    with (out / "rows" / "count.csv").open(encoding="utf-8") as f:
        detail = list(csv.DictReader(f))
    assert [(r["file"], r["truth"], r["pred"], r["answered_by"]) for r in detail] == [
        ("c01.jpg", "3", "3", ER2), ("c02.jpg", "4", "5", FLASH)]

    with (out / "count.csv").open(encoding="utf-8") as f:
        deck = list(csv.DictReader(f))
    assert list(deck[0]) == common.METRIC_HEADER
    metrics = [r["metric"] for r in deck]
    assert metrics.count("count_accuracy") == 1 and metrics.count("count_abstain_rate") == 1
    assert metrics.count("er2_count_latency_ms") == 1 and metrics.count("count_fallback_latency_ms") == 1
    assert {r["device"] for r in deck} == {"Pixel 7a"} and {r["build"] for r in deck} == {"b1"}
    assert "answered by the fallback" in text


def test_count_run_with_failed_calls_writes_no_deck_numbers(tmp_path, make_jpeg, monkeypatch, capsys):
    from aira_live.vision.gemini import VisionError

    _count_set(tmp_path, make_jpeg, counts=(3, 4))
    replies = [{"items": _points(3), "_model": ER2}, VisionError("all models failed")]

    async def fake_generate_json(parts, thinking="medium", timeout_s=25.0):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(count_mod, "generate_count_json", fake_generate_json)
    out = tmp_path / "results"
    assert score_count.main(["--data", str(tmp_path), "--out", str(out)]) == 1
    assert "failed" in capsys.readouterr().out
    assert not (out / "count.csv").exists() and not (out / "count.json").exists()
    detail = list(csv.DictReader((out / "rows" / "count.csv").open(encoding="utf-8")))
    assert "VisionError" in detail[1]["error"]
    assert math.isnan(float(detail[1]["ms"]))


# ---- identify CLI -------------------------------------------------------------------

@pytest.fixture
def no_identify_calls(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("must not call Gemini")

    monkeypatch.setattr(identify_mod, "generate_json", boom)


def _identify_set(tmp_path, make_jpeg, skus, per_sku=2):
    rows, i = [], 0
    for sku in skus:
        for _ in range(per_sku):
            i += 1
            name = f"i{i:02d}.jpg"
            (tmp_path / name).write_bytes(make_jpeg())
            rows.append({"file": name, "sku": sku})
    (tmp_path / "labels.json").write_text(json.dumps(rows))
    return rows


def test_identify_dry_run_needs_every_sku_twice(tmp_path, make_jpeg, no_identify_calls, capsys):
    skus = common.candidate_skus()
    _identify_set(tmp_path, make_jpeg, skus[:-1], per_sku=2)  # last SKU missing
    assert score_identify.main(["--dry-run", "--data", str(tmp_path)]) == 1
    text = capsys.readouterr().out
    assert "NOT READY" in text and skus[-1] in text


def test_identify_dry_run_ready(tmp_path, make_jpeg, no_identify_calls, capsys):
    _identify_set(tmp_path, make_jpeg, common.candidate_skus(), per_sku=2)
    out = tmp_path / "results"
    assert score_identify.main(["--dry-run", "--data", str(tmp_path), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "DRY RUN" in text and "READY" in text and models_mod.model_for("point") in text
    assert not out.exists()


def test_identify_unknown_sku_is_not_ready(tmp_path, make_jpeg, no_identify_calls, capsys):
    rows = _identify_set(tmp_path, make_jpeg, common.candidate_skus(), per_sku=2)
    rows[0]["sku"] = "aavin_milk_2l"
    (tmp_path / "labels.json").write_text(json.dumps(rows))
    assert score_identify.main(["--data", str(tmp_path), "--out", str(tmp_path / "r")]) == 1
    assert "aavin_milk_2l" in capsys.readouterr().out
    assert not (tmp_path / "r").exists()


def test_identify_live_run_writes_deck_csv_summary_and_rows(tmp_path, make_jpeg, monkeypatch, capsys):
    rows = _identify_set(tmp_path, make_jpeg, common.candidate_skus(), per_sku=2)
    answers = {r["file"].removesuffix(".jpg"): r["sku"] for r in rows}
    answers["i01"] = None  # one abstention
    answers["i03"] = "aavin_milk_500ml"  # truth aavin_milk_1l: label not readable -> code abstains
    answers["i15"] = "aavin_butter_100g"  # truth aavin_paneer_200g, label readable -> confident wrong
    seen_frames = iter(sorted(answers))

    async def fake_generate_json(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        assert (role, thinking, timeout_s) == ("point", "low", 6.0)
        fid = next(seen_frames)
        visible = fid != "i03"
        return {"sku": answers[fid], "confidence": 0.95, "alternatives": [],
                "label_visible": visible, "_model": ER2}

    monkeypatch.setattr(identify_mod, "generate_json", fake_generate_json)
    out = tmp_path / "results"
    assert score_identify.main(["--data", str(tmp_path), "--out", str(out), "--build", "b2"]) == 0
    assert identify_mod.generate_json is fake_generate_json

    n = len(rows)
    summary = json.loads((out / "identify.json").read_text())
    assert summary["n"] == n
    assert summary["accuracy"] == round((n - 3) / n, 3)
    assert summary["abstain"] == round(2 / n, 3)
    assert summary["wrong_confident"] == round(1 / n, 3)
    assert summary["pairs"] == {"aavin_paneer_200g->aavin_butter_100g": 1}
    assert summary["errors"] == 0 and summary["fallbacks"] == 0
    assert summary["model"] == models_mod.model_for("point")

    detail = list(csv.DictReader((out / "rows" / "identify.csv").open(encoding="utf-8")))
    assert len(detail) == n and detail[0]["pred"] == "" and detail[0]["answered_by"] == ER2

    deck = list(csv.DictReader((out / "identify.csv").open(encoding="utf-8")))
    assert [(r["metric"], r["unit"], r["build"]) for r in deck] == [("product_id_accuracy", "%", "b2")]
    assert float(deck[0]["value"]) == pytest.approx(round((n - 3) / n * 100, 1))


# ---- the deck's table reads what the scorers write ------------------------------

@pytest.mark.skipif(not BENCH_TABLE.exists(), reason="shared/scripts/bench_table.py not merged yet")
def test_bench_table_reads_scorer_output(tmp_path):
    spec = importlib.util.spec_from_file_location("bench_table", BENCH_TABLE)
    bench_table = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench_table)

    count_rows = [{"truth": 3, "pred": 3, "ms": 900.0, "answered_by": ER2, "error": ""},
                  {"truth": 4, "pred": None, "ms": 950.0, "answered_by": ER2, "error": ""}]
    id_rows = [{"truth": "aavin_milk_1l", "pred": "aavin_milk_1l"}]
    p1, p2 = tmp_path / "count.csv", tmp_path / "identify.csv"
    common.write_metrics_csv(p1, score_count.metric_rows(count_rows, er2_model=ER2, device="Pixel 7a",
                                                         build="b", ts="2026-10-13T10:00:00Z"))
    common.write_metrics_csv(p2, score_identify.metric_rows(id_rows, device="Pixel 7a", build="b",
                                                            ts="2026-10-13T10:00:00Z"))
    table = {s["metric"]: s for s in bench_table.summarize(bench_table.load_rows([p1, p2]))}
    assert table["count_accuracy"]["result"] == "50.0%"
    assert table["count_abstain_rate"]["result"] == "50.0%"
    assert table["er2_count_latency"]["n"] == 2
    assert table["product_id_accuracy"]["result"] == "100.0%"
    assert set(table) <= set(bench_table.EXPECTED_METRICS)
