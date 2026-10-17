"""Canonical shop zones: 'fridge two, middle shelf, left' <-> 'fridge2/middle/left' <-> English words.

``normalize_zone`` reads English words (the Live tool asks the model to pass the place in English);
``speak_zone`` gives the English reading that Live phrases in the owner's language.
"""
import re

_NUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
_ALIAS = {"centre": "center", "mid": "middle", "upper": "top", "lower": "bottom", "fridges": "fridge"}
_SKIP = {"shelf", "shelves", "the", "on", "in", "of", "side", "at", "number", "no"}
_CONTAINERS = {"fridge", "freezer", "rack", "counter", "cupboard", "box"}
_WORDS = {"top": "top shelf", "middle": "middle shelf", "bottom": "bottom shelf", "center": "centre"}


def normalize_zone(text: str) -> str:
    """'Fridge 2, middle shelf, left' -> 'fridge2/middle/left'. Raises ValueError when no place word is left."""
    tokens = [_NUM.get(tok, _ALIAS.get(tok, tok)) for tok in re.findall(r"[a-z]+|\d+", text.lower())]
    tokens = [tok for tok in tokens if tok not in _SKIP]
    if not tokens:
        raise ValueError(f"empty zone: {text!r}")
    out, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _CONTAINERS and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            out.append(tok + tokens[i + 1])
            i += 2
        else:
            out.append(tok)
            i += 1
    return "/".join(out)


def speak_zone(zone: str, lang: str = "en") -> str:
    """'fridge2/middle/left' -> 'fridge 2, middle shelf, left' (English; ``lang`` is ignored, Live translates)."""
    parts = []
    for part in zone.split("/"):
        m = re.fullmatch(r"([a-z]+)(\d+)", part)
        if m:
            base, num = m.groups()
            parts.append(f"{base} {num}")
        else:
            parts.append(_WORDS.get(part, part))
    return ", ".join(parts)
