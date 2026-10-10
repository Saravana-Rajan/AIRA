"""bench/publish.py: p50/p95 CSV from the app's /bench NDJSON export. bench/ is on sys.path via conftest.py."""
import csv
import json
from pathlib import Path

import publish
from publish import load_ndjson, main, percentile, summarize, write_csv

BENCH = Path(__file__).resolve().parents[1]

ROW = {"metric": "first_audio_ms", "value": 650, "unit": "ms", "device": "Redmi Note 13",
       "build": "0.1.0", "ts": "2026-10-15T10:00:00.000Z"}


def _write(tmp_path, rows, name="a.ndjson"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in rows) + "\n")
    return str(p)


def test_percentile_nearest_rank():
    assert percentile([5, 1, 3, 2, 4], 50) == 3
    assert percentile(list(range(1, 11)), 95) == 10
    assert percentile([], 50) is None


def test_load_skips_invalid_rows(tmp_path):
    path = _write(tmp_path, [ROW, {"metric": "x"}, "not json", {**ROW, "value": "fast"}, "",
                             {**ROW, "value": True}, {**ROW, "ts": "yesterday"}, "[1, 2]"])
    rows, skipped = load_ndjson([path])
    assert rows == [ROW] and skipped == 6


def test_load_rejects_non_finite_values(tmp_path):
    path = _write(tmp_path, ['{"metric": "first_audio_ms", "value": NaN, "unit": "ms", "device": "d",'
                             ' "build": "0.1.0", "ts": "2026-10-15T10:00:00.000Z"}',
                             '{"metric": "first_audio_ms", "value": Infinity, "unit": "ms", "device": "d",'
                             ' "build": "0.1.0", "ts": "2026-10-15T10:00:00.000Z"}'])
    rows, skipped = load_ndjson([path])
    assert rows == [] and skipped == 2


def test_load_reads_several_files(tmp_path):
    a = _write(tmp_path, [ROW], "a.ndjson")
    b = _write(tmp_path, [{**ROW, "value": 700}], "b.ndjson")
    rows, skipped = load_ndjson([a, b])
    assert [r["value"] for r in rows] == [650, 700] and skipped == 0


def test_summarize_per_metric_and_device():
    rows = [{**ROW, "value": v} for v in [600, 700, 800, 900, 1000]] + [{**ROW, "metric": "sale_e2e_ms", "value": 20000}]
    s = summarize(rows)
    fa = next(x for x in s if x["metric"] == "first_audio_ms")
    assert fa == {"metric": "first_audio_ms", "device": "Redmi Note 13", "unit": "ms", "n": 5,
                  "p50": 800, "p95": 1000, "mean": 800.0}
    assert [x["metric"] for x in s] == ["first_audio_ms", "sale_e2e_ms"]


def test_summarize_keeps_devices_apart_and_fractional_percentiles():
    rows = [{**ROW, "device": "A", "value": 1.5}, {**ROW, "device": "B", "value": 2}]
    s = summarize(rows)
    assert [(x["device"], x["p50"], x["p95"]) for x in s] == [("A", 1.5, 1.5), ("B", 2, 2)]


def test_write_csv(tmp_path):
    out = tmp_path / "results" / "summary.csv"
    write_csv([{"metric": "first_audio_ms", "device": "d", "unit": "ms", "n": 1, "p50": 1, "p95": 1, "mean": 1.0}], str(out))
    rows = list(csv.DictReader(out.open()))
    assert list(rows[0].keys()) == ["metric", "device", "unit", "n", "p50", "p95", "mean"]
    assert rows[0]["metric"] == "first_audio_ms" and rows[0]["p95"] == "1"


def test_default_csv_goes_to_bench_results():
    assert Path(publish.DEFAULT_CSV) == BENCH / "results" / "summary.csv"


def test_main_writes_csv_and_reports(tmp_path, capsys):
    path = _write(tmp_path, [{**ROW, "value": v} for v in [600, 700, 800]] + ["junk"])
    out = tmp_path / "results" / "summary.csv"
    assert main([path, "--csv", str(out)]) == 0
    rows = list(csv.DictReader(out.open()))
    assert rows == [{"metric": "first_audio_ms", "device": "Redmi Note 13", "unit": "ms", "n": "3",
                     "p50": "700", "p95": "800", "mean": "700.0"}]
    printed = capsys.readouterr().out
    assert "valid=3 skipped=1" in printed and "p50=700 p95=800" in printed


def test_main_fails_when_no_valid_rows(tmp_path):
    path = _write(tmp_path, ["junk"])
    out = tmp_path / "results" / "summary.csv"
    assert main([path, "--csv", str(out)]) == 1
    assert not out.exists()


def test_module_has_no_bigquery_load():
    assert not hasattr(publish, "bq_load_cmd")
    assert "subprocess" not in Path(publish.__file__).read_text()
