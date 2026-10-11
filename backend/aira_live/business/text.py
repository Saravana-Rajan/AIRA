"""Indic-safe text helpers. Python's \\w drops Tamil/Devanagari vowel signs, so we split on separators instead."""
import re

_SPLIT = re.compile(r"[\s,.;:!?()\[\]{}\"'“”‘’/|\\+*=<>@#&%-]+")
_DIGITS = str.maketrans("௦௧௨௩௪௫௬௭௮௯०१२३४५६७८९", "01234567890123456789")


def normalize_digits(s: str) -> str:
    return s.translate(_DIGITS)


def tokens(s: str) -> list[str]:
    return [t for t in _SPLIT.split(normalize_digits(s).lower().replace("₹", " ₹ ")) if t]
