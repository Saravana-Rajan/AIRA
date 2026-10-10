"""Checks that money/stock numbers AIRA spoke actually came from tool results (spec §3 'model proposes').

Deterministic first (`check_numbers`); Tamil/Hindi number *words* are left to an independent model read
(`llm_verify`, role "verify"), which never blocks the shop when it fails.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from aira_live.i18n import t

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

CORRECTION_FALLBACK = (
    "Correction needed: you said {wrong}, which is not in any tool result. "
    "Briefly correct yourself using only the numbers from the tool results."
)


@dataclass
class SafetyVerdict:
    ok: bool
    unexpected: list[Num] = field(default_factory=list)
    needs_llm: bool = False


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


def numbers_in_results(results) -> set[Num]:
    nums: set[Num] = set()

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            nums.add(_norm(v))
        elif isinstance(v, str):
            for m in re.findall(_NUM, _normalise(v)):
                nums.add(_parse(m))
        elif isinstance(v, dict):
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
    except Exception as exc:  # never block the shop on a verifier outage; the deterministic check already ran
        log.warning("safety.llm_verify failed: %r", exc)
        return SafetyVerdict(ok=True)
    allowed = numbers_in_results(results)
    wrong = sorted({n for n in named if n not in allowed})
    if raw:  # the verifier named numbers: trust only the ones that really are missing from the results
        return SafetyVerdict(ok=not wrong, unexpected=wrong)
    return SafetyVerdict(ok=consistent)


def correction_text(unexpected: list[Num], lang: str) -> str:
    """Instruction sent to the model (English in every catalog). Falls back if the i18n key is missing."""
    wrong = ", ".join(str(n) for n in unexpected) or "a number"
    text = t("safety.correction", lang, wrong=wrong)
    return CORRECTION_FALLBACK.format(wrong=wrong) if text == "safety.correction" else text
