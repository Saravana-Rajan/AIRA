"""Checks that money/stock numbers AIRA spoke actually came from tool results (spec §3 'model proposes').

Deterministic first (`check_numbers`); Tamil/Hindi number *words* are left to an independent model read
(`llm_verify`, role "verify"). When that verifier fails it blocks only safety-critical claims (a money amount,
or an unhedged object count no tool result supports); everything else passes.

Object counts (`object_counts`, `check_turn`) are checked on every turn, also turns with no tool (an answer
from the live video): a hedged estimate ("about 7 bottles") is allowed, an exact count needs a tool result.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field


log = logging.getLogger(__name__)

Num = int | float

_DIGITS = str.maketrans("௦௧௨௩௪௫௬௭௮௯०१२३४५६७८९", "01234567890123456789")
# Units that make a number a money or stock number. Longer forms come first in each alternation.
_UNIT = (
    r"₹|rs\.?|rupees?|inr|litres?|liters?|ltrs?|l|packets?|packs?|pouch(?:es)?|boxes|box|jars?|"
    r"ரூபாய்|ரூ\.?|லிட்டர்|பாக்கெட்|रुपये|रुपए|रुपया|रु\.?|लीटर|पैकेट"
)
# A unit is only a unit when it is not glued to Latin letters ("until", "hours", "packed" are not units).
_UNIT_RE = rf"(?<![a-z])(?:{_UNIT})(?![a-z])"
_NUM = r"\d+(?:,\d{2,3})*(?:\.\d+)?"
_PAT = re.compile(rf"(?:{_UNIT_RE}\s*({_NUM}))|(?:({_NUM})\s*{_UNIT_RE})", re.IGNORECASE)
_HAS_UNIT = re.compile(_UNIT_RE, re.IGNORECASE)
_INDIC_LETTERS = re.compile(r"[஀-௿ऀ-ॿ]")
_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"thousand": 1_000, "lakh": 100_000, "lakhs": 100_000}

# Currency units only: a number next to one of these is a money amount.
_MONEY = r"₹|rs\.?|rupees?|inr|ரூபாய்|ரூ\.?|रुपये|रुपए|रुपया|रु\.?"
_MONEY_RE = re.compile(rf"(?<![a-z])(?:{_MONEY})(?![a-z])", re.IGNORECASE)
_MONEY_PAT = re.compile(rf"(?:(?<![a-z])(?:{_MONEY})(?![a-z])\s*({_NUM}))|(?:({_NUM})\s*(?<![a-z])(?:{_MONEY})(?![a-z]))",
                        re.IGNORECASE)
# Nouns that make a number a count of objects. Latin ones must end the word; Tamil/Hindi ones may take suffixes.
_COUNT_NOUN = (
    r"(?:packets?|packs?|pouch(?:es)?|cartons?|bottles?|boxes|box|items?|cups?|tubs?|jars?|cans?|tins?|bags?|"
    r"pieces?|sachets?|bricks?|blocks?|crates?|trays?|tetra ?packs?|products?|things?|objects?)(?![a-z])"
    r"|பாக்கெட்|பாட்டில்|டப்பா|பெட்டி|கப்|பை|पैकेट|बोतल|डिब्बा|डिब्बे|पैक|थैली|कप"
)
# number, then up to three words (no digits), then a count noun: "7 bottles", "12 Aavin curd packets".
_COUNT_PAT = re.compile(
    rf"(?<![\d.,₹\w])({_NUM})[ \t]+((?:[^\s\d.,!?।]+[ \t]+){{0,3}}?)(?:{_COUNT_NOUN})", re.IGNORECASE)
# Words between the number and the noun that make it a size or price, not a count ("500 ml pouch").
_NOT_COUNT_WORDS = {
    "ml", "l", "ltr", "ltrs", "litre", "litres", "liter", "liters", "g", "gm", "gms", "gram", "grams", "kg", "kgs",
    "rupee", "rupees", "rs", "inr", "per", "a", "an", "each", "every", "for", "of",
}
# Words that mark a count as an estimate (English, Tamil, Hindi).
_HEDGE = re.compile(
    r"(?<![a-z])(?:about|around|approximately|approx|roughly|nearly|almost|maybe|perhaps|probably|i think|"
    r"looks like|seems?|estimate[ds]?|more or less|close to|or so)(?![a-z])|சுமார்|கிட்டத்தட்ட|लगभग|करीब|तकरीबन",
    re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?।\n](?:\s|$)")

CORRECTION_FALLBACK = (
    "Correction needed: you said {wrong}, which is not in any tool result. "
    "Briefly correct yourself using only the numbers from the tool results."
)
CORRECTION_UNVERIFIED = (
    "Correction needed: the amount or count you just said could not be verified. In one short sentence, say "
    "you are not sure of it, and repeat a number only if it came from a tool result."
)


@dataclass
class SafetyVerdict:
    ok: bool
    unexpected: list[Num] = field(default_factory=list)
    needs_llm: bool = False
    unverified: bool = False  # the verifier was down: the claim is blocked because it could not be checked


def _norm(x: float) -> Num:
    """Integers stay ints; other values keep two decimals (so 2.5 L is not mistaken for 2 L)."""
    x = float(x)
    return int(x) if x.is_integer() else round(x, 2)


def _parse(raw: str) -> Num:
    return _norm(float(raw.replace(",", "")))


def _words_to_digits(text: str) -> str:
    """English number words → digits ("six hundred rupees" → "600 rupees")."""
    out, cur, total, in_num = [], 0, 0, False
    for tok in re.split(r"(\s+|-)", text):
        w = tok.lower().strip(" ,.")
        if w in _ONES or w in _TENS or w == "hundred" or w in _SCALES:
            in_num = True
            if w in _ONES:
                cur += _ONES[w]
            elif w in _TENS:
                cur += _TENS[w]
            elif w == "hundred":
                cur = max(cur, 1) * 100
            else:
                total += max(cur, 1) * _SCALES[w]
                cur = 0
            continue
        if tok.strip() == "" or tok == "-":
            if not in_num:
                out.append(tok)
            continue
        if in_num:
            out.append(f"{total + cur} ")
            cur, total, in_num = 0, 0, False
        out.append(tok)
    if in_num:
        out.append(f" {total + cur}")
    return "".join(out)


def _normalise(text: str) -> str:
    return _words_to_digits(text.translate(_DIGITS))


def money_stock_numbers(text: str) -> set[Num]:
    return {_parse(a or b) for a, b in _PAT.findall(_normalise(text))}


def is_external(result) -> bool:
    """A tool result marked ``external`` (web_search) is information from outside the shop: its numbers are not
    tool-verified business numbers and never whitelist a spoken price, amount or stock count."""
    return isinstance(result, dict) and bool(result.get("external"))


def verified_results(results) -> list:
    """``results`` without the external ones (the only numbers the safety checks may allow)."""
    return [r for r in (results or []) if not is_external(r)]


def numbers_in_results(results) -> set[Num]:
    nums: set[Num] = set()
    if is_external(results):
        return nums

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            nums.add(_norm(v))
        elif isinstance(v, str):
            for m in re.findall(_NUM, _normalise(v)):
                nums.add(_parse(m))
        elif isinstance(v, dict):
            if is_external(v):
                return
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple, set)):
            for x in v:
                walk(x)

    walk(results)
    return nums


def check_numbers(spoken: str, results: list[dict]) -> SafetyVerdict:
    norm = _normalise(spoken)
    matches = _PAT.findall(norm)
    spoken_nums = {_parse(a or b) for a, b in matches}
    # Tamil/Hindi number words next to a unit cannot be parsed here: hand them to the model verifier.
    unparsed_units = len(_HAS_UNIT.findall(norm)) > len(matches)
    needs_llm = bool(_INDIC_LETTERS.search(spoken)) and unparsed_units
    if not spoken_nums:
        return SafetyVerdict(ok=True, needs_llm=needs_llm)
    allowed = numbers_in_results(results)
    unexpected = sorted(n for n in spoken_nums if n not in allowed)
    return SafetyVerdict(ok=not unexpected, unexpected=unexpected, needs_llm=needs_llm and not unexpected)


# --- object counts ------------------------------------------------------------------------------------------
def _hedged(text: str, start: int, end: int) -> bool:
    """A hedge word in the same sentence before the number, or "or so" right after the counted noun."""
    before = text[:start]
    cut = max((m.end() for m in _SENTENCE_END.finditer(before)), default=0)
    if _HEDGE.search(before[cut:]):
        return True
    return bool(re.match(r"\s*(?:or so|roughly|approximately)(?![a-z])", text[end:], re.IGNORECASE))


def object_counts(text: str) -> list[tuple[Num, bool]]:
    """``(count, hedged)`` for every count of objects stated in ``text`` ("7 bottles", "about 12 curd packets").

    Not counts: times, steps, distances, dates, phone numbers, ordinals ("2nd"), prices ("₹60 per packet"),
    sizes ("500 ml pouch") and a single object ("one curd packet" identifies, it does not count).
    Tamil/Hindi number *words* are not parsed here (digits in any script are).
    """
    norm = _normalise(text)
    out: list[tuple[Num, bool]] = []
    for m in _COUNT_PAT.finditer(norm):
        between = [w.strip(" ,").lower() for w in m.group(2).split()]
        if any(w in _NOT_COUNT_WORDS for w in between):
            continue
        if re.search(rf"(?:{_MONEY})\s*$", norm[:m.start()], re.IGNORECASE):
            continue  # "₹60 packet": a price
        n = _parse(m.group(1))
        if n <= 1:
            continue
        out.append((n, _hedged(norm, m.start(), m.end())))
    return out


def states_object_count(text: str) -> bool:
    return bool(object_counts(text))


def _is_estimate(r) -> bool:
    """A count result that is only an estimate: approx, not exact, or not verified."""
    return isinstance(r, dict) and "count" in r and (
        bool(r.get("approx")) or r.get("exact") is False or r.get("verified") is False)


def _count_problems(spoken: str, current, results) -> tuple[list[Num], set[Num]]:
    """(unsupported counts, hedged counts that are fine). An unhedged count must be in a non-estimate result;
    a hedged one may be any result's number, or a video estimate unless this turn's camera count disagrees."""
    results = verified_results(results)
    exact_ok = numbers_in_results([r for r in results if not _is_estimate(r)])
    any_ok = numbers_in_results(results)
    count_now = any(isinstance(r, dict) and "count" in r for r in verified_results(current))
    bad: list[Num] = []
    fine: set[Num] = set()
    for n, hedged in object_counts(spoken):
        if hedged and (n in any_ok or not count_now):
            fine.add(n)
        elif not (not hedged and n in exact_ok):
            bad.append(n)
    return bad, fine


def is_critical(spoken: str, results) -> bool:
    """Safety-critical claims (blocked when they cannot be verified): any money amount, or an unhedged object
    count that no tool result supports. Descriptions, sizes and estimates are not critical."""
    if _MONEY_RE.search(_normalise(spoken)):
        return True
    exact_ok = numbers_in_results([r for r in verified_results(results) if not _is_estimate(r)])
    return any(not hedged and n not in exact_ok for n, hedged in object_counts(spoken))


def check_turn(spoken: str, current: list[dict], results: list[dict]) -> SafetyVerdict:
    """The turn-end check. ``current`` = this turn's tool results (empty: an answer from the video or memory);
    ``results`` = everything that may back a number (current + recent turns + catalog facts).

    Tool turns: money/stock numbers must come from ``results`` (``check_numbers``). Every turn: object counts
    (``object_counts``) are checked; a hedged estimate passes unless this turn's camera count contradicts it.
    """
    bad, hedged_fine = _count_problems(spoken, current, results)
    verdict = check_numbers(spoken, results) if current else SafetyVerdict(ok=True)
    money = {_parse(a or b) for a, b in _MONEY_PAT.findall(_normalise(spoken))}
    # a hedged video estimate ("about 7 packets") is not a stock number, unless the same number is also money
    unexpected = sorted({n for n in verdict.unexpected if n not in hedged_fine or n in money} | set(bad))
    return SafetyVerdict(ok=not unexpected, unexpected=unexpected, needs_llm=verdict.needs_llm and not unexpected)


async def generate_json(*args, **kwargs) -> dict:  # lazy shim → Sarmitha's aira_live.vision.gemini
    from aira_live.vision.gemini import generate_json as _gen

    return await _gen(*args, **kwargs)


def _as_num(x) -> Num | None:
    if isinstance(x, bool) or x is None:
        return None
    if isinstance(x, (int, float)):
        return _norm(x)
    m = re.search(_NUM, str(x).translate(_DIGITS))
    return _parse(m.group(0)) if m else None


async def llm_verify(spoken: str, results: list[dict]) -> SafetyVerdict:
    results = verified_results(results)  # web_search text is external: never an allowed number
    shown = json.dumps(results, ensure_ascii=False, default=str)
    prompt = (
        "A shop assistant said this to a blind shopkeeper:\n"
        f"SPOKEN: {spoken}\nTOOL RESULTS (the only allowed numbers): {shown}\n"
        "Do the money, quantity and stock numbers in SPOKEN (they may be written as Tamil or Hindi words) all "
        "match numbers in TOOL RESULTS? List any spoken numbers that do not match, as integers."
    )
    try:
        out = await generate_json("verify", [prompt], '{"consistent": bool, "wrong_numbers": [int]}',
                                  thinking="low", timeout_s=3.0)
        consistent = bool(out.get("consistent", True))
        raw = out.get("wrong_numbers") or []
        raw = raw if isinstance(raw, list) else [raw]
        named = [n for n in (_as_num(x) for x in raw) if n is not None]
    except Exception as exc:  # fail closed only for money amounts / unsupported counts; never block descriptions
        critical = is_critical(spoken, results)
        log.warning("safety.llm_verify failed (critical=%s): %r", critical, exc)
        return SafetyVerdict(ok=not critical, unverified=critical)
    allowed = numbers_in_results(results)
    wrong = sorted({n for n in named if n not in allowed})
    if raw:  # the verifier named numbers: trust only the ones that really are missing from the results
        return SafetyVerdict(ok=not wrong, unexpected=wrong)
    return SafetyVerdict(ok=consistent)


def correction_text(unexpected: list[Num], lang: str | None = None, unverified: bool = False) -> str:
    """The instruction sent to the model (always English; the model corrects itself in the owner's language)."""
    if unverified and not unexpected:
        return CORRECTION_UNVERIFIED
    wrong = ", ".join(str(n) for n in unexpected) or "a number"
    return CORRECTION_FALLBACK.format(wrong=wrong)
