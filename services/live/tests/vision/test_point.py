import math

import pytest

from aira_live.contracts import TargetMsg
from aira_live.models import fallback_for, model_for
from aira_live.vision.gemini import VisionError
from aira_live.vision.point import parse_target, point_to


def test_valid_point_and_box():
    t = parse_target({"point": [412, 530], "box": [390, 505, 435, 555], "label": "Sakthi curd", "confidence": 0.8}, "curd")
    assert t.point == (412, 530) and t.box == (390, 505, 435, 555) and t.label == "Sakthi curd"


def test_not_visible_returns_none():
    assert parse_target({"point": None}, "curd") is None
    assert parse_target({}, "curd") is None


def test_out_of_range_or_malformed_point_is_none():
    assert parse_target({"point": [1200, 10]}, "curd") is None
    assert parse_target({"point": [10]}, "curd") is None
    assert parse_target({"point": [True, 10]}, "curd") is None
    assert parse_target({"point": "300,400"}, "curd") is None
    assert parse_target({"point": [float("nan"), 10]}, "curd") is None
    assert parse_target({"point": [-1, 10]}, "curd") is None


def test_non_dict_reply_is_none():
    assert parse_target(None, "curd") is None
    assert parse_target([300, 400], "curd") is None


def test_low_confidence_is_none():
    assert parse_target({"point": [100, 100], "confidence": 0.1}, "curd") is None
    assert parse_target({"point": [100, 100], "confidence": -0.5}, "curd") is None


def test_unparseable_confidence_uses_default():
    for bad in ("high", None, True, float("nan"), float("inf")):
        t = parse_target({"point": [100, 100], "confidence": bad}, "curd")
        assert t is not None and t.confidence == 0.5, bad


def test_percentage_confidence_is_scaled_and_over_100_is_rejected():
    t = parse_target({"point": [100, 100], "confidence": 85}, "curd")
    assert t is not None and t.confidence == pytest.approx(0.85)
    assert parse_target({"point": [100, 100], "confidence": 20}, "curd") is None
    assert parse_target({"point": [100, 100], "confidence": 150}, "curd") is None


def test_box_not_containing_point_is_dropped():
    t = parse_target({"point": [100, 100], "box": [500, 500, 600, 600], "confidence": 0.9}, "curd")
    assert t is not None and t.box is None


def test_malformed_box_is_dropped():
    t = parse_target({"point": [100, 100], "box": [50, 50, 150], "confidence": 0.9}, "curd")
    assert t is not None and t.box is None
    t = parse_target({"point": [100, 100], "box": [50, 50, 1500, 150], "confidence": 0.9}, "curd")
    assert t is not None and t.box is None


def test_box_2d_key_is_accepted():
    # Gemini's detection format names the box "box_2d".
    t = parse_target({"point": [412, 530], "box_2d": [390, 505, 435, 555], "confidence": 0.8}, "curd")
    assert t.box == (390, 505, 435, 555)


def test_missing_label_uses_requested_label_and_default_confidence():
    t = parse_target({"point": [250.4, 750.6]}, "Aavin milk")
    assert t.label == "Aavin milk" and t.point == (250, 751) and t.confidence == 0.5
    t = parse_target({"point": [250, 750], "label": "   "}, "Aavin milk")
    assert t.label == "Aavin milk"
    t = parse_target({"point": [250, 750], "label": 7}, "Aavin milk")
    assert t.label == "Aavin milk"


def test_result_fits_the_target_server_message():
    t = parse_target({"point": [999.6, 0.2], "box": [990, 0, 1000, 10], "label": " Arun ice cream box ",
                      "confidence": 92}, "ice cream")
    msg = TargetMsg(frame_id="f1", label=t.label, point=t.point, box=t.box, confidence=t.confidence)
    assert msg.point == (1000, 0) and msg.box == (990, 0, 1000, 10) and msg.label == "Arun ice cream box"
    assert math.isfinite(msg.confidence) and 0.0 <= msg.confidence <= 1.0


async def test_point_to_handles_list_reply(fake_genai, frame):
    client = fake_genai('[{"point": [300, 400], "label": "Arun ice cream box"}]')
    t = await point_to(frame, "Arun ice cream box")
    assert t.point == (300, 400)
    call = client.aio.models.calls[0]
    assert call["model"] == model_for("point")
    assert any("Arun ice cream box" in str(p) for p in call["contents"])


async def test_point_to_skips_invalid_list_items(fake_genai, frame):
    fake_genai('[{"point": [1200, 5]}, "noise", {"point": [300, 400], "label": "Sakthi curd 1 L"}]')
    t = await point_to(frame, "Sakthi curd 1 L")
    assert t is not None and t.point == (300, 400) and t.label == "Sakthi curd 1 L"


async def test_point_to_object_reply(fake_genai, frame):
    fake_genai('```json\n{"point": [412, 530], "box": [390, 505, 435, 555], "confidence": 0.8}\n```')
    t = await point_to(frame, "Aavin milk 500 ml")
    assert t.point == (412, 530) and t.box == (390, 505, 435, 555) and t.label == "Aavin milk 500 ml"


async def test_point_to_not_visible(fake_genai, frame):
    fake_genai('{"point": null}')
    assert await point_to(frame, "Sakthi curd 1 L") is None


async def test_point_to_empty_list_is_none(fake_genai, frame):
    fake_genai('[]')
    assert await point_to(frame, "Aavin ghee 500 ml jar") is None


async def test_point_to_non_list_items_is_none(fake_genai, frame):
    fake_genai('{"items": 5}')
    assert await point_to(frame, "Aavin paneer 200 g") is None


async def test_point_to_falls_back_then_raises_when_all_fail(fake_genai, frame):
    client = fake_genai(RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom"))
    with pytest.raises(VisionError):
        await point_to(frame, "Aavin butter 100 g")
    assert [c["model"] for c in client.aio.models.calls] == [model_for("point"), model_for("point"), fallback_for("point")]
