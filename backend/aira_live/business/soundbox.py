"""Parse a payment-soundbox announcement heard in the Live input transcript (en / hi / ta).

Evidence rule: a UPI payment counts only when the soundbox says the money was RECEIVED, names exactly one
whole-rupee amount, and says nothing that turns it around (sent, debited, failed, not received, refund, a
request, paise). Anything else returns None, and the tool asks the owner instead of guessing.
"""
import re
from decimal import Decimal

from pydantic import BaseModel

from aira_live.business.numwords import fold, word_amounts
from aira_live.business.text import normalize_digits, tokens


class SoundboxEvent(BaseModel):
    amount: int
    provider: str | None


_RECEIVED_TOKENS = {"received", "recieved", "credited", "prapt", "mile", "mila", "jama"}
_RECEIVED_PARTS = ("प्राप्त", "मिले", "मिला", "जमा", "क्रेडिट",
                   "பெறப்பட்டது", "பெற்றுள்ளீர்கள்", "பெற்றது", "வரவு", "கிடைத்தது", "கிடைச்சது")
_NEGATIVE_TOKENS = {"sent", "send", "debited", "debit", "deducted", "failed", "failure", "declined",
                    "unsuccessful", "refund", "refunded", "reversed", "pending", "paid", "cancelled", "canceled",
                    "not", "request", "requested", "transferred", "nahi", "nahin", "bheje", "bheja"}
_NEGATIVE_PARTS = ("नहीं", "भेजे", "भेजा", "कटे", "कटा", "डेबिट", "असफल", "विफल", "फेल", "लंबित", "रद्द", "रिफंड",
                   "वापस", "अनुरोध",
                   "இல்லை", "வில்லை", "அனுப்ப", "தோல்வி", "நிலுவை", "திருப்பி", "டெபிட்", "பிடித்தம்",
                   "கோரிக்கை")
# What a person says, never a soundbox: a question, "my phone says", or a customer telling the owner "bhaiya, I sent
# it". A soundbox announces in the third person and never asks.
_PERSON_TOKENS = {"kya", "did", "i", "my", "me", "we", "our", "bhaiya", "bhai", "anna", "sir", "madam", "kar", "diye",
                  "diya"}
_PERSON_PARTS = ("phone says", "phone shows", "screen", "क्या", "मैंने", "मेरे", "मेरा", "भैया", "भाई", "कर दिए",
                 "कर दिया", "दिए", "दिया",
                 "அண்ணா", "நான்", "என்", "எனக்கு")
# "ரத்து" (cancelled) as a word of its own: inside "ஆயிரத்து" (thousand-and) it is part of a number.
_CANCELLED_TA = fold("ரத்து")
_FRACTION_TOKENS = {"paise", "paisa", "point", "dot", "half", "quarter"}
_FRACTION_PARTS = ("पैसे", "पैसा", "दशमलव", "आधा", "பைசா", "புள்ளி")

_PROVIDERS = (("phonepe", "PhonePe"), ("phone pe", "PhonePe"), ("paytm", "Paytm"), ("pay tm", "Paytm"),
              ("gpay", "Google Pay"), ("g pay", "Google Pay"), ("google pay", "Google Pay"),
              ("bharatpe", "BharatPe"), ("bharat pe", "BharatPe"), ("bhim", "BHIM"), ("amazon pay", "Amazon Pay"),
              ("पेटीएम", "Paytm"), ("फ़ोनपे", "PhonePe"), ("फोन पे", "PhonePe"), ("गूगल पे", "Google Pay"),
              ("भारतपे", "BharatPe"),
              ("போன்பே", "PhonePe"), ("போன் பே", "PhonePe"), ("பேடிஎம்", "Paytm"), ("பேட்டிஎம்", "Paytm"),
              ("கூகுள் பே", "Google Pay"), ("ஜிபே", "Google Pay"), ("பாரத்பே", "BharatPe"))
_PROVIDER_KEYS = [(tokens(fold(key)), name) for key, name in _PROVIDERS]
_RECEIVED_PARTS = tuple(fold(p) for p in _RECEIVED_PARTS)
_NEGATIVE_PARTS = tuple(fold(p) for p in _NEGATIVE_PARTS)
_FRACTION_PARTS = tuple(fold(p) for p in _FRACTION_PARTS)

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3}")    # 1,250 · 12,500 · 1,00,000


def _digit_amounts(text: str) -> list[Decimal]:
    """Digit amounts. "1,250" is one number; "60,80" is not a valid grouping, so it is two."""
    out: list[Decimal] = []
    for m in _NUM.finditer(text):
        whole, _, frac = m.group().partition(".")
        whole = whole.rstrip(",")
        parts = [whole] if "," not in whole or _GROUPED.fullmatch(whole) else [p for p in whole.split(",") if p]
        for i, part in enumerate(parts):
            s = part.replace(",", "")
            if frac and i == len(parts) - 1:
                s += "." + frac
            out.append(Decimal(s))
    return out


def _token_matches(tok: str, key: str) -> bool:
    # Indic scripts take case suffixes ("போன்பேயில்"), so a prefix match is enough there.
    return tok == key or (not key.isascii() and tok.startswith(key))


def _provider(toks: list[str]) -> str | None:
    found: set[str] = set()
    for key, name in _PROVIDER_KEYS:
        n = len(key)
        for i in range(len(toks) - n + 1):
            if toks[i:i + n - 1] == key[:-1] and _token_matches(toks[i + n - 1], key[-1]):
                found.add(name)
                break
    return found.pop() if len(found) == 1 else None


def _says(toks: set[str], text: str, words: set[str], parts: tuple[str, ...]) -> bool:
    return bool(toks & words) or any(p in text for p in parts)


def parse(transcript: str) -> SoundboxEvent | None:
    if not transcript or not transcript.strip():
        return None
    text = fold(normalize_digits(transcript)).lower()
    toks = tokens(text)
    tok_set = set(toks)
    if _says(tok_set, text, _NEGATIVE_TOKENS, _NEGATIVE_PARTS) or any(t.startswith(_CANCELLED_TA) for t in toks):
        return None                                     # sent / failed / not received / refund / request / cancelled
    if "?" in transcript or "？" in transcript or _says(tok_set, text, _PERSON_TOKENS, _PERSON_PARTS):
        return None                                     # a question or a person talking: not the soundbox
    if _says(tok_set, text, _FRACTION_TOKENS, _FRACTION_PARTS):
        return None                                     # paise: cannot match a whole-rupee bill exactly
    if not _says(tok_set, text, _RECEIVED_TOKENS, _RECEIVED_PARTS):
        return None
    digits = _digit_amounts(text)
    if any(d != d.to_integral_value() for d in digits):
        return None                                     # ₹60.50
    words = word_amounts(text)
    if any(v is None or v.denominator != 1 for v in words):
        return None                                     # unreadable or fractional number words
    amounts = {int(d) for d in digits} | {int(v) for v in words}
    amounts.discard(0)
    if len(amounts) != 1:
        return None                                     # none, or two different amounts
    return SoundboxEvent(amount=amounts.pop(), provider=_provider(toks))
