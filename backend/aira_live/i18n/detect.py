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

from dataclasses import dataclass

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
    latin = candidate == "en" or candidate in LATIN_LANGS
    for word, code in _letter_words(text):
        if code == "zh" and candidate == "ja":
            code = "ja"  # kanji inside a Japanese sentence
        if code == "en" and latin:
            code = candidate  # Latin letters: Spanish, Vietnamese, ... (latin_lang) or English
        if code != candidate:
            continue
        low = word.lower()
        if latin and (low in _NEUTRAL or (previous != "en" and low in ROMANISED_INDIC)):
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
    Without an expected-language list every language is allowed (``judge_lang`` adds the list). Never raises.
    """
    step = judge_lang(text, previous, pending)
    return step.lang, step.pending


# ---------- Latin-script languages ----------
# Gemini's auto language detection sometimes hears short or accented English as Spanish or Portuguese and
# transcribes a sentence the owner never said ("¿Eh? ¿Cuántos años tienes?"). A light guess tells those
# transcripts apart from English so the session can treat them as "unclear" instead of switching.
# Stop words are chosen to be rare in English and in romanised Tamil/Hindi; shared Romance words ("la", "de",
# "que") count for every language that uses them, so the diacritics and punctuation decide the ties.
LATIN_STOP_WORDS: dict[str, frozenset[str]] = {
    "es": frozenset({
        "el", "la", "los", "las", "de", "del", "que", "es", "un", "una", "por", "para", "con", "como", "pero",
        "muy", "tiene", "tienes", "hay", "quiero", "quiere", "dónde", "donde", "qué", "cuánto", "cuántos",
        "cuánta", "cuántas", "años", "hola", "gracias", "usted", "esta", "este", "esto", "eso", "aquí", "también",
        "bien", "buenos", "buenas", "días", "señor", "señora", "leche", "litros", "cuesta", "dos", "tres", "sí",
        "y", "eh",
    }),
    "pt": frozenset({
        "o", "os", "a", "as", "da", "das", "do", "dos", "de", "que", "é", "um", "uma", "por", "para", "com",
        "como", "mas", "muito", "tem", "você", "não", "está", "quanto", "quantos", "obrigado", "obrigada",
        "olá", "onde", "aqui", "também", "bem", "bom", "dia", "leite", "hoje", "litros", "dois", "três", "e",
    }),
    "fr": frozenset({
        "le", "la", "les", "des", "du", "de", "que", "est", "un", "une", "pour", "avec", "comme", "mais", "très",
        "avez", "vous", "nous", "je", "suis", "combien", "où", "bonjour", "merci", "oui", "c'est", "pas", "lait",
        "litres", "deux", "trois", "et", "aujourd'hui",
    }),
    "it": frozenset({
        "il", "lo", "la", "gli", "le", "del", "della", "che", "è", "un", "una", "per", "con", "come", "ma",
        "molto", "hai", "quanto", "quanti", "costa", "anni", "ciao", "grazie", "dove", "sono", "questo", "questa",
        "latte", "litri", "due", "tre", "e", "oggi",
    }),
    "de": frozenset({
        "der", "die", "das", "den", "dem", "des", "und", "ist", "ein", "eine", "nicht", "ich", "sie", "wir", "wie",
        "viel", "wieviel", "wo", "danke", "hallo", "bitte", "milch", "ja", "nein", "haben", "liter", "zwei",
        "drei", "heute",
    }),
    "id": frozenset({  # Indonesian and Malay
        "saya", "mau", "ingin", "dua", "tiga", "berapa", "susu", "yang", "dan", "ini", "itu", "tidak", "ada",
        "terima", "kasih", "apa", "di", "ke", "dari", "untuk", "hari", "harga", "liter", "nak", "awak",
    }),
    "vi": frozenset({
        "tôi", "muốn", "hai", "ba", "của", "và", "không", "có", "bao", "nhiêu", "sữa", "cho", "xin", "chào",
        "cảm", "ơn", "hộp", "chua", "này", "lít", "hôm", "nay",
    }),
    "fil": frozenset({
        "ako", "ikaw", "ang", "ng", "mga", "po", "ba", "hindi", "oo", "gatas", "magkano", "salamat", "kumusta",
        "ito", "iyan", "sa", "ay", "dalawa", "tatlo", "litro", "ngayon",
    }),
}
# Letters and marks that point strongly (2) or weakly (1) at one language.
_STRONG_MARKS: dict[str, str] = {"es": "ñ¿¡", "pt": "ãõ", "fr": "œ", "de": "ß", "vi": "ơưđăạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"}
_WEAK_MARKS: dict[str, str] = {"es": "áéíóú", "pt": "çáéíóúê", "fr": "çàèùâêîôûëé", "it": "àèìòù", "de": "äöü"}
# Plain English stop words: when they outnumber the guess, the sentence is English with a stray accent.
_ENGLISH: frozenset[str] = frozenset({
    "the", "is", "are", "was", "how", "much", "many", "what", "where", "when", "which", "who", "do", "does", "you",
    "i", "my", "we", "it", "this", "that", "these", "and", "for", "of", "to", "in", "on", "with", "have", "has",
    "want", "need", "give", "me", "please", "today", "now", "one", "two", "three", "there", "here", "can",
    "could", "will", "would", "not", "all", "any", "some", "get", "take", "put", "from", "at", "by",
})
LATIN_LANGS: frozenset[str] = frozenset(LATIN_STOP_WORDS)
_LATIN_MIN_SCORE = 2


def latin_lang(text: str) -> str | None:
    """A guess at which non-English Latin-script language ``text`` is in, or ``None`` for English/unsure.

    Evidence: inverted punctuation and language-specific letters (strong), accented letters (weak) and small
    stop-word lists. A guess needs at least two points and more than the English stop words in the sentence.
    Never raises.
    """
    if not isinstance(text, str) or not text:
        return None
    low = text.lower()
    # Whole tokens keep "c'est" and "avez-vous" together; letter runs split them for the plain stop words.
    tokens = [w.strip(".,;:!?\"()¿¡") for w in low.split()]
    tokens += [w for w, code in _letter_words(low) if code == "en"]
    scores: dict[str, int] = {}
    for lang, stop in LATIN_STOP_WORDS.items():
        hits = sum(1 for w in set(tokens) if w in stop)
        strong = 2 if any(ch in low for ch in _STRONG_MARKS.get(lang, "")) else 0
        weak = 1 if any(ch in low for ch in _WEAK_MARKS.get(lang, "")) else 0
        score = hits + strong + weak
        if score:
            scores[lang] = score
    if not scores:
        return None
    best = max(scores, key=lambda k: (scores[k], -list(LATIN_STOP_WORDS).index(k)))
    english = sum(1 for w in set(tokens) if w in _ENGLISH)
    if scores[best] < _LATIN_MIN_SCORE or scores[best] <= english:
        return None
    return best


# ---------- expected languages ----------
UNEXPECTED_SENTENCES = 3  # consecutive full sentences in a language the shop does not usually speak: switch


@dataclass(frozen=True)
class LangStep:
    """The outcome of one utterance: the session language, the streak carried to the next utterance, and
    whether the transcript looked like a language the shop does not usually speak (``unclear``, ``heard``)."""

    lang: str
    pending: str | None = None
    unclear: bool = False
    heard: str | None = None
    streak: int = 0


def _normalise(codes) -> list[str]:
    out: list[str] = []
    for c in codes or ():
        if isinstance(c, str):
            base = c.strip().lower().split("-")[0]
            if base and base not in out:
                out.append(base)
    return out


def judge_lang(text: str, previous: str, pending: str | None = None, expected=None, streak: int = 0) -> LangStep:
    """``follow_lang`` guided by the shop's expected languages.

    ``expected`` (BCP-47 codes, or ``None`` for "any language") lists what the shop usually speaks. A full
    sentence in another language is flagged ``unclear`` and never switches the session, unless
    ``UNEXPECTED_SENTENCES`` consecutive full sentences come in it (``streak`` carries the count). Latin-script
    transcripts are told apart with ``latin_lang`` so a misheard "¿Cuántos años tienes?" reads as Spanish,
    not English. Never raises.
    """
    previous = previous or FALLBACK
    if not isinstance(text, str):
        return LangStep(previous)
    candidate = detect_lang(text, previous)
    if candidate == "en":
        candidate = latin_lang(text) or "en"
    if candidate == previous:
        return LangStep(previous)
    weight = _new_language_weight(text, previous, candidate)
    allowed = _normalise(expected)
    if allowed and candidate not in allowed:
        streak = streak + 1 if weight >= SENTENCE_WORDS else 0
        if streak >= UNEXPECTED_SENTENCES:
            return LangStep(candidate)
        return LangStep(previous, None, unclear=weight > 0, heard=candidate if weight > 0 else None,
                        streak=streak)
    if weight >= SENTENCE_WORDS or (weight >= STREAK_WORDS and pending == candidate):
        return LangStep(candidate)
    return LangStep(previous, candidate if weight >= STREAK_WORDS else None)
