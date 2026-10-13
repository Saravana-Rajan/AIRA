"""Engine outcome codes -> facts (code + values + a short English meaning). Live phrases them for the owner."""
import pytest

from aira_live.business import hints

DISCREPANCY_KINDS = ["SHORT_DELIVERY", "OVER_DELIVERY", "UNORDERED_ITEM", "INVOICE_UNREADABLE",
                     "UNKNOWN_INVOICE_LINE", "INVOICE_QTY_MISMATCH", "PRICE_MISMATCH", "LINE_TOTAL_MISMATCH",
                     "TOTAL_MISMATCH"]
ENGINE_CODES = ["InsufficientStock", "InsufficientTender", "PaymentMismatch", "InvalidTransition",
                "PRODUCT_AMBIGUOUS", "PRODUCT_UNKNOWN", "SUPPLIER_UNKNOWN", "SOUNDBOX_UNSURE"]
ALL_KW = {"product": "Sakthi Curd 1 L", "have": 3, "short": 20, "expected": 30, "actual": 28,
          "name": "ARUN ICE CREAM", "options": "500 ml, 1 L"}


def test_every_code_has_a_meaning():
    assert set(DISCREPANCY_KINDS + ENGINE_CODES) <= set(hints.HINTS)


def test_every_meaning_renders_fully():
    for code in hints.HINTS:
        s = hints.english(code, **ALL_KW)
        assert s and "{" not in s and "}" not in s, f"{code}: {s}"


def test_fact_keeps_every_value_exact():
    r = hints.fact("SHORT_DELIVERY", product="Sakthi Curd 1 L", expected=30, actual=28)
    assert r["ok"] is False and r["error"] == "SHORT_DELIVERY"
    assert r["product"] == "Sakthi Curd 1 L" and r["expected"] == 30 and r["actual"] == 28
    assert r["say_en"] == "Sakthi Curd 1 L: ordered 30, counted 28. Short delivery."


def test_money_stays_a_number():
    r = hints.fact("InsufficientTender", short=20)
    assert r["short"] == 20 and "20" in r["say_en"]


def test_extra_keywords_are_kept_as_facts():
    r = hints.fact("SUPPLIER_UNKNOWN", supplier="xyz traders")
    assert r["supplier"] == "xyz traders" and r["say_en"] == "That vendor is not known."


def test_no_fixed_translations():
    import json

    assert all(isinstance(v, str) for v in hints.HINTS.values())
    assert not any(ord(c) > 0x2000 for v in hints.HINTS.values() for c in v if c != "₹")
    json.dumps(hints.fact("PRODUCT_AMBIGUOUS", options="500 ml, 1 L"))  # a plain JSON tool result


def test_unknown_code_raises():
    with pytest.raises(KeyError):
        hints.english("NOT_A_CODE")
    with pytest.raises(KeyError):
        hints.fact("NOT_A_CODE")
