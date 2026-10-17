"""Robotics-ER replies cut off mid-JSON keep their complete objects; JSON mode asks for a high output cap."""
import pytest

from aira_live.vision import gemini


def test_salvage_keeps_complete_objects_from_the_real_failure():
    cut = ('{\n  "objects": [\n    {"label": "water bottle", "point": [548, 729], "box_2d": [500, 700, 600, 760]},\n'
           '    {"label": "furniture", "point": [810, 450], "box_2d": [2')
    assert gemini.extract_json(cut) == {
        "objects": [{"label": "water bottle", "point": [548, 729], "box_2d": [500, 700, 600, 760]}]}


def test_salvage_handles_a_top_level_list_and_escaped_quotes():
    cut = '[{"label": "a \\"quoted\\" box"}, {"label": "b", "point": [1'
    assert gemini.extract_json(cut) == [{"label": 'a "quoted" box'}]


def test_nothing_complete_still_raises():
    with pytest.raises(ValueError):
        gemini.extract_json('{"objects": [{"label": "furn')


def test_config_sets_a_high_output_cap():
    cfg = gemini._config("low")
    assert cfg.max_output_tokens == gemini.MAX_OUTPUT_TOKENS >= 4096
