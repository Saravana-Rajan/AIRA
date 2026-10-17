from decimal import Decimal

from aira_live.vision.invoice_merge import ParsedLine, arithmetic_issues, merge_reads
from aira_live.vision.invoice_models import InvoiceLine, InvoiceRead

CURD = {"name": "Sakthi Curd 1L", "qty": "30 L", "unit": "L", "unit_price": "₹33", "line_total": "₹990"}
MILK = {"name": "Aavin Milk 500ml", "qty": "20", "unit": "pack", "unit_price": "Rs. 24", "line_total": "480"}


def read(lines, total="₹1,470", supplier="Sakthi Dairy", date="2026-10-10", handwritten=False):
    return {"supplier": supplier, "date": date, "handwritten": handwritten, "lines": lines, "total": total}


def test_identical_reads_agree_and_convert_types():
    r = merge_reads(read([CURD, MILK]), read([CURD, MILK]))
    assert isinstance(r, InvoiceRead) and all(isinstance(ln, InvoiceLine) for ln in r.lines)
    assert r.agreed is True
    assert r.disagreements == []
    assert r.supplier == "Sakthi Dairy" and r.date == "2026-10-10"
    assert r.total == 1470
    assert r.lines[0].name == "Sakthi Curd 1L"
    assert r.lines[0].qty == 30.0 and r.lines[0].unit == "L"
    assert r.lines[0].unit_price == 33 and r.lines[0].line_total == 990


def test_lines_in_different_order_still_agree():
    r = merge_reads(read([CURD, MILK]), read([MILK, CURD]))
    assert r.agreed is True, r.disagreements
    assert [ln.name for ln in r.lines] == ["Sakthi Curd 1L", "Aavin Milk 500ml"]


def test_supplier_case_and_punctuation_ignored():
    r = merge_reads(read([CURD, MILK]), read([CURD, MILK], supplier="SAKTHI DAIRY."))
    assert r.agreed is True, r.disagreements


def test_different_supplier_reported():
    r = merge_reads(read([CURD, MILK]), read([CURD, MILK], supplier="Aavin Agency"))
    assert r.agreed is False
    assert any(d.startswith("supplier: ") for d in r.disagreements), r.disagreements


def test_date_disagreement_reported():
    r = merge_reads(read([CURD, MILK]), read([CURD, MILK], date="2026-10-01"))
    assert r.agreed is False
    assert any(d.startswith("date: ") for d in r.disagreements), r.disagreements


def test_date_in_indic_digits_or_indian_order_matches_iso():
    # Read B's prompt allows Tamil/Hindi digits; Indian bills print DD/MM/YYYY.
    for b_date in ("௧௦/௧௦/௨௦௨௬", "10-10-2026", "10.10.26", "१०/१०/२०२६"):
        r = merge_reads(read([CURD, MILK]), read([CURD, MILK], date=b_date))
        assert r.agreed is True, (b_date, r.disagreements)
    r = merge_reads(read([CURD, MILK], date="௨௦௨௬-௧௦-௧௦"), read([CURD, MILK]))
    assert r.agreed is True, r.disagreements
    assert r.date == "2026-10-10"


def test_unparseable_date_kept_as_written_and_compared():
    r = merge_reads(read([CURD, MILK], date="Oct 10"), read([CURD, MILK], date="Oct 10"))
    assert r.agreed is True, r.disagreements
    assert r.date == "Oct 10"
    r = merge_reads(read([CURD, MILK], date="31/02/2026"), read([CURD, MILK], date="2026-02-28"))
    assert r.agreed is False
    assert any(d.startswith("date: ") for d in r.disagreements), r.disagreements


def test_line_pairing_prefers_best_match_over_read_order():
    milk = {"name": "Aavin Milk 1L", "qty": "10", "unit": "pack", "unit_price": "44", "line_total": "440"}
    toned = {"name": "Aavin Milk 1L Toned", "qty": "5", "unit": "pack", "unit_price": "40", "line_total": "200"}
    r = merge_reads(read([milk, toned], total="640"), read([toned], total="640"))
    assert r.agreed is False
    assert "line Aavin Milk 1L: only in one read" in r.disagreements, r.disagreements
    assert not any(d.startswith("line 1 ") or d.startswith("line 2 ") for d in r.disagreements), r.disagreements
    assert [ln.name for ln in r.lines] == ["Aavin Milk 1L", "Aavin Milk 1L Toned"]


def test_qty_disagreement_is_reported_precisely():
    curd_b = dict(CURD, qty="32 L")
    r = merge_reads(read([CURD, MILK]), read([curd_b, MILK]))
    assert r.agreed is False
    assert any(d.startswith("line 1 Sakthi Curd 1L qty: 30 vs 32") for d in r.disagreements), r.disagreements


def test_unit_disagreement_is_reported():
    curd_b = dict(CURD, qty="30", unit="pack")
    r = merge_reads(read([CURD, MILK]), read([curd_b, MILK]))
    assert r.agreed is False
    assert "line 1 Sakthi Curd 1L unit: L vs pack" in r.disagreements, r.disagreements


def test_unit_missing_in_one_read_is_not_a_disagreement():
    curd_b = dict(CURD, qty="30", unit="")
    r = merge_reads(read([CURD, MILK]), read([curd_b, MILK]))
    assert r.agreed is True, r.disagreements
    assert r.lines[0].unit == "L"


def test_total_disagreement_between_reads():
    r = merge_reads(read([CURD, MILK]), read([CURD, MILK], total="₹1,740"))
    assert r.agreed is False
    assert "total: 1470 vs 1740" in r.disagreements


def test_total_missing_in_both_reads_never_agrees():
    r = merge_reads(read([CURD, MILK], total=None), read([CURD, MILK], total=""))
    assert r.agreed is False
    assert r.total is None
    assert "total: unreadable" in r.disagreements


def test_arithmetic_error_flagged_even_when_reads_agree():
    bad = dict(CURD, line_total="₹1,000")  # 30 x 33 = 990, bill says 1000
    r = merge_reads(read([bad, MILK], total="₹1,480"), read([bad, MILK], total="₹1,480"))
    assert r.agreed is False
    assert any(d.startswith("arith: line 1") for d in r.disagreements), r.disagreements


def test_total_not_equal_to_sum_of_lines_flagged():
    r = merge_reads(read([CURD, MILK], total="₹1,570"), read([CURD, MILK], total="₹1,570"))
    assert r.agreed is False
    assert any(d.startswith("arith: lines sum to 1470") for d in r.disagreements), r.disagreements


def test_rupee_rounding_within_tolerance_is_not_an_error():
    cone = {"name": "Arun Cone", "qty": "3", "unit": "pcs", "unit_price": "Rs. 33", "line_total": "100"}
    r = merge_reads(read([cone], total="100"), read([cone], total="100"))  # 3 x 33 = 99, bill rounds to 100
    assert r.agreed is True, r.disagreements


def test_paise_unit_price_is_rounded_but_never_presented_as_agreed():
    cone = {"name": "Arun Cone", "qty": "3", "unit": "pcs", "unit_price": "Rs. 33.50", "line_total": "100"}
    r = merge_reads(read([cone], total="100"), read([cone], total="100"))
    assert r.lines[0].unit_price == 34  # the frozen contract carries whole rupees, rounded half-up
    assert r.agreed is False
    assert r.disagreements == ["line 1 Arun Cone unit_price: 33.50 (rounded to 34)"]


def test_paise_dealer_rate_is_flagged_so_no_phantom_discrepancy_is_agreed():
    curd = {"name": "Sakthi Curd 500ml", "qty": "30", "unit": "pack", "unit_price": "22.50", "line_total": "675"}
    r = merge_reads(read([curd], total="675"), read([curd], total="675"))
    assert r.lines[0].line_total == 675 and r.lines[0].unit_price == 23
    assert r.agreed is False
    assert "line 1 Sakthi Curd 500ml unit_price: 22.50 (rounded to 23)" in r.disagreements


def test_whole_rupee_price_written_with_paise_is_not_flagged():
    curd = dict(CURD, unit_price="33.00")
    r = merge_reads(read([curd, MILK]), read([curd, MILK]))
    assert r.agreed is True, r.disagreements


def test_indian_and_indic_formats_compare_equal():
    curd_b = dict(CURD, qty="௩௦ L", unit_price="Rs. 33/-", line_total="९९०")
    r = merge_reads(read([CURD, MILK]), read([curd_b, MILK], total="INR 1,470"))
    assert r.agreed is True, r.disagreements


def test_arithmetic_issues_direct():
    lines = [ParsedLine("Curd", Decimal("30"), "L", Decimal("33"), Decimal("1000"))]
    issues = arithmetic_issues(lines, Decimal("1000"))
    assert len(issues) == 1 and issues[0].startswith("arith: line 1 Curd")
    assert arithmetic_issues([], None) == []


def test_line_only_in_one_read():
    r = merge_reads(read([CURD, MILK]), read([CURD], total="₹1,470"))
    assert r.agreed is False
    assert any("only in one read" in d for d in r.disagreements)


def test_handwritten_bill_reads_back_every_line():
    r = merge_reads(read([CURD, MILK], handwritten=True), read([CURD, MILK]))
    assert r.agreed is False
    assert "handwritten: confirm each line" in r.disagreements
    assert any(d.startswith("line 2 Aavin Milk 500ml: 20 pack at 24 = 480") for d in r.disagreements)


def test_unreadable_field_is_a_disagreement():
    blank = dict(CURD, unit_price="")
    r = merge_reads(read([blank, MILK]), read([blank, MILK]))
    assert r.agreed is False
    assert any("unit_price: unreadable" in d for d in r.disagreements)


def test_empty_reads_never_agree():
    r = merge_reads(read([], total=None), read([], total=None))
    assert r.agreed is False and r.lines == [] and r.total is None
    assert "lines: none read" in r.disagreements


def test_malformed_raw_never_raises():
    r = merge_reads({"lines": [None, "x", {"name": "Curd"}]}, [])  # type: ignore[arg-type]
    assert r.agreed is False
