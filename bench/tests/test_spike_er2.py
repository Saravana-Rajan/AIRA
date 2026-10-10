import csv
import json
import math

import pytest

import aira_live.models as models_mod
import aira_live.vision.identify as identify_mod
import spike_er2


# ---- decision rule ---------------------------------------------------------

def test_spike_decision_threshold():
    assert spike_er2.decide(2999) == "KEEP"
    assert spike_er2.decide(3000) == "KEEP"
    assert spike_er2.decide(3001) == "SWITCH"


def test_decision_switches_when_flash_is_clearly_more_accurate():
    assert spike_er2.decide(1500, er2_accuracy=0.70, flash_accuracy=0.90) == "SWITCH"
    assert spike_er2.decide(1500, er2_accuracy=0.80, flash_accuracy=0.90) == "SWITCH"  # exactly 10 points
    assert spike_er2.decide(1500, er2_accuracy=0.85, flash_accuracy=0.90) == "KEEP"
    assert spike_er2.decide(1500, er2_accuracy=0.95, flash_accuracy=0.80) == "KEEP"
    assert spike_er2.decide(4000, er2_accuracy=0.95, flash_accuracy=0.80) == "SWITCH"  # too slow anyway


def test_decision_never_switches_to_a_slower_flash():
    assert spike_er2.decide(3500, flash_p50_ms=1200) == "SWITCH"
    assert spike_er2.decide(3500, flash_p50_ms=3500) == "KEEP"
    assert spike_er2.decide(3500, flash_p50_ms=4800) == "KEEP"
    # A clearly more accurate Flash is still worth switching to.
    assert spike_er2.decide(3500, er2_accuracy=0.6, flash_accuracy=0.9, flash_p50_ms=4800) == "SWITCH"


# ---- summary -----------------------------------------------------------------

def _row(truth, pred, ms, err=""):
    return {"pass": "er2", "model": "m", "file": "x.jpg", "truth": truth, "pred": pred,
            "confidence": 0.9, "ms": ms, "error": err}


def test_summarize_drops_failed_calls_from_latency():
    rows = [
        _row("aavin_milk_1l", "aavin_milk_1l", 1000.0),
        _row("aavin_milk_500ml", None, 2000.0),
        _row("sakthi_curd_1l", "sakthi_curd_500ml", 3000.0),
        _row("arun_icecream_box", None, float("nan"), "TimeoutError()"),
    ]
    s = spike_er2.summarize(rows)
    assert s["n"] == 4
    assert s["p50_ms"] == 2000
    assert s["accuracy"] == 0.25
    assert s["abstain"] == 0.25  # a failed call is an error, not an abstain
    assert s["wrong"] == 0.25
    assert s["errors"] == 1


def test_summarize_empty_and_all_failed():
    assert spike_er2.summarize([])["p50_ms"] is None
    s = spike_er2.summarize([_row("aavin_milk_1l", None, float("nan"), "boom")])
    assert s["p50_ms"] is None and s["errors"] == 1


# ---- dataset check (used by --dry-run) ---------------------------------------

def test_check_dataset_reports_problems(tmp_path, make_jpeg):
    (tmp_path / "p01.jpg").write_bytes(make_jpeg())
    (tmp_path / "p02.jpg").write_bytes(b"not a jpeg (maybe HEIC)")
    rows = [
        {"file": "p01.jpg", "sku": "aavin_milk_1l"},
        {"file": "p02.jpg", "sku": "aavin_milk_1l"},
        {"file": "p03.jpg", "sku": "aavin_milk_500ml"},
        {"file": "p01.jpg", "sku": "amul_milk_1l"},
    ]
    rep = spike_er2.check_dataset(rows, tmp_path, ["aavin_milk_500ml", "aavin_milk_1l"], min_per_sku=2)
    assert rep["missing"] == ["p03.jpg"]
    assert rep["not_jpeg"] == ["p02.jpg"]
    assert rep["unknown_sku"] == ["amul_milk_1l"]
    assert rep["coverage"] == {"aavin_milk_500ml": 1, "aavin_milk_1l": 2}
    assert rep["thin"] == ["aavin_milk_500ml"]
    assert rep["ready"] is False


def test_check_dataset_ready(tmp_path, make_jpeg):
    for name in ("p01.jpg", "p02.jpg"):
        (tmp_path / name).write_bytes(make_jpeg())
    rows = [{"file": "p01.jpg", "sku": "aavin_milk_1l"}, {"file": "p02.jpg", "sku": "aavin_milk_1l"}]
    rep = spike_er2.check_dataset(rows, tmp_path, ["aavin_milk_1l"], min_per_sku=2)
    assert rep["ready"] is True and rep["thin"] == []


def test_check_dataset_empty_is_not_ready(tmp_path):
    rep = spike_er2.check_dataset([], tmp_path, ["aavin_milk_1l"])
    assert rep["ready"] is False and rep["n"] == 0


# ---- CLI -------------------------------------------------------------------------

@pytest.fixture
def no_network(monkeypatch):
    async def boom(frame, candidates):
        raise AssertionError("dry run must not call Gemini")

    monkeypatch.setattr(identify_mod, "identify_product", boom)


@pytest.fixture
def fake_models(monkeypatch):
    import os

    monkeypatch.delenv("AIRA_MODEL_POINT", raising=False)
    monkeypatch.setattr(models_mod, "model_for",
                        lambda role: os.environ.get("AIRA_MODEL_POINT") or "test-er2")
    monkeypatch.setattr(models_mod, "fallback_for", lambda role: "test-flash")


def _dataset(tmp_path, make_jpeg, n_per_sku=2):
    skus = ["aavin_milk_500ml", "aavin_milk_1l"]
    rows, i = [], 0
    for sku in skus:
        for _ in range(n_per_sku):
            i += 1
            name = f"p{i:02d}.jpg"
            (tmp_path / name).write_bytes(make_jpeg())
            rows.append({"file": name, "sku": sku})
    (tmp_path / "labels.json").write_text(json.dumps(rows))
    return rows


def test_dry_run_ready_dataset_makes_no_calls(tmp_path, make_jpeg, no_network, fake_models, capsys):
    _dataset(tmp_path, make_jpeg)
    out = tmp_path / "results" / "spike.csv"
    code = spike_er2.main(["--dry-run", "--data", str(tmp_path), "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 0
    assert "DRY RUN" in text and "READY" in text
    assert "test-er2" in text and "test-flash" in text
    assert not out.exists()


def test_dry_run_without_photos_is_not_ready(tmp_path, no_network, fake_models, capsys):
    (tmp_path / "labels.json").write_text("[]")
    code = spike_er2.main(["--dry-run", "--data", str(tmp_path)])
    assert code == 1
    assert "NOT READY" in capsys.readouterr().out


def test_dry_run_without_labels_file_is_not_ready(tmp_path, no_network, fake_models, capsys):
    code = spike_er2.main(["--dry-run", "--data", str(tmp_path)])
    assert code == 1
    assert "labels.json" in capsys.readouterr().out


def test_live_run_with_fake_model_writes_results_and_decides(tmp_path, make_jpeg, fake_models,
                                                             monkeypatch, capsys):
    import os

    rows = _dataset(tmp_path, make_jpeg)
    truth = {r["file"].removesuffix(".jpg"): r["sku"] for r in rows}
    seen_models = []

    async def fake_identify(frame, candidates):
        seen_models.append(os.environ.get("AIRA_MODEL_POINT"))
        assert {c.sku for c in candidates} >= set(truth.values())
        return identify_mod.IdentifyResult(sku=truth[frame.id], confidence=0.9, alternatives=[])

    monkeypatch.setattr(identify_mod, "identify_product", fake_identify)
    monkeypatch.setenv("AIRA_MODEL_POINT", "operator-choice")
    out = tmp_path / "results" / "spike.csv"
    code = spike_er2.main(["--data", str(tmp_path), "--out", str(out)])
    text = capsys.readouterr().out

    assert code == 0
    assert "DECISION: KEEP" in text
    # ER 2 pass runs with no override, Flash pass forces the fallback, then the env is restored.
    assert seen_models == [None] * 4 + ["test-flash"] * 4
    assert os.environ["AIRA_MODEL_POINT"] == "operator-choice"

    with out.open(encoding="utf-8") as f:
        written = list(csv.DictReader(f))
    assert [r["pass"] for r in written] == ["er2"] * 4 + ["flash"] * 4
    assert {r["model"] for r in written if r["pass"] == "er2"} == {"test-er2"}
    assert {r["model"] for r in written if r["pass"] == "flash"} == {"test-flash"}
    summary = json.loads(out.with_suffix(".json").read_text())
    assert summary["decision"] == "KEEP"
    assert summary["er2"]["accuracy"] == 1.0


def test_live_run_records_which_model_answered(tmp_path, make_jpeg, fake_models, monkeypatch, capsys):
    """If ER 2 times out, generate_json answers with the Flash fallback inside the same call.
    The CSV must say who really answered, so ER 2's numbers are not silently Flash's."""
    _dataset(tmp_path, make_jpeg, n_per_sku=1)
    calls = []

    async def fake_generate_json(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        calls.append(role)
        return {"sku": "aavin_milk_1l", "confidence": 0.95, "alternatives": [],
                "label_visible": True, "_model": "test-flash"}

    real_generate_json = identify_mod.generate_json
    monkeypatch.setattr(identify_mod, "generate_json", fake_generate_json)
    out = tmp_path / "results" / "spike.csv"
    code = spike_er2.main(["--data", str(tmp_path), "--out", str(out)])
    text = capsys.readouterr().out

    assert code == 0
    assert calls == ["point"] * 4
    assert identify_mod.generate_json is fake_generate_json  # spy removed after each pass
    with out.open(encoding="utf-8") as f:
        written = list(csv.DictReader(f))
    assert [r["answered_by"] for r in written] == ["test-flash"] * 4
    assert [r["pred"] for r in written] == ["aavin_milk_1l"] * 4
    summary = json.loads(out.with_suffix(".json").read_text())
    assert summary["er2"]["fallbacks"] == 2
    assert summary["flash"]["fallbacks"] == 0
    assert summary["er2"]["accuracy"] == 0.5
    assert "answered by the fallback" in text
    assert real_generate_json is not fake_generate_json


def test_summarize_counts_fallback_answers():
    rows = [
        {**_row("aavin_milk_1l", "aavin_milk_1l", 900.0), "answered_by": "m"},
        {**_row("aavin_milk_1l", "aavin_milk_1l", 7000.0), "answered_by": "flash"},
        {**_row("aavin_milk_1l", None, float("nan"), "boom"), "answered_by": ""},
        _row("aavin_milk_1l", "aavin_milk_1l", 800.0),  # identify faked: answering model unknown
    ]
    assert spike_er2.summarize(rows)["fallbacks"] == 1


def test_dry_run_names_the_real_models(tmp_path, make_jpeg, no_network, capsys):
    _dataset(tmp_path, make_jpeg)
    assert spike_er2.main(["--dry-run", "--data", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert models_mod.model_for("point") in text
    assert models_mod.fallback_for("point") in text


def test_shipped_labels_point_at_real_photos():
    """No fabricated data: every shipped label row names a photo that exists, with a known SKU."""
    import common

    packs = common.BENCH / "data" / "packs"
    rows = common.load_labels(packs / "labels.json", {"file", "sku"})
    rep = spike_er2.check_dataset(rows, packs, common.candidate_skus())
    assert rep["missing"] == [] and rep["not_jpeg"] == [] and rep["unknown_sku"] == []


def _pass_aware(monkeypatch, er2_ms, flash_ms, flash_fails=False):
    """Fake identify + timing per pass: the Flash pass runs with AIRA_MODEL_POINT=test-flash."""
    import os

    def is_flash():
        return os.environ.get("AIRA_MODEL_POINT") == "test-flash"

    async def identify(frame, candidates):
        if is_flash() and flash_fails:
            raise RuntimeError("flash unavailable")
        return identify_mod.IdentifyResult(sku="aavin_milk_1l", confidence=0.9, alternatives=[])

    async def fake_timed(coro):
        result = await coro
        return result, (flash_ms if is_flash() else er2_ms)

    monkeypatch.setattr(identify_mod, "identify_product", identify)
    monkeypatch.setattr(spike_er2, "timed", fake_timed)


def test_live_run_never_switches_to_a_flash_that_failed(tmp_path, make_jpeg, fake_models, monkeypatch, capsys):
    _dataset(tmp_path, make_jpeg, n_per_sku=1)
    _pass_aware(monkeypatch, er2_ms=4000.0, flash_ms=900.0, flash_fails=True)
    out = tmp_path / "r" / "s.csv"
    code = spike_er2.main(["--data", str(tmp_path), "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 1
    assert "NO DECISION" in text and "every Flash call failed" in text
    assert "DECISION: SWITCH" not in text
    assert json.loads(out.with_suffix(".json").read_text())["decision"] is None


def test_live_run_keeps_er2_but_warns_when_flash_failed(tmp_path, make_jpeg, fake_models, monkeypatch, capsys):
    _dataset(tmp_path, make_jpeg, n_per_sku=1)
    _pass_aware(monkeypatch, er2_ms=1500.0, flash_ms=900.0, flash_fails=True)
    code = spike_er2.main(["--data", str(tmp_path), "--out", str(tmp_path / "r" / "s.csv")])
    text = capsys.readouterr().out
    assert code == 0
    assert "DECISION: KEEP" in text
    assert "every Flash call failed" in text


def test_live_run_switches_when_er2_is_slow_and_flash_answers(tmp_path, make_jpeg, fake_models, monkeypatch,
                                                              capsys):
    _dataset(tmp_path, make_jpeg, n_per_sku=1)
    _pass_aware(monkeypatch, er2_ms=4000.0, flash_ms=900.0)
    out = tmp_path / "r" / "s.csv"
    code = spike_er2.main(["--data", str(tmp_path), "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 0
    assert "DECISION: SWITCH identify + point to test-flash" in text
    assert "pointing_model=test-flash" in text and "AIRA_MODEL_POINT" in text
    assert json.loads(out.with_suffix(".json").read_text())["decision"] == "SWITCH"


def test_live_run_keeps_er2_when_flash_is_slower(tmp_path, make_jpeg, fake_models, monkeypatch, capsys):
    _dataset(tmp_path, make_jpeg, n_per_sku=1)
    _pass_aware(monkeypatch, er2_ms=3600.0, flash_ms=5200.0)
    out = tmp_path / "r" / "s.csv"
    code = spike_er2.main(["--data", str(tmp_path), "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 0
    assert "DECISION: KEEP" in text
    assert "Flash is slower" in text
    summary = json.loads(out.with_suffix(".json").read_text())
    assert summary["decision"] == "KEEP"
    assert summary["er2"]["p50_ms"] == 3600 and summary["flash"]["p50_ms"] == 5200


def test_live_run_all_failures_gives_no_decision(tmp_path, make_jpeg, fake_models, monkeypatch, capsys):
    _dataset(tmp_path, make_jpeg, n_per_sku=1)

    async def failing(frame, candidates):
        raise RuntimeError("no GEMINI_API_KEY")

    monkeypatch.setattr(identify_mod, "identify_product", failing)
    code = spike_er2.main(["--data", str(tmp_path), "--out", str(tmp_path / "r" / "s.csv")])
    text = capsys.readouterr().out
    assert code == 1
    assert "NO DECISION" in text
    summary = json.loads((tmp_path / "r" / "s.json").read_text())
    assert summary["decision"] is None
    assert math.isnan(float(list(csv.DictReader((tmp_path / "r" / "s.csv").open()))[0]["ms"]))
