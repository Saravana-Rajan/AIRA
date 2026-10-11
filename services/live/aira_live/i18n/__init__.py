"""Spoken strings in ta, hi and en, plus ``detect_lang``.

AIRA has no default and no fixed language: it answers in whatever language the speaker used last. The
catalogs here only cover the fixed lines (hints, confirmations, errors) in Tamil, Hindi and English; every
owner adds keys to all three catalogs (same keys, same placeholders). For any other language ``t`` gives the
English line: the Live model is told to say it in the speaker's language, and the phone's own TTS reads it
in English rather than staying silent.
"""
import json
from functools import lru_cache
from pathlib import Path

from aira_live.i18n.detect import detect_lang

_DIR = Path(__file__).parent
LANGS = ("ta", "hi", "en")  # the languages with a catalog file; not a limit on what AIRA speaks

__all__ = ["LANGS", "detect_lang", "t"]


@lru_cache(maxsize=None)
def _catalog(lang: str) -> dict[str, str]:
    if lang not in LANGS:
        return {}
    return json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def t(key: str, lang: str, **kw) -> str:
    """The text for ``key`` in ``lang``; English for a language without a catalog, then the key. Never raises."""
    text = _catalog(lang).get(key) or _catalog("en").get(key) or key
    if not kw:
        return text
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        return text
