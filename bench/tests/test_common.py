import asyncio
import base64
import csv
import json
from pathlib import Path

import pytest

import common

REPO = Path(common.__file__).resolve().parents[1]
CATALOG = REPO / "content" / "catalog" / "murugan_dairy.json"

DEMO_SKUS = [
    "aavin_milk_500ml", "aavin_milk_1l", "sakthi_curd_500ml", "sakthi_curd_1l",
    "aavin_buttermilk_200ml", "aavin_ghee_200ml", "aavin_ghee_500ml",
    "aavin_paneer_200g", "aavin_butter_100g", "arun_icecream_box",
]


def test_percentile_linear_interpolation():
    assert common.percentile([100, 200, 300, 400], 50) == 250
    assert common.percentile([100], 95) == 100
    assert common.percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == pytest.approx(9.55)


def test_percentile_empty_raises():
    with pytest.raises(ValueError):
        common.percentile([], 50)


def test_load_labels_requires_keys(tmp_path):
    good = tmp_path / "labels.json"
    good.write_text(json.dumps([{"file": "p01.jpg", "sku": "aavin_milk_1l"}]))
    assert common.load_labels(good, {"file", "sku"})[0]["sku"] == "aavin_milk_1l"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([{"file": "p01.jpg"}]))
    with pytest.raises(ValueError):
        common.load_labels(bad, {"file", "sku"})


def test_load_labels_rejects_non_list(tmp_path):
    p = tmp_path / "labels.json"
    p.write_text(json.dumps({"file": "p01.jpg", "sku": "aavin_milk_1l"}))
    with pytest.raises(ValueError):
        common.load_labels(p, {"file", "sku"})


def test_candidates_file_matches_demo_skus():
    skus = [c.sku for c in common.load_candidates()]
    assert skus == DEMO_SKUS
    assert common.candidate_skus() == DEMO_SKUS


@pytest.mark.skipif(not CATALOG.exists(), reason="catalog not merged yet")
def test_candidates_match_catalog():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["products"]
    by_sku = {p["sku"]: p for p in catalog}
    assert set(by_sku) == set(DEMO_SKUS)
    for c in common.load_candidates():
        p = by_sku[c.sku]
        assert (c.name, c.brand, c.pack) == (p["name"], p["brand"], p["pack"])


def test_jpeg_size_reads_sof_header(make_jpeg):
    assert common.jpeg_size(make_jpeg(1280, 720)) == (1280, 720)
    assert common.jpeg_size(b"\x89PNG\r\n\x1a\n....") is None
    assert common.jpeg_size(b"") is None
    assert common.jpeg_size(b"\xff\xd8\xff") is None  # truncated


def test_frame_from_file_builds_frame(tmp_path, make_jpeg):
    data = make_jpeg(800, 600)
    p = tmp_path / "p01.jpg"
    p.write_bytes(data)
    f = common.frame_from_file(p, "identify")
    assert f.id == "p01"
    assert f.purpose == "identify"
    assert base64.b64decode(f.jpeg_b64) == data
    assert (f.w, f.h) == (800, 600)
    assert f.ts > 0


def test_timed_returns_result_and_ms():
    async def work():
        await asyncio.sleep(0.01)
        return "ok"

    result, ms = asyncio.run(common.timed(work()))
    assert result == "ok"
    assert ms >= 5


def test_write_csv(tmp_path):
    out = tmp_path / "results" / "x.csv"
    common.write_csv(out, [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    with out.open(encoding="utf-8") as f:
        assert list(csv.DictReader(f)) == [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]
    empty = tmp_path / "empty.csv"
    common.write_csv(empty, [])
    assert empty.read_text() == ""
