import json
from pathlib import Path

from aira_live.i18n import t
from aira_live.vision.hints import HINT_KEYS, HINTS, hint_text

LANGS = ("en", "ta", "hi")
I18N_DIR = Path(__file__).resolve().parents[2] / "aira_live" / "i18n"


def test_every_hint_has_three_languages():
    assert set(HINTS) == set(HINT_KEYS)
    for key, entry in HINTS.items():
        for lang in LANGS:
            assert entry.get(lang), f"{key} missing {lang}"


def test_hint_text_fallbacks():
    assert hint_text("", "ta") == ""
    assert hint_text("vision.aim.unknown", "en") == ""
    assert hint_text("vision.aim.move_back", "fr") == HINTS["vision.aim.move_back"]["en"]
    assert hint_text("vision.aim.move_back", "ta") == HINTS["vision.aim.move_back"]["ta"]


def test_hint_text_is_a_thin_wrapper_over_i18n():
    for key in HINT_KEYS:
        for lang in LANGS:
            assert hint_text(key, lang) == t(key, lang)


def test_hint_keys_match_the_interface_list():
    assert set(HINT_KEYS) == {
        "vision.aim.move_back", "vision.aim.spread_out", "vision.aim.show_items",
        "vision.aim.show_label", "vision.aim.hold_closer", "vision.aim.not_visible",
    }


def test_hint_keys_are_in_every_shipped_catalog():
    for lang in LANGS:
        catalog = json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        missing = [k for k in HINT_KEYS if not catalog.get(k)]
        assert missing == [], f"{lang}.json missing {missing}"


def test_languages_are_really_translated():
    for key in HINT_KEYS:
        assert HINTS[key]["ta"] != HINTS[key]["en"]
        assert HINTS[key]["hi"] != HINTS[key]["en"]
