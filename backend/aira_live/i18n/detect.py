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


# ---------- sticky session language ----------
# Gemini's input transcript sometimes renders Tamil or Hindi words in Latin letters ("Amo" for ஆமா, "paal" for
# பால்). Those are still the speaker's language. The list is small on purpose: the common yes/no/want/
# don't-want/product words of a dairy shop conversation.
ROMANISED_INDIC: frozenset[str] = frozenset({
    # Tamil
    "aama", "ama", "amo", "aamaa", "aamam", "amma", "illa", "illai", "ille", "venum", "venam", "vendam",
    "vendum", "paal", "pal", "thayir", "thair", "sari", "seri", "romba", "konjam", "kudunga", "kodunga",
    "enna", "evlo", "evvalavu", "ethana", "rendu", "onnu", "moonu", "naalu", "anju", "inniki", "innaiki",
    "naalaiki", "vanakkam", "nalla", "nallathu", "podhum", "pothum", "mudinchu", "irukku", "iruku", "aachu",
    "poitu", "paakanum", "sollu", "sollunga", "yen", "yenna", "adhu", "idhu", "inga", "anga",
    # Hindi
    "haan", "han", "ha", "nahi", "nahin", "theek", "thik", "accha", "achha", "acha", "dudh", "doodh", "dahi",
    "kitna", "kitne", "chahiye", "nai", "bas", "abhi", "kal", "aaj", "bolo", "batao", "kya", "kaun", "kahan",
    "do", "teen", "char", "paanch", "ek", "ji", "haanji", "namaste", "sahi",
})
# Words that belong to no language in a shop conversation: confirmations, fillers, brands, units and the
# product nouns the owner mixes into any sentence. They never count towards a language change.
_NEUTRAL: frozenset[str] = frozenset({
    "ok", "okay", "k", "yes", "no", "yeah", "yep", "nope", "hmm", "mm", "hm", "ah", "oh", "uh", "um", "hi",
    "hello", "hey", "bye", "stop", "cancel", "done", "wait", "thanks", "thank", "you", "please", "sorry",
    "fine", "aira",
    "milk", "curd", "yogurt", "ghee", "butter", "paneer", "ice", "cream", "aavin", "sakthi", "arun", "amul",
    "litre", "liter", "litres", "liters", "ml", "kg", "g", "gram", "grams", "packet", "packets", "pack",
    "piece", "pieces", "cup", "cups", "rupees", "rupee", "rs", "upi", "cash",
})
# Scripts written without spaces: a run of letters is a sentence, so characters are counted instead of words.
_UNSPACED: frozenset[str] = frozenset({"ja", "zh", "th", "lo", "km", "my"})
SENTENCE_WORDS = 3   # this many letter-words in another language is a clear sentence: switch at once
STREAK_WORDS = 2     # this many in each of two consecutive utterances is a switch too


def _letter_words(text: str) -> list[tuple[str, str]]:
    """``(word, language)`` for every run of letters in ``text``; the language is that of most of its letters."""
    words: list[tuple[str, str]] = []
    buf: list[str] = []
    counts: dict[str, int] = {}

    def flush() -> None:
        if buf:
            words.append(("".join(buf), max(counts, key=counts.__getitem__)))
            buf.clear()
            counts.clear()

    for ch in text:
        code = _script_of(ord(ch))
        if code is None:
            flush()
            continue
        buf.append(ch)
        counts[code] = counts.get(code, 0) + 1
    flush()
    return words


def _new_language_weight(text: str, previous: str, candidate: str) -> int:
    """How strongly ``text`` speaks ``candidate`` rather than ``previous``: the letter-words (characters, for an
    unspaced script) that belong to ``candidate`` and are neither neutral nor a romanised Indic word."""
    weight = 0
    for word, code in _letter_words(text):
        if code == "zh" and candidate == "ja":
            code = "ja"  # kanji inside a Japanese sentence
        if code != candidate:
            continue
        low = word.lower()
        if candidate == "en" and (low in _NEUTRAL or (previous != "en" and low in ROMANISED_INDIC)):
            continue
        weight += len(word) if candidate in _UNSPACED else 1
    return weight


def follow_lang(text: str, previous: str, pending: str | None = None) -> tuple[str, str | None]:
    """The session language after ``text``, and the language streak to carry to the next utterance.

    The language changes only when the speaker clearly speaks another language:
    - at least ``SENTENCE_WORDS`` letter-words in the new language in one utterance, or
    - at least ``STREAK_WORDS`` such words in each of two consecutive utterances (``pending`` is the streak
      language from the previous utterance, or ``None``).
    Short replies (one or two words, confirmations, numbers) never change the language, and romanised Tamil
    or Hindi words ("amo", "paal", "haan") count as the current Indic language, never as English.
    Never raises.
    """
    previous = previous or FALLBACK
    if not isinstance(text, str):
        return previous, None
    candidate = detect_lang(text, previous)
    if candidate == previous:
        return previous, None
    weight = _new_language_weight(text, previous, candidate)
    if weight >= SENTENCE_WORDS or (weight >= STREAK_WORDS and pending == candidate):
        return candidate, None
    return previous, candidate if weight >= STREAK_WORDS else None
