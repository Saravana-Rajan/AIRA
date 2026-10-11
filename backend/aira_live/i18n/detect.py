"""Which language did the speaker just use? A small, pure, script-based guess.

AIRA has no default and no fixed language: it answers in the language of the last utterance. This function only
has to tell the server which spoken catalog (``i18n/*.json``) and which on-screen labels fit that utterance.
The Live model hears the raw audio and answers in the same language anyway, so a wrong guess among Latin-script
languages (Vietnamese, Indonesian, Malay, Filipino, English) costs nothing: they all map to ``en`` here and the
model still replies in the speaker's language.

Scripts covered (JAPAC): Tamil, Devanagari, Bengali, Gurmukhi, Gujarati, Oriya, Telugu, Kannada, Malayalam,
Sinhala, Thai, Lao, Myanmar, Khmer, Hangul, Hiragana/Katakana, Han, Arabic, Cyrillic, Greek, Hebrew, Latin.
"""
from __future__ import annotations

# (first code point, last code point, language code). Order matters only for ties, which the first entry wins.
_SCRIPTS: tuple[tuple[int, int, str], ...] = (
    (0x0B80, 0x0BFF, "ta"),   # Tamil
    (0x0900, 0x097F, "hi"),   # Devanagari (Hindi, Marathi)
    (0x0980, 0x09FF, "bn"),   # Bengali
    (0x0A00, 0x0A7F, "pa"),   # Gurmukhi (Punjabi)
    (0x0A80, 0x0AFF, "gu"),   # Gujarati
    (0x0B00, 0x0B7F, "or"),   # Oriya
    (0x0C00, 0x0C7F, "te"),   # Telugu
    (0x0C80, 0x0CFF, "kn"),   # Kannada
    (0x0D00, 0x0D7F, "ml"),   # Malayalam
    (0x0D80, 0x0DFF, "si"),   # Sinhala
    (0x0E00, 0x0E7F, "th"),   # Thai
    (0x0E80, 0x0EFF, "lo"),   # Lao
    (0x1000, 0x109F, "my"),   # Myanmar
    (0x1780, 0x17FF, "km"),   # Khmer
    (0x1100, 0x11FF, "ko"),   # Hangul Jamo
    (0x3130, 0x318F, "ko"),   # Hangul compatibility Jamo
    (0xAC00, 0xD7AF, "ko"),   # Hangul syllables
    (0x3040, 0x309F, "ja"),   # Hiragana
    (0x30A0, 0x30FF, "ja"),   # Katakana
    (0x4E00, 0x9FFF, "zh"),   # CJK unified ideographs (Han)
    (0x3400, 0x4DBF, "zh"),   # CJK extension A
    (0x0600, 0x06FF, "ar"),   # Arabic (also Urdu)
    (0x0400, 0x04FF, "ru"),   # Cyrillic
    (0x0370, 0x03FF, "el"),   # Greek
    (0x0590, 0x05FF, "he"),   # Hebrew
)
# Latin letters (ASCII, Latin-1 Supplement, Latin Extended-A/B and Additional: Vietnamese needs the last one).
_LATIN: tuple[tuple[int, int], ...] = ((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F), (0x1E00, 0x1EFF))

FALLBACK = "en"


def _script_of(cp: int) -> str | None:
    for lo, hi, code in _SCRIPTS:
        if lo <= cp <= hi:
            return code
    for lo, hi in _LATIN:
        if lo <= cp <= hi:
            return "en"
    return None


def detect_lang(text: str, previous: str = FALLBACK) -> str:
    """The language code of ``text``, judged by its letters' scripts.

    - Indic, Thai, Korean, Japanese, Chinese and the other listed scripts give their own code.
    - Any non-Latin script present outranks Latin letters: "Sakthi curd ரெண்டு" is Tamil, since product
      names and brands are often spoken in English inside a Tamil or Hindi sentence.
    - Han characters next to kana are Japanese.
    - Latin-only text is ``en`` (the model still answers Vietnamese, Indonesian or Filipino in kind).
    - Text with no letters at all (digits, silence, punctuation, something that is not a string) keeps
      ``previous``: a number read out loud does not change the conversation's language.
    Never raises.
    """
    if not isinstance(text, str):
        return previous or FALLBACK
    counts: dict[str, int] = {}
    for ch in text:
        code = _script_of(ord(ch))
        if code is not None:
            counts[code] = counts.get(code, 0) + 1
    if not counts:
        return previous or FALLBACK
    if "ja" in counts:
        counts.pop("zh", None)  # kanji inside a Japanese sentence
    non_latin = {k: v for k, v in counts.items() if k != "en"}
    if non_latin:
        return max(non_latin, key=non_latin.__getitem__)
    return "en"
