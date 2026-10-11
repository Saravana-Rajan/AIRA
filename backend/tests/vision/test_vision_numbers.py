from decimal import Decimal

from aira_live.vision.numbers import normalize_amount, parse_qty, rupees, to_ascii_digits

_UNIT_SET = {"L", "ml", "pcs", "pack", "box", "kg", "g", ""}


def test_to_ascii_digits_tamil_and_devanagari():
    assert to_ascii_digits("௩௦ L") == "30 L"
    assert to_ascii_digits("३२०") == "320"


def test_normalize_amount_indian_formats():
    assert normalize_amount("₹1,00,000") == Decimal("100000")
    assert normalize_amount("Rs. 45.50/-") == Decimal("45.50")
    assert normalize_amount("INR 1,250") == Decimal("1250")
    assert normalize_amount("௩௦") == Decimal("30")
    assert normalize_amount("३२०") == Decimal("320")
    assert normalize_amount(990) == Decimal("990")
    assert normalize_amount(45.5) == Decimal("45.5")


def test_normalize_amount_local_rupee_marks_and_paise():
    assert normalize_amount("ரூ. 1,470") == Decimal("1470")
    assert normalize_amount("रु ३२०") == Decimal("320")
    assert normalize_amount("Rs.1,470.00") == Decimal("1470")
    assert normalize_amount("1,470/-") == Decimal("1470")
    assert normalize_amount(Decimal("33")) == Decimal("33")


def test_parse_qty_numeric_input_uses_hint():
    assert parse_qty(30, "Ltr.") == (Decimal("30"), "L")
    assert parse_qty(1.5, "kg") == (Decimal("1.5"), "kg")
    assert parse_qty(True, "L") == (None, "L")


def test_normalize_amount_rejects_garbage():
    assert normalize_amount(None) is None
    assert normalize_amount("") is None
    assert normalize_amount("total") is None
    assert normalize_amount(True) is None
    assert normalize_amount(float("nan")) is None
    assert normalize_amount(float("inf")) is None


def test_parse_qty_units():
    assert parse_qty("30 Ltr") == (Decimal("30"), "L")
    assert parse_qty("500ml") == (Decimal("500"), "ml")
    assert parse_qty("12", "pkt") == (Decimal("12"), "pack")
    assert parse_qty("௩௦ L") == (Decimal("30"), "L")
    assert parse_qty("2 boxes") == (Decimal("2"), "box")
    assert parse_qty(None, "L") == (None, "L")
    assert parse_qty("abc") == (None, "")


def test_parse_qty_unit_always_in_known_set():
    for value, hint in [("3 bottles", ""), ("3 bottles", "kg"), ("4", "crates"), ("2 kgs", ""), ("250 gms", "")]:
        _, unit = parse_qty(value, hint)
        assert unit in _UNIT_SET
    assert parse_qty("3 bottles", "kg") == (Decimal("3"), "kg")
    assert parse_qty("2 kgs") == (Decimal("2"), "kg")
    assert parse_qty("250 gms") == (Decimal("250"), "g")


def test_rupees_rounds_half_up():
    assert rupees(Decimal("45.5")) == 46
    assert rupees(Decimal("45.49")) == 45
    assert rupees(None) is None
