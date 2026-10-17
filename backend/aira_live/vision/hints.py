"""Aim/retake hints: a key per situation, with a short English meaning.

Vision results carry only the key (``aim_hint``); tools pass it on as a fact and Gemini Live tells the owner,
in whatever language he is speaking, what to do with the phone. ``hint_text(key)`` gives the English meaning
for ``say_en`` and the caption. Nothing here is translated.
"""
from __future__ import annotations

HINT_KEYS: tuple[str, ...] = (
    "vision.aim.move_back",     # packs cut off at the frame edge
    "vision.aim.spread_out",    # packs stacked or overlapping
    "vision.aim.show_items",    # nothing countable in view
    "vision.aim.show_label",    # size label needed (confusion pairs such as 500 ml vs 1 L)
    "vision.aim.hold_closer",   # item too small or too far away, or too many packs to count in one view
    "vision.aim.not_visible",   # pointing target not in view
)

# The English meaning per key: what the owner should do with the phone.
HINTS: dict[str, str] = {
    "vision.aim.move_back": "Move the phone back a little.",
    "vision.aim.spread_out": "Spread the items apart so I can count them.",
    "vision.aim.show_items": "Show me the items in front of the camera.",
    "vision.aim.show_label": "Turn the pack so the label faces the camera.",
    "vision.aim.hold_closer": "Hold it closer to the camera.",
    "vision.aim.not_visible": "I can't see it right now.",
}


def hint_text(key: str, lang: str | None = None) -> str:
    """The short English aim hint for ``key``; "" for an empty or unknown key. ``lang`` is ignored (kept for
    callers that still pass it): Live phrases the hint in the owner's language."""
    if not key:
        return ""
    return HINTS.get(key, "")
