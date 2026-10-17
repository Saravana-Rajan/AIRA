"""Aim hints: a fixed key set with a short English meaning each (Live phrases it in the owner's language)."""
from aira_live.vision.hints import HINT_KEYS, HINTS, hint_text


def test_hint_keys_match_the_interface_list():
    assert set(HINT_KEYS) == {
        "vision.aim.move_back", "vision.aim.spread_out", "vision.aim.show_items",
        "vision.aim.show_label", "vision.aim.hold_closer", "vision.aim.not_visible",
    }


def test_every_key_has_a_short_english_meaning():
    assert set(HINTS) == set(HINT_KEYS)
    for key in HINT_KEYS:
        text = hint_text(key)
        assert text and text == HINTS[key] and text.endswith(".") and text.isascii(), key


def test_hint_text_ignores_the_language_and_handles_unknown_keys():
    assert hint_text("") == "" and hint_text("", "ta") == ""
    assert hint_text("vision.aim.unknown") == "" and hint_text("vision.aim.unknown", "en") == ""
    assert hint_text("vision.aim.move_back", "ta") == hint_text("vision.aim.move_back", "fr") == \
        hint_text("vision.aim.move_back") == "Move the phone back a little."
