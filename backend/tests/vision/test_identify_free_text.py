"""Free-text identify (spec §4): "is the thing under the fingertip a water bottle?" Code decides, never the model."""
import pytest

from aira_live.frames import Frame
from aira_live.vision import identify
from aira_live.vision.identify import FreeTextResult, decide_free_text, identify_free_text


def _frame():
    return Frame(id="f1", jpeg_b64="/9j/4AAQSkZJRg==", w=10, h=10, ts=1.0, purpose="guide")


def test_match_with_confidence_is_accepted():
    res = decide_free_text({"match": True, "confidence": 0.9, "seen": "water bottle"}, "water bottle")
    assert res == FreeTextResult(match=True, confidence=0.9, seen="water bottle")


def test_match_below_accept_is_not_a_match():
    res = decide_free_text({"match": True, "confidence": 0.4, "seen": "water bottle"}, "water bottle")
    assert res.match is False and res.confidence == 0.4


def test_mismatch_keeps_what_was_seen():
    res = decide_free_text({"match": False, "confidence": 0.95, "seen": "jam jar"}, "water bottle")
    assert res.match is False and res.seen == "jam jar" and res.confidence == 0.95


@pytest.mark.parametrize("raw", [None, "yes", 42, [], {"match": "true", "confidence": "high"},
                                 {"match": True, "confidence": True}, {"match": True, "confidence": float("nan")}])
def test_junk_is_never_a_match(raw):
    res = decide_free_text(raw, "water bottle")
    assert res.match is False and res.confidence == 0.0


def test_string_flags_and_percentages_are_understood():
    res = decide_free_text({"match": "yes", "confidence": 85, "seen": " Water Bottle "}, "water bottle")
    assert res.match is True and res.confidence == 0.85 and res.seen == "Water Bottle"


def test_seen_is_short_and_never_the_label_when_missing():
    res = decide_free_text({"match": False, "confidence": 0.9}, "water bottle")
    assert res.seen == ""
    res = decide_free_text({"match": False, "confidence": 0.9, "seen": "x" * 200}, "water bottle")
    assert len(res.seen) <= identify.MAX_SEEN


async def test_identify_free_text_asks_the_point_model_about_the_label(monkeypatch):
    seen = {}

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        seen.update(role=role, prompt=parts[-1], schema=schema_hint)
        return {"match": True, "confidence": 0.8, "seen": "water bottle"}

    monkeypatch.setattr(identify, "generate_json", fake)
    res = await identify_free_text(_frame(), "water bottle")
    assert res.match is True
    assert seen["role"] == "point" and "water bottle" in seen["prompt"] and "match" in seen["schema"]


async def test_identify_free_text_blank_label_is_never_a_match():
    res = await identify_free_text(_frame(), "   ")
    assert res == FreeTextResult(match=False, confidence=0.0, seen="")
