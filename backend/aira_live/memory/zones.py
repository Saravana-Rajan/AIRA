"""Canonical shop zones: 'fridge two, middle shelf, left' <-> 'fridge2/middle/left' <-> speech.

``normalize_zone`` reads English words (the Live tool asks the model to pass the place in English).
"""
import re

from aira_live.contracts import Lang

_NUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
_ALIAS = {"centre": "center", "mid": "middle", "upper": "top", "lower": "bottom", "fridges": "fridge"}
_SKIP = {"shelf", "shelves", "the", "on", "in", "of", "side", "at", "number", "no"}
_CONTAINERS = {"fridge", "freezer", "rack", "counter", "cupboard", "box"}
_WORDS: dict[str, dict[str, str]] = {
    "en": {"fridge": "fridge", "freezer": "freezer", "rack": "rack", "counter": "counter",
           "cupboard": "cupboard", "box": "box", "top": "top shelf", "middle": "middle shelf",
           "bottom": "bottom shelf", "left": "left", "right": "right", "center": "centre"},
    "ta": {"fridge": "ஃப்ரிட்ஜ்", "freezer": "ஃப்ரீசர்", "rack": "ரேக்", "counter": "கவுண்டர்",
           "cupboard": "அலமாரி", "box": "பெட்டி", "top": "மேல் அலமாரி", "middle": "நடு அலமாரி",
           "bottom": "கீழ் அலமாரி", "left": "இடது", "right": "வலது", "center": "நடுவில்"},
    "hi": {"fridge": "फ्रिज", "freezer": "फ्रीज़र", "rack": "रैक", "counter": "काउंटर",
           "cupboard": "अलमारी", "box": "डिब्बा", "top": "ऊपर की शेल्फ़", "middle": "बीच की शेल्फ़",
           "bottom": "नीचे की शेल्फ़", "left": "बाएँ", "right": "दाएँ", "center": "बीच में"},
}


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


def speak_zone(zone: str, lang: Lang) -> str:
    """'fridge2/middle/left' -> 'fridge 2, middle shelf, left' in ta/hi/en (unknown languages use English)."""
    words = _WORDS.get(lang, _WORDS["en"])
    parts = []
    for part in zone.split("/"):
        m = re.fullmatch(r"([a-z]+)(\d+)", part)
        if m:
            base, num = m.groups()
            parts.append(f"{words.get(base, base)} {num}")
        else:
            parts.append(words.get(part, part))
    return ", ".join(parts)
