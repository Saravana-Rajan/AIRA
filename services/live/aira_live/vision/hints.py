"""Aim/retake hints spoken to Murugan.

The sentences live in the shared i18n catalog (``aira_live/i18n/{en,ta,hi}.json``, keys ``vision.aim.*``).
Vision results carry only the key (``aim_hint``); tools speak ``hint_text(result.aim_hint, session.lang)``,
which is the same as ``i18n.t(key, lang)``.
"""
from __future__ import annotations

from aira_live.i18n import t

LANGS: tuple[str, ...] = ("en", "ta", "hi")

HINT_KEYS: tuple[str, ...] = (
    "vision.aim.move_back",     # packs cut off at the frame edge
    "vision.aim.spread_out",    # packs stacked or overlapping
    "vision.aim.show_items",    # nothing countable in view
    "vision.aim.show_label",    # size label needed (confusion pairs such as 500 ml vs 1 L)
    "vision.aim.hold_closer",   # item too small or too far away, or too many packs to count in one view
    "vision.aim.not_visible",   # pointing target not in view
)


def hint_text(key: str, lang: str) -> str:
    """Thin wrapper over ``i18n.t``: "" for an empty or unknown key; an unknown language falls back to English."""
    if not key:
        return ""
    text = t(key, lang)
    return "" if text == key else text


# Read-only view of the catalog sentences per key and language. i18n stays the single source of truth.
HINTS: dict[str, dict[str, str]] = {key: {lang: hint_text(key, lang) for lang in LANGS} for key in HINT_KEYS}
