import re
from pathlib import Path

import check_submission as cs

ROOT = Path(__file__).resolve().parents[2]
DESC = ROOT / "docs" / "submission" / "description.txt"
MODELS_PY = ROOT / "services" / "live" / "aira_live" / "models.py"

# Removed by the build overrides (§1), dropped features, and words the team never uses.
BANNED = (
    "counterfeit",
    "fake note",
    "firebase auth",
    "app check",
    "cloud messaging",
    "remote config",
    "pub/sub",
    "bigquery",
    "scheduler",
    "udhaar",
    "kaggle",
    "aira business",
    "claude",
    "generated with",
)


def _text() -> str:
    return DESC.read_text(encoding="utf-8").strip()


def test_final_description_passes_checks():
    text = _text()
    assert cs.check_description(text) == []
    assert 900 <= cs.js_length(text) <= cs.MAX_DESCRIPTION


def test_description_has_no_dropped_features():
    text = _text().lower()
    for banned in BANNED:
        assert banned not in text, banned


def test_description_is_one_line():
    assert "\n" not in _text()


def test_description_model_ids_exist_in_models_py():
    """Every model id quoted in the description must be a real default in aira_live/models.py."""
    models_src = MODELS_PY.read_text(encoding="utf-8")
    ids = {m.rstrip(".") for m in re.findall(r"gemini-[\w.-]+", _text())}
    assert ids, "description should name the live model id"
    for model_id in ids:
        assert f'"{model_id}"' in models_src, model_id
