import csv

import pytest

import bench_table as bt

HEADER = "metric,value,unit,device,build,ts\n"


def write(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return p


def table_rows(md: str) -> dict[str, list[str]]:
    """Markdown table body -> {metric: [result, n, device]} (first row per metric)."""
    out: dict[str, list[str]] = {}
    for line in md.splitlines()[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        out.setdefault(cells[0], cells[1:])
    return out


# --- plan tests -------------------------------------------------------------


def test_percentile_nearest_rank():
    assert bt.percentile([100, 200, 300, 400], 50) == 200
    assert bt.percentile([100, 200, 300, 400], 95) == 400
    assert bt.percentile([5], 95) == 5


def test_summary_latency_and_accuracy(tmp_path):
    a = write(tmp_path, "a.csv", [
        "first_audio,800,ms,Pixel 7a,1.0,2026-10-15T10:00:00Z",
        "first_audio,1200,ms,Pixel 7a,1.0,2026-10-15T10:01:00Z",
        "first_audio,900,ms,Pixel 7a,1.0,2026-10-15T10:02:00Z",
    ])
    b = write(tmp_path, "b.csv", [
        "count_accuracy,95,%,Pixel 7a,1.0,2026-10-15T11:00:00Z",
        "count_accuracy,97,%,Pixel 7a,1.0,2026-10-15T11:05:00Z",
    ])
    s = {r["metric"]: r for r in bt.summarize(bt.load_rows([a, b]))}
    assert s["first_audio"]["result"] == "p50 900 ms · p95 1200 ms"
    assert s["count_accuracy"]["result"] == "96.0%"
    assert s["count_accuracy"]["n"] == 2
    md = bt.to_markdown(bt.summarize(bt.load_rows([a, b])))
    assert md.splitlines()[0] == "| Metric | Result | n | Device |"


def test_bad_header_rejected(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("name,val\nx,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="header must be"):
        bt.load_rows([p])


def test_merge_writes_one_csv(tmp_path):
    a = write(tmp_path, "a.csv", ["sale_time,21.5,s,Pixel 7a,1.0,2026-10-15T12:00:00Z"])
    out = tmp_path / "merged.csv"
    assert bt.main([str(a), "--merge", str(out)]) == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert rows[0]["metric"] == "sale_time" and rows[0]["unit"] == "s"


# --- never invent numbers ---------------------------------------------------


def test_percentile_rejects_empty_and_out_of_range():
    with pytest.raises(ValueError):
        bt.percentile([], 50)
    with pytest.raises(ValueError):
        bt.percentile([1, 2], 101)


@pytest.mark.parametrize("bad", ["", "NA", "fast", "nan", "inf"])
def test_missing_or_non_numeric_value_rejected(tmp_path, bad):
    p = write(tmp_path, "a.csv", [f"count_accuracy,{bad},%,Pixel 7a,1.0,2026-10-15T11:00:00Z"])
    with pytest.raises(ValueError, match=r"a\.csv:2"):
        bt.load_rows([p])


def test_short_row_rejected(tmp_path):
    p = write(tmp_path, "a.csv", ["count_accuracy,95"])
    with pytest.raises(ValueError, match=r"a\.csv:2"):
        bt.load_rows([p])


def test_missing_expected_metrics_show_pending(tmp_path):
    a = write(tmp_path, "a.csv", [
        "count_accuracy,95,%,Pixel 7a,1.0,2026-10-15T11:00:00Z",
        "count_accuracy,97,%,Pixel 7a,1.0,2026-10-15T11:05:00Z",
        "my_extra_metric,3,s,Pixel 7a,1.0,2026-10-15T11:06:00Z",
    ])
    md = bt.to_markdown(bt.summarize(bt.load_rows([a])))
    rows = table_rows(md)
    assert rows["count_accuracy"] == ["96.0%", "2", "Pixel 7a"]
    for metric in bt.EXPECTED_METRICS:
        assert metric in rows, metric
        if metric != "count_accuracy":
            assert rows[metric] == ["pending", "0", "—"], metric
    # slide order: expected metrics first, in EXPECTED_METRICS order; extras after
    order = list(rows)
    assert order[: len(bt.EXPECTED_METRICS)] == list(bt.EXPECTED_METRICS)
    assert order[-1] == "my_extra_metric"


def test_expected_metrics_cover_spec_section_7():
    assert list(bt.EXPECTED_METRICS) == [
        "count_accuracy", "count_abstain_rate", "invoice_catch_rate", "cash_total_accuracy",
        "product_id_accuracy", "guidance_time_to_touch", "soundbox_parse_accuracy",
        "sale_time_with_aira", "sale_time_baseline", "task_completion",
        "first_audio", "er2_count_latency", "invoice_check_latency", "cost_per_sale", "uptime",
    ]


def test_markdown_without_expected_lists_only_measured(tmp_path):
    a = write(tmp_path, "a.csv", ["uptime,99.5,%,Cloud Run,1.0,2026-10-15T11:00:00Z"])
    md = bt.to_markdown(bt.summarize(bt.load_rows([a])), expected=())
    assert list(table_rows(md)) == ["uptime"]


def test_no_csv_files_prints_all_pending(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bt, "RESULTS_DIR", tmp_path)
    assert bt.main([]) == 0
    captured = capsys.readouterr()
    rows = table_rows(captured.out)
    assert list(rows) == list(bt.EXPECTED_METRICS)
    assert all(r == ["pending", "0", "—"] for r in rows.values())
    assert "no benchmark CSV" in captured.err


# --- formatting and naming --------------------------------------------------


def test_ms_suffix_is_folded_into_metric_name(tmp_path):
    a = write(tmp_path, "a.csv", [
        "first_audio_ms,650,ms,Redmi Note 13,0.1.0,2026-10-15T10:00:00Z",
        "first_audio,700,ms,Redmi Note 13,0.1.0,2026-10-15T10:01:00Z",
    ])
    s = {r["metric"]: r for r in bt.summarize(bt.load_rows([a]))}
    assert list(s) == ["first_audio"]
    assert s["first_audio"]["n"] == 2


def test_rupee_and_seconds_formatting(tmp_path):
    a = write(tmp_path, "a.csv", [
        "cost_per_sale,1.23,₹,—,1.0,2026-10-15T10:00:00Z",
        "sale_time_with_aira,20,s,Pixel 7a,1.0,2026-10-15T10:00:00Z",
        "sale_time_with_aira,23,s,Pixel 7a,1.0,2026-10-15T10:05:00Z",
    ])
    s = {r["metric"]: r for r in bt.summarize(bt.load_rows([a]))}
    assert s["cost_per_sale"]["result"] == "₹1.23"
    assert s["sale_time_with_aira"]["result"] == "21.50 s"


def test_same_metric_two_units_stay_separate(tmp_path):
    a = write(tmp_path, "a.csv", [
        "sale_time_with_aira,20,s,Pixel 7a,1.0,2026-10-15T10:00:00Z",
        "sale_time_with_aira,21000,ms,Pixel 7a,1.0,2026-10-15T10:00:00Z",
    ])
    summary = bt.summarize(bt.load_rows([a]))
    assert sorted(r["unit"] for r in summary) == ["ms", "s"]
    md = bt.to_markdown(summary)
    assert md.count("| sale_time_with_aira |") == 2


def test_devices_joined_and_pipes_escaped(tmp_path):
    a = write(tmp_path, "a.csv", [
        "uptime,99,%,Pixel 7a,1.0,2026-10-15T10:00:00Z",
        "uptime,100,%,Redmi|Note 13,1.0,2026-10-15T10:00:00Z",
    ])
    md = bt.to_markdown(bt.summarize(bt.load_rows([a])), expected=())
    assert r"| uptime | 99.5% | 2 | Pixel 7a, Redmi\|Note 13 |" in md


# --- CLI ---------------------------------------------------------------------


def test_default_glob_skips_nonconforming_csv(tmp_path, monkeypatch, capsys):
    write(tmp_path, "good.csv", ["count_accuracy,95,%,Pixel 7a,1.0,2026-10-15T11:00:00Z"])
    (tmp_path / "k2_live_run.csv").write_text("step,ms\nread_back,900\n", encoding="utf-8")
    monkeypatch.setattr(bt, "RESULTS_DIR", tmp_path)
    assert bt.main([]) == 0
    captured = capsys.readouterr()
    assert table_rows(captured.out)["count_accuracy"] == ["95.0%", "1", "Pixel 7a"]
    assert "k2_live_run.csv" in captured.err and "skipped" in captured.err


def test_explicit_bad_file_returns_2(tmp_path, capsys):
    p = tmp_path / "bad.csv"
    p.write_text("name,val\nx,1\n", encoding="utf-8")
    assert bt.main([str(p)]) == 2
    assert "header must be" in capsys.readouterr().err


def test_merge_keeps_raw_rows_and_creates_parent(tmp_path):
    a = write(tmp_path, "a.csv", ["first_audio_ms,650,ms,Redmi Note 13,0.1.0,2026-10-15T10:00:00Z"])
    b = write(tmp_path, "b.csv", ["uptime,99.9,%,Cloud Run,1.0,2026-10-15T10:00:00Z"])
    out = tmp_path / "nested" / "merged.csv"
    assert bt.main([str(a), str(b), "--merge", str(out)]) == 0
    text = out.read_text(encoding="utf-8").splitlines()
    assert text[0] == "metric,value,unit,device,build,ts"
    assert text[1] == "first_audio_ms,650,ms,Redmi Note 13,0.1.0,2026-10-15T10:00:00Z"
    assert len(text) == 3
