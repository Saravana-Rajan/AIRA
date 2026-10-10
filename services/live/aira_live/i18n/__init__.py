"""Spoken strings in ta, hi and en. Every owner adds keys to all three catalogs (same keys, same placeholders)."""
import json
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent
LANGS = ("ta", "hi", "en")


@lru_cache(maxsize=None)
def _catalog(lang: str) -> dict[str, str]:
    if lang not in LANGS:
        return {}
    return json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def t(key: str, lang: str, **kw) -> str:
    """The text for ``key`` in ``lang`` (falls back to English, then to the key). Never raises."""
    text = _catalog(lang).get(key) or _catalog("en").get(key) or key
    if not kw:
        return text
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        return text
