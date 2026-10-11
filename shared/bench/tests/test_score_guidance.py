import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))
from score_guidance import main, metric_rows, percentile, score  # noqa: E402

EXAMPLE = BENCH / "data" / "guidance" / "trials.example.json"


def _trial(n, start, touch, ok, product="sakthi_curd_1l"):
    return {"trial": n, "product": product, "startedAtMs": start, "touchedAtMs": touch, "correctItem": ok}


def _write_log(path: Path, trials: list[dict]) -> Path:
    path.write_text(json.dumps({"trials": trials}), encoding="utf-8")
    return path


def _bench_table():
    spec = importlib.util.spec_from_file_location("bench_table", BENCH.parent / "scripts" / "bench_table.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_percentile_nearest_rank():
    assert percentile([6.0, 12.0, 8.4], 50) == 8.4
    assert percentile([6.0, 12.0, 8.4], 95) == 12.0
    assert percentile([], 50) is None


def test_score_counts_successes_wrong_and_timeouts():
    trials = [
        {"trial": 1, "product": "sakthi_curd_1l", "startedAtMs": 0, "touchedAtMs": 8400, "correctItem": True},
        {"trial": 2, "product": "sakthi_curd_1l", "startedAtMs": 1000, "touchedAtMs": 13000, "correctItem": True},
        {"trial": 3, "product": "aavin_milk_500ml", "startedAtMs": 0, "touchedAtMs": 6000, "correctItem": True},
        {"trial": 4, "product": "aavin_milk_500ml", "startedAtMs": 0, "touchedAtMs": 5000, "correctItem": False},
        {"trial": 5, "product": "arun_icecream_box", "startedAtMs": 0, "touchedAtMs": None, "correctItem": False},
    ]
    out = score(trials)
    assert out == {"trials": 5, "successes": 3, "successRate": 0.6, "wrongItem": 1, "timeouts": 1,
                   "p50S": 8.4, "p95S": 12.0, "meanS": 8.8, "meetsSampleSize": False}


def test_score_rejects_touch_before_start():
    with pytest.raises(ValueError):
        score([{"trial": 1, "product": "x", "startedAtMs": 5000, "touchedAtMs": 1000, "correctItem": True}])


def test_score_empty_log_reports_nothing_measured():
    assert score([]) == {"trials": 0, "successes": 0, "successRate": 0.0, "wrongItem": 0, "timeouts": 0,
                         "p50S": None, "p95S": None, "meanS": None, "meetsSampleSize": False}


def test_score_meets_sample_size_at_twenty_trials():
    trials = [_trial(i, 0, 5000 + i * 100, True) for i in range(1, 21)]
    assert score(trials)["meetsSampleSize"] is True
    assert score(trials[:19])["meetsSampleSize"] is False


@pytest.mark.parametrize("bad", [
    {"trial": 1, "product": "x", "touchedAtMs": 1000, "correctItem": True},                       # no start
    {"trial": 1, "product": "x", "startedAtMs": "0", "touchedAtMs": 1000, "correctItem": True},   # string ms
    {"trial": 1, "product": "x", "startedAtMs": 0, "touchedAtMs": True, "correctItem": True},     # bool ms
    {"trial": 1, "product": "x", "startedAtMs": 0, "touchedAtMs": 1000, "correctItem": "yes"},    # not a bool
    {"trial": 1, "product": "x", "startedAtMs": 0, "touchedAtMs": 1000},                          # no verdict
    "trial 1",                                                                                     # not an object
])
def test_score_rejects_malformed_trials(bad):
    """A broken log must stop the scorer, never become a made-up time or a silent timeout."""
    with pytest.raises(ValueError):
        score([bad])


def test_metric_rows_one_ms_row_per_correct_touch_plus_success_rate():
    trials = [_trial(1, 0, 8400, True), _trial(2, 1000, 13000, True),
              _trial(3, 0, 5000, False), _trial(4, 0, None, False)]
    rows = metric_rows(trials, device="Pixel 7a", build="abc123", ts="2026-10-14T10:00:00Z")
    touch = [r for r in rows if r["metric"] == "guidance_time_to_touch"]
    assert [(r["value"], r["unit"]) for r in touch] == [(8400, "ms"), (12000, "ms")]
    rate = [r for r in rows if r["metric"] == "guidance_success_rate"]
    assert [(r["value"], r["unit"]) for r in rate] == [(50.0, "%")]
    assert all(r["device"] == "Pixel 7a" and r["build"] == "abc123" and r["ts"] == "2026-10-14T10:00:00Z"
               for r in rows)


def test_metric_rows_no_trials_writes_nothing():
    assert metric_rows([], device="", build="dev", ts="t") == []


def test_cli_scores_the_format_example(capsys):
    assert main([str(EXAMPLE)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["trials"] == 3
    assert out["successes"] == 2
    assert out["successRate"] == 0.667
    assert out["timeouts"] == 1
    assert out["meetsSampleSize"] is False


def test_cli_writes_no_files_without_out(tmp_path, monkeypatch, capsys):
    log = _write_log(tmp_path / "trials.json", [_trial(1, 0, 8400, True)])
    monkeypatch.chdir(tmp_path)
    assert main([str(log)]) == 0
    capsys.readouterr()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["trials.json"]


def test_cli_out_writes_deck_rows_that_bench_table_reads(tmp_path, capsys):
    log = _write_log(tmp_path / "trials.json",
                     [_trial(1, 0, 8400, True), _trial(2, 1000, 13000, True), _trial(3, 0, None, False)])
    out_dir = tmp_path / "results"
    assert main([str(log), "--out", str(out_dir), "--device", "Pixel 7a", "--build", "abc123"]) == 0
    capsys.readouterr()

    with (out_dir / "guidance.csv").open(newline="", encoding="utf-8") as f:
        assert next(csv.reader(f)) == ["metric", "value", "unit", "device", "build", "ts"]
    summary = json.loads((out_dir / "guidance.json").read_text(encoding="utf-8"))
    assert summary["trials"] == 3 and summary["successes"] == 2
    assert summary["device"] == "Pixel 7a" and summary["build"] == "abc123"

    bt = _bench_table()
    table = {s["metric"]: s for s in bt.summarize(bt.load_rows([out_dir / "guidance.csv"]))}
    assert table["guidance_time_to_touch"]["n"] == 2
    assert table["guidance_time_to_touch"]["result"] == "p50 8400 ms · p95 12000 ms"
    assert table["guidance_success_rate"]["result"] == "66.7%"


def test_cli_refuses_to_publish_the_format_example(tmp_path, capsys):
    """The example numbers are not measurements, so they must never reach the deck table."""
    out_dir = tmp_path / "results"
    assert main([str(EXAMPLE), "--out", str(out_dir)]) == 2
    assert "example" in capsys.readouterr().err
    assert not out_dir.exists()


def test_cli_bad_log_exits_with_error(tmp_path, capsys):
    log = _write_log(tmp_path / "trials.json", [_trial(1, 5000, 1000, True)])
    out_dir = tmp_path / "results"
    assert main([str(log), "--out", str(out_dir)]) == 2
    assert "trial 1" in capsys.readouterr().err
    assert not out_dir.exists()


def test_cli_rejects_log_without_trials_list(tmp_path, capsys):
    log = tmp_path / "trials.json"
    log.write_text(json.dumps([_trial(1, 0, 8400, True)]), encoding="utf-8")
    assert main([str(log)]) == 2
    assert "trials" in capsys.readouterr().err
