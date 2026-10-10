"""Engine outcome codes -> i18n keys, spoken only through aira_live.i18n.t (en, ta and hi)."""
import json
import re
from pathlib import Path

import pytest

from aira_live.business import hints

I18N = Path(__file__).resolve().parents[2] / "aira_live" / "i18n"
LANGS = ("en", "ta", "hi")
DISCREPANCY_KINDS = ["SHORT_DELIVERY", "OVER_DELIVERY", "UNORDERED_ITEM", "INVOICE_UNREADABLE",
                     "UNKNOWN_INVOICE_LINE", "INVOICE_QTY_MISMATCH", "PRICE_MISMATCH", "LINE_TOTAL_MISMATCH",
                     "TOTAL_MISMATCH"]
ENGINE_CODES = ["InsufficientStock", "InsufficientTender", "PaymentMismatch", "InvalidTransition",
                "PRODUCT_AMBIGUOUS", "PRODUCT_UNKNOWN", "SUPPLIER_UNKNOWN", "SOUNDBOX_UNSURE"]
# Every placeholder the biz.* keys may use (plan: Task 6 interfaces).
ALL_KW = {"product": "Sakthi Curd 1 L", "have": 3, "short": 20, "expected": 30, "actual": 28,
          "name": "ARUN ICE CREAM", "options": "500 ml, 1 L"}
PLACEHOLDER = re.compile(r"{(\w+)}")


def _flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flat(v, key + "."))
        else:
            out[key] = v
    return out


def _catalog(lang: str) -> dict:
    return _flat(json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8")))


def test_every_hint_key_in_all_languages():
    for lang in LANGS:
        catalog = _catalog(lang)
        missing = [k for k in hints.HINTS.values() if k not in catalog]
        assert not missing, f"{lang} missing {missing}"


def test_every_code_has_a_hint():
    assert set(DISCREPANCY_KINDS + ENGINE_CODES) <= set(hints.HINTS)
    assert all(v.startswith("biz.") for v in hints.HINTS.values())


def test_placeholders_match_across_languages():
    en = _catalog("en")
    for lang in ("ta", "hi"):
        cat = _catalog(lang)
        for key in hints.HINTS.values():
            assert set(PLACEHOLDER.findall(cat[key])) == set(PLACEHOLDER.findall(en[key])), f"{lang} {key}"


def test_placeholders_are_only_the_documented_ones():
    for lang in LANGS:
        cat = _catalog(lang)
        for key in hints.HINTS.values():
            assert set(PLACEHOLDER.findall(cat[key])) <= set(ALL_KW), f"{lang} {key}"


@pytest.mark.parametrize("lang", LANGS)
def test_every_hint_renders_fully(lang):
    for code in hints.HINTS:
        s = hints.say(code, lang, **ALL_KW)
        assert s and "{" not in s and "}" not in s and not s.startswith("biz."), f"{lang} {code}: {s}"


def test_say_formats_through_i18n():
    s = hints.say("SHORT_DELIVERY", "en", product="Sakthi Curd 1 L", expected=30, actual=28)
    assert s == "Sakthi Curd 1 L: ordered 30, counted 28. Short delivery."
    assert hints.key_for("INVOICE_UNREADABLE") == "biz.invoice.unreadable"


def test_say_in_tamil_and_hindi():
    assert hints.say("InsufficientTender", "ta", short=20) == "20 ரூபாய் குறைவாக உள்ளது."
    assert hints.say("PRODUCT_AMBIGUOUS", "hi", options="500 ml, 1 L") == "कौन सा: 500 ml, 1 L?"


def test_extra_keywords_are_ignored():
    # Tools pass what they have (e.g. supplier=..., product=...) even when the sentence does not use it.
    assert hints.say("SUPPLIER_UNKNOWN", "en", supplier="xyz traders") == "I don't know that vendor."
    assert hints.say("PRODUCT_UNKNOWN", "en", product="biscuit") == "I don't know that product."


def test_say_is_a_thin_wrapper_over_i18n_t(monkeypatch):
    from aira_live import i18n

    seen = []

    def fake(key, lang, **kw):
        seen.append((key, lang, kw))
        return "ok"

    monkeypatch.setattr(i18n, "t", fake)
    assert hints.say("TOTAL_MISMATCH", "ta", expected=1500, actual=1700) == "ok"
    assert seen == [("biz.invoice.total_mismatch", "ta", {"expected": 1500, "actual": 1700})]


def test_unknown_code_raises():
    with pytest.raises(KeyError):
        hints.key_for("NOT_A_CODE")
    with pytest.raises(KeyError):
        hints.say("NOT_A_CODE", "en")
