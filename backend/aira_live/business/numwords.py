"""Number words → amounts for en / hi (Devanagari) / ta.

Two entry points:
- `amounts_in_words(text) -> list[int]`: every number phrase that reads as an exact whole number, in order.
- `word_amounts(text) -> list[Fraction | None]`: the strict form behind the soundbox parser. A fractional
  phrase (डेढ़ = 1.5, ஒன்றரை = 1.5, साढ़े तीन = 3.5) comes back as a Fraction. A phrase that looks like a
  number but cannot be read exactly (a dangling "நூற்றி", an unknown fused Tamil number, "zero" inside a
  run, a lone "सवा") comes back as None. A caller that must not guess rejects the input on either.

This is a grammar, not a word sum: "sixty and eighty" is two numbers, never 140, and "one two" is 1 and 2.
"""
from __future__ import annotations

import unicodedata
from fractions import Fraction
from functools import lru_cache
from typing import NamedTuple

from aira_live.business.text import tokens


def fold(s: str) -> str:
    """NFC, drop the Devanagari nukta, chandrabindu → anusvara: so हज़ार == हजार and पाँच == पांच."""
    return unicodedata.normalize("NFC", s).replace("़", "").replace("ँ", "ं")


class _W(NamedTuple):
    kind: str          # en/hi: unit teen tens num frac mod hundred big zero and · ta: num comb big bigc half one poison
    value: Fraction
    place: int = 0     # ta only: a combining form ("இருபத்தி", place 10) must be followed by a value below place


_F = Fraction
_HALF = _F(1, 2)

# ---- English --------------------------------------------------------------------------------------------

_EN: dict[str, _W] = {"zero": _W("zero", _F(0)), "and": _W("and", _F(0)), "hundred": _W("hundred", _F(100))}
for _i, _w in enumerate(["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"], start=1):
    _EN[_w] = _W("unit", _F(_i))
for _i, _w in enumerate(["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"],
                        start=10):
    _EN[_w] = _W("teen", _F(_i))
for _w, _v in {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
               "eighty": 80, "ninety": 90}.items():
    _EN[_w] = _W("tens", _F(_v))
for _w, _v in {"thousand": 1000, "lakh": 10**5, "lakhs": 10**5, "lac": 10**5, "lacs": 10**5, "crore": 10**7,
               "crores": 10**7}.items():
    _EN[_w] = _W("big", _F(_v))

# ---- Hindi (Devanagari). 1–99 are single words; common spelling variants included. Keys are folded. ------

_HI_NUM = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "छह": 6, "छः": 6, "छे": 6, "छ": 6, "सात": 7, "आठ": 8,
    "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15, "सोलह": 16,
    "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20, "इक्कीस": 21, "बाईस": 22, "बाइस": 22, "तेईस": 23,
    "तेइस": 23, "चौबीस": 24, "पच्चीस": 25, "छब्बीस": 26, "सत्ताईस": 27, "अट्ठाईस": 28, "अठाईस": 28, "उनतीस": 29,
    "उन्तीस": 29, "तीस": 30, "इकतीस": 31, "इकत्तीस": 31, "बत्तीस": 32, "तैंतीस": 33, "तेंतीस": 33, "चौंतीस": 34,
    "चौतीस": 34, "पैंतीस": 35, "पैतीस": 35, "छत्तीस": 36, "सैंतीस": 37, "सेंतीस": 37, "अड़तीस": 38,
    "उनतालीस": 39, "उन्तालीस": 39, "चालीस": 40, "इकतालीस": 41, "बयालीस": 42, "बियालीस": 42, "तैंतालीस": 43,
    "तेतालीस": 43, "चवालीस": 44, "चौवालीस": 44, "पैंतालीस": 45, "छियालीस": 46, "सैंतालीस": 47, "अड़तालीस": 48,
    "उनचास": 49, "पचास": 50, "इक्यावन": 51, "इकावन": 51, "बावन": 52, "तिरपन": 53, "तिरेपन": 53, "चौवन": 54,
    "चव्वन": 54, "पचपन": 55, "छप्पन": 56, "सत्तावन": 57, "अट्ठावन": 58, "अठावन": 58, "उनसठ": 59, "साठ": 60,
    "इकसठ": 61, "बासठ": 62, "तिरसठ": 63, "तिरेसठ": 63, "चौंसठ": 64, "चौसठ": 64, "पैंसठ": 65, "पैसठ": 65,
    "छियासठ": 66, "सड़सठ": 67, "सरसठ": 67, "अड़सठ": 68, "उनहत्तर": 69, "सत्तर": 70, "इकहत्तर": 71,
    "बहत्तर": 72, "तिहत्तर": 73, "चौहत्तर": 74, "पचहत्तर": 75, "छिहत्तर": 76, "छियत्तर": 76, "सतहत्तर": 77,
    "अठहत्तर": 78, "उन्यासी": 79, "उनासी": 79, "अस्सी": 80, "इक्यासी": 81, "बयासी": 82, "बियासी": 82,
    "तिरासी": 83, "चौरासी": 84, "पचासी": 85, "छियासी": 86, "सत्तासी": 87, "अट्ठासी": 88, "अठासी": 88,
    "नवासी": 89, "नब्बे": 90, "इक्यानवे": 91, "इक्यानबे": 91, "बानवे": 92, "बानबे": 92, "तिरानवे": 93,
    "तिरानबे": 93, "चौरानवे": 94, "चौरानबे": 94, "पचानवे": 95, "पचानबे": 95, "छियानवे": 96, "छियानबे": 96,
    "सत्तानवे": 97, "सत्तानबे": 97, "अट्ठानवे": 98, "अठानवे": 98, "निन्यानवे": 99, "निन्यानबे": 99,
}
_HI: dict[str, _W] = {fold(k): _W("num", _F(v)) for k, v in _HI_NUM.items()}
_HI.update({fold(k): w for k, w in {
    "शून्य": _W("zero", _F(0)),
    "सौ": _W("hundred", _F(100)),
    "हज़ार": _W("big", _F(1000)), "लाख": _W("big", _F(10**5)), "करोड़": _W("big", _F(10**7)),
    "डेढ़": _W("frac", _F(3, 2)), "ढाई": _W("frac", _F(5, 2)),              # 1.5 and 2.5: only before सौ / हज़ार
    "साढ़े": _W("mod", _HALF), "सवा": _W("mod", _F(1, 4)), "पौने": _W("mod", _F(-1, 4)),   # +½, +¼, −¼
}.items()})

# ---- Tamil. Additive with combining forms: "இருபத்தி ஐந்து" = 20 + 5, "நூற்றி இருபது" = 100 + 20. --------------


def _ta_comb(word: str) -> list[str]:
    """Combining forms: இருபது → இருபத்து / இருபத்தி, நூறு → நூற்று / நூற்றி, ஆயிரம் → ஆயிரத்து / ஆயிரத்தி."""
    if word.endswith("ம்"):
        return [word[:-2] + "த்து", word[:-2] + "த்தி"]
    if word.endswith("ு"):
        stem = word[:-1] + "்" + word[-2]
        return [stem + "ு", stem + "ி"]
    return []


_TA_UNITS = {"ஒன்று": 1, "ஒண்ணு": 1, "இரண்டு": 2, "ரெண்டு": 2, "மூன்று": 3, "மூணு": 3, "நான்கு": 4, "நாலு": 4,
             "ஐந்து": 5, "அஞ்சு": 5, "ஆறு": 6, "ஏழு": 7, "எட்டு": 8, "ஒன்பது": 9, "ஒம்பது": 9}
_TA_TENS = {"பத்து": 10, "பதினொன்று": 11, "பதினொண்ணு": 11, "பன்னிரண்டு": 12, "பன்னெண்டு": 12, "பதிமூன்று": 13,
            "பதின்மூன்று": 13, "பதிமூணு": 13, "பதினான்கு": 14, "பதினாலு": 14, "பதினைந்து": 15, "பதினஞ்சு": 15,
            "பதினாறு": 16, "பதினேழு": 17, "பதினெட்டு": 18, "பத்தொன்பது": 19, "இருபது": 20, "முப்பது": 30,
            "நாற்பது": 40, "ஐம்பது": 50, "அம்பது": 50, "அறுபது": 60, "எழுபது": 70, "எண்பது": 80, "தொண்ணூறு": 90}
_TA_TENS_COMBINING = ("இருபது", "முப்பது", "நாற்பது", "ஐம்பது", "அம்பது", "அறுபது", "எழுபது", "எண்பது", "தொண்ணூறு")
_TA_HUNDREDS = {"நூறு": 100, "இருநூறு": 200, "முந்நூறு": 300, "முன்னூறு": 300, "நானூறு": 400, "நாநூறு": 400,
                "ஐநூறு": 500, "ஐந்நூறு": 500, "அறுநூறு": 600, "எழுநூறு": 700, "எண்ணூறு": 800, "தொள்ளாயிரம்": 900}
_TA_THOUSANDS = {"இரண்டாயிரம்": 2000, "ரெண்டாயிரம்": 2000, "மூவாயிரம்": 3000, "மூணாயிரம்": 3000,
                 "நான்காயிரம்": 4000, "நாலாயிரம்": 4000, "ஐயாயிரம்": 5000, "ஐந்தாயிரம்": 5000, "அஞ்சாயிரம்": 5000,
                 "ஆறாயிரம்": 6000, "ஏழாயிரம்": 7000, "எட்டாயிரம்": 8000, "ஒன்பதாயிரம்": 9000, "ஒம்பதாயிரம்": 9000,
                 "பத்தாயிரம்": 10000}
_TA_BIG = {"ஆயிரம்": 1000, "லட்சம்": 10**5, "இலட்சம்": 10**5}

_TA: dict[str, _W] = {}
for _w, _v in _TA_UNITS.items():
    _TA[_w] = _W("num", _F(_v), 1)
for _w, _v in _TA_TENS.items():
    _TA[_w] = _W("num", _F(_v), 10)
for _w in _TA_TENS_COMBINING:
    for _c in _ta_comb(_w):
        _TA[_c] = _W("comb", _F(_TA_TENS[_w]), 10)
for _table, _place in ((_TA_HUNDREDS, 100), (_TA_THOUSANDS, 1000)):
    for _w, _v in _table.items():
        _TA[_w] = _W("num", _F(_v), _place)
        for _c in _ta_comb(_w):
            _TA[_c] = _W("comb", _F(_v), _place)
for _w, _v in _TA_BIG.items():
    _TA[_w] = _W("big", _F(_v))
    for _c in _ta_comb(_w):
        _TA[_c] = _W("bigc", _F(_v))
# Spoken Tamil: "நூத்தி இருபது" (120) is how a soundbox and a shopkeeper say நூற்றி இருபது.
_TA.update({"நூத்தி": _W("comb", _F(100), 100), "நூத்து": _W("comb", _F(100), 100)})
_TA.update({"கோடி": _W("big", _F(10**7)), "கோடியே": _W("bigc", _F(10**7)),
            "அரை": _W("half", _HALF), "ஒரு": _W("one", _F(1))})
_TA = {fold(k): w for k, w in _TA.items()}

_POISON = _W("poison", _F(0))
# Tokens that start like a tens / hundreds / thousands number but do not parse are unreadable, never skipped:
# skipping "இருபத்தைஞ்சு" after "நூற்றி" would turn 125 into 100.
_TA_STEMS = tuple(fold(s) for s in (
    "இருபத", "முப்பத", "நாற்பத", "ஐம்பத", "அம்பத", "அறுபத", "எழுபத", "எண்பத", "தொண்ணூ", "நூற", "இருநூ",
    "முந்நூ", "முன்னூ", "நானூ", "நாநூ", "ஐநூ", "ஐந்நூ", "அறுநூ", "எழுநூ", "எண்ணூ", "தொள்ளாயிர", "ஆயிர",
    "லட்ச", "இலட்ச", "கோடி", "பதின", "பதிமூ", "பன்னி", "பன்னெ", "பத்தொ"))

# Sandhi for fused Tamil numbers ("இருபத்தைந்து" = இருபத்த் + ஐந்து, "நூற்றியிருபது" = நூற்றி + ய + இருபது).
_TA_VOWEL_SIGN = {"அ": "", "ஆ": "ா", "இ": "ி", "ஈ": "ீ", "உ": "ு", "ஊ": "ூ", "எ": "ெ", "ஏ": "ே", "ஐ": "ை",
                  "ஒ": "ொ", "ஓ": "ோ", "ஔ": "ௌ"}
_TA_HARD = "கசதப"
# piece start → allowed after which previous piece end
_FOLLOWS = {"start": {"normal"}, "bare": {"sign"}, "i": {"normal", "glide", "dbl"}, "u": {"normal", "dbl"},
            "other": {"normal"}}


def _end_type(s: str) -> str:
    return "i" if s.endswith("ி") else "u" if s.endswith("ு") else "other"


def _build_pieces() -> dict[str, list[tuple[_W, str, str]]]:
    pieces: dict[str, list[tuple[_W, str, str]]] = {}
    for word, w in _TA.items():
        if w.kind == "one":
            continue
        forms = [(word, _end_type(word))]
        if word.endswith(("ு", "ி")) and len(word) > 2:
            forms.append((word[:-1], "bare"))          # இருபத்தி → இருபத்த, waits for a vowel sign
        for surface, end in forms:
            pieces.setdefault(surface, []).append((w, "normal", end))
            head = surface[0]
            if head in _TA_VOWEL_SIGN:
                tail = _TA_VOWEL_SIGN[head] + surface[1:]
                pieces.setdefault(tail, []).append((w, "sign", end))           # ஐந்து → ைந்து
                pieces.setdefault("ய" + tail, []).append((w, "glide", end))   # இருபது → யிருபது
            elif head in _TA_HARD:
                pieces.setdefault(head + "்" + surface, []).append((w, "dbl", end))   # பத்து → ப்பத்து
    return pieces


_PIECES = _build_pieces()
_MAX_PIECE = max(len(s) for s in _PIECES)


@lru_cache(maxsize=4096)
def _segment_ta(tok: str) -> tuple[_W, ...] | None:
    """Split one fused Tamil token into number words, or None. Shortest split wins."""
    n = len(tok)
    states: list[dict[str, tuple[_W, ...]]] = [{} for _ in range(n + 1)]
    states[0]["start"] = ()
    for i in range(n):
        for prev, words in list(states[i].items()):
            for j in range(i + 1, min(n, i + _MAX_PIECE) + 1):
                for w, start, end in _PIECES.get(tok[i:j], ()):
                    if start not in _FOLLOWS[prev]:
                        continue
                    cand = words + (w,)
                    if end not in states[j] or len(cand) < len(states[j][end]):
                        states[j][end] = cand
    finals = [v for k, v in states[n].items() if k != "bare"]
    return min(finals, key=len) if finals else None


def _is_tamil(s: str) -> bool:
    return any("஀" <= ch <= "௿" for ch in s)


# ---- grammars -------------------------------------------------------------------------------------------

_AFTER = {   # en / hi: which previous element each kind may follow inside one number
    "unit": {None, "tens", "hundred", "big", "and"},
    "teen": {None, "hundred", "big", "and"},
    "tens": {None, "hundred", "big", "and"},
    "num": {None, "hundred", "big", "mod"},
    "frac": {None, "hundred", "big"},
    "mod": {None, "hundred", "big"},
    "hundred": {None, "unit", "teen", "num", "frac", "mod"},
    "big": {None, "unit", "teen", "tens", "num", "frac", "mod", "hundred"},
}


class _Mult:
    """One en / hi number: "two thousand five hundred", "साढ़े तीन सौ"."""

    def __init__(self) -> None:
        self.total = _F(0)        # committed thousand / lakh / crore groups
        self.cur = _F(0)          # the group below the last big multiplier
        self.last: str | None = None
        self.last_big: Fraction | None = None
        self.mod: Fraction | None = None
        self.started = False

    def accepts(self, w: _W) -> bool:
        if self.last not in _AFTER[w.kind]:
            return False
        if w.kind in ("hundred", "big"):
            if self.last == "mod" and self.mod == _HALF:      # "साढ़े सौ" is not a number
                return False
            if w.kind == "hundred" and self.cur >= 100:
                return False
            if w.kind == "big" and (self.cur >= w.value or (self.last_big is not None and w.value >= self.last_big)):
                return False
        return True

    def take(self, w: _W) -> None:
        self.started = True
        if w.kind == "mod":
            self.mod = w.value
        elif w.kind in ("hundred", "big"):
            base = self.cur or _F(1)
            if self.last == "mod":
                base, self.mod = 1 + self.mod, None
            if w.kind == "hundred":
                self.cur = base * 100
            else:
                self.total += base * w.value
                self.cur = _F(0)
                self.last_big = w.value
        else:
            v = w.value
            if self.mod is not None:
                v, self.mod = v + self.mod, None
            self.cur += v
        self.last = w.kind

    def value(self) -> Fraction | None:
        return None if self.last == "mod" else self.total + self.cur


def _parse_mult(ws: list[_W]) -> list[Fraction | None]:
    if len(ws) > 1 and any(w.kind == "zero" for w in ws):
        return [None]                                   # "one zero zero": digit-by-digit, not an amount
    out: list[Fraction | None] = []
    acc = _Mult()
    for i, w in enumerate(ws):
        if w.kind == "zero":
            out.append(_F(0))
            continue
        if w.kind == "and":
            nxt = ws[i + 1].kind if i + 1 < len(ws) else None
            if acc.last in ("hundred", "big") and nxt in ("unit", "teen", "tens"):
                acc.last = "and"                        # "one hundred and twenty"
                continue
            if acc.started:
                out.append(acc.value())                 # "sixty and eighty": two numbers
            acc = _Mult()
            continue
        if not acc.accepts(w):
            if acc.started:
                out.append(acc.value())
            acc = _Mult()
        acc.take(w)
    if acc.started:
        out.append(acc.value())
    return out


class _Ta:
    """One Tamil number: additive combining forms plus ஆயிரம் / லட்சம் / கோடி as multipliers."""

    def __init__(self) -> None:
        self.total = _F(0)
        self.cur = _F(0)
        self.cap = _F(0)          # while open, the next value must be below this
        self.open = False         # last word was a combining form and needs a continuation
        self.last: str | None = None
        self.last_place = 0
        self.last_big: Fraction | None = None
        self.started = False

    def _fits(self, v: Fraction) -> bool:
        return not self.started or (self.open and v < self.cap)

    def _multiplies(self, m: Fraction) -> bool:
        return (self.last in ("num", "half") and not self.open and 0 < self.cur < m
                and (self.last_big is None or m < self.last_big))

    def accepts(self, w: _W) -> bool:
        if w.kind in ("num", "comb"):
            return self._fits(w.value)
        if w.kind in ("big", "bigc"):
            return self._multiplies(w.value) or self._fits(w.value)
        if w.kind == "half":
            return not self.started or (self.last == "num" and self.last_place <= 10 and not self.open
                                        and self.cur.denominator == 1)
        return False

    def take(self, w: _W) -> None:
        if w.kind in ("big", "bigc"):
            if self._multiplies(w.value):
                self.total += self.cur * w.value       # மூன்று ஆயிரம், ஒன்றரை லட்சம்
                self.cur = _F(0)
            else:
                self.cur += w.value                    # ஆயிரம் on its own is 1000
            self.last_big = w.value
            self.open, self.cap = w.kind == "bigc", w.value
            self.last, self.last_place = "big", int(w.value)
        elif w.kind == "half":
            self.cur += _HALF
            self.open, self.last = False, "half"
        else:
            self.cur += w.value
            self.open, self.cap = w.kind == "comb", _F(w.place)
            self.last, self.last_place = w.kind, w.place
        self.started = True

    def value(self) -> Fraction | None:
        return None if self.open else self.total + self.cur


def _parse_ta(ws: list[_W]) -> list[Fraction | None]:
    if any(w.kind == "poison" for w in ws):
        return [None]
    # ஒரு is the article "a" unless a multiplier follows it ("ஒரு லட்சம்").
    kept: list[_W] = []
    for i, w in enumerate(ws):
        if w.kind == "one":
            if i + 1 < len(ws) and ws[i + 1].kind in ("big", "bigc"):
                kept.append(_W("num", _F(1), 1))
            continue
        kept.append(w)
    out: list[Fraction | None] = []
    acc = _Ta()
    for w in kept:
        if not acc.accepts(w):
            if acc.started:
                out.append(acc.value())
            acc = _Ta()
        acc.take(w)
    if acc.started:
        out.append(acc.value())
    return out


_PARSERS = {"en": _parse_mult, "hi": _parse_mult, "ta": _parse_ta}


def _classify(tok: str) -> tuple[str, list[_W]] | None:
    f = fold(tok)
    if f in _EN:
        return "en", [_EN[f]]
    if f in _HI:
        return "hi", [_HI[f]]
    if f in _TA:
        return "ta", [_TA[f]]
    if _is_tamil(f):
        seg = _segment_ta(f)
        if seg is not None:
            return "ta", list(seg)
        if f.startswith(_TA_STEMS):
            return "ta", [_POISON]
    return None


def word_amounts(text: str) -> list[Fraction | None]:
    """Every number-word phrase in `text`, in order; None for one that cannot be read exactly."""
    out: list[Fraction | None] = []
    run: list[_W] = []
    lang: str | None = None

    def flush() -> None:
        nonlocal run, lang
        if run:
            out.extend(_PARSERS[lang](run))
        run, lang = [], None

    for tok in tokens(text):
        c = _classify(tok)
        if c is None:
            flush()
            continue
        tok_lang, ws = c
        if ws[0].kind == "and" and lang != "en":
            flush()                                     # a stray "and" joins nothing
            continue
        if tok_lang != lang:
            flush()
        run.extend(ws)
        lang = tok_lang
    flush()
    return out


def amounts_in_words(text: str) -> list[int]:
    """Whole-number phrases only; fractional and unreadable phrases are left out."""
    return [int(v) for v in word_amounts(text) if v is not None and v.denominator == 1]
