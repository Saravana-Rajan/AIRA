"""Order vs physical count vs paper invoice, and the full order -> count -> invoice -> pay -> sell scenario."""
from datetime import date

import pytest

from aira_live.business import reconcile
from aira_live.vision.invoice import InvoiceLine, InvoiceRead

SHOP = "sunrise_dairy"
ORDER = [{"sku": "sakthi_curd_1l", "name": "Sakthi Curd 1 L", "qty": 30, "agreedPrice": 50}]
# Both curd sizes on one order, with the order fields kanish/02 stores (qty and price in selling units: L).
ORDER2 = [{"sku": "sakthi_curd_500ml", "name": "Sakthi Curd 500 ml", "qty": 10.0, "unit": "L", "agreedPrice": 52},
          {"sku": "sakthi_curd_1l", "name": "Sakthi Curd 1 L", "qty": 30.0, "unit": "L", "agreedPrice": 50}]
PANEER = [{"sku": "aavin_paneer_200g", "name": "Aavin Paneer 200 g", "qty": 25.0, "unit": "pack", "agreedPrice": 80}]


def inv(qty=30, unit_price=50, line_total=None, total=None, agreed=True, name="SAKTHI CURD 1L"):
    lt = qty * unit_price if line_total is None else line_total
    return InvoiceRead(supplier="Sakthi", date="2026-10-12",
                       lines=[InvoiceLine(name=name, qty=qty, unit="L", unit_price=unit_price, line_total=lt)],
                       total=lt if total is None else total, agreed=agreed,
                       disagreements=[] if agreed else ["line 1 qty"])


def bill(*lines, total=None):
    ls = [InvoiceLine(name=n, qty=q, unit=u, unit_price=p, line_total=t) for n, q, u, p, t in lines]
    return InvoiceRead(supplier="Sakthi", date="2026-10-12", lines=ls,
                       total=sum(x.line_total for x in ls) if total is None else total,
                       agreed=True, disagreements=[])


def kinds(r):
    return sorted(d["kind"] for d in r.discrepancies)


def by_kind(r, kind):
    return [d for d in r.discrepancies if d["kind"] == kind]


# ---- plan cases ----

def test_compare_match():
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, inv())
    assert r.status == "match" and r.discrepancies == []


def test_compare_short_and_invoice_mismatch():
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 28}, inv(qty=30))
    assert r.status == "discrepancy"
    assert kinds(r) == ["INVOICE_QTY_MISMATCH", "SHORT_DELIVERY"]
    q = by_kind(r, "INVOICE_QTY_MISMATCH")[0]
    assert (q["sku"], q["expected"], q["actual"]) == ("sakthi_curd_1l", 28.0, 30.0)


def test_compare_price_and_total_mismatch():
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, inv(unit_price=55, total=1700))
    assert kinds(r) == ["PRICE_MISMATCH", "TOTAL_MISMATCH"]
    p = by_kind(r, "PRICE_MISMATCH")[0]
    assert (p["expected"], p["actual"]) == (50, 55)
    t = by_kind(r, "TOTAL_MISMATCH")[0]
    assert (t["expected"], t["actual"]) == (1650, 1700)


def test_compare_unreadable_invoice():
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, inv(agreed=False))
    assert r.status == "discrepancy" and kinds(r) == ["INVOICE_UNREADABLE"]
    assert r.discrepancies[0]["fields"] == ["line 1 qty"]


def test_unreadable_invoice_is_never_trusted_even_if_its_numbers_look_wrong():
    # Two reads disagree: none of the bill's numbers are used, so no price/total claims are made from it.
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, inv(unit_price=99, total=1, agreed=False))
    assert kinds(r) == ["INVOICE_UNREADABLE"]


def test_compare_over_unordered_and_line_total():
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 31, "aavin_milk_1l": 2},
                          inv(qty=31, line_total=1500, total=1500))
    assert kinds(r) == ["LINE_TOTAL_MISMATCH", "OVER_DELIVERY", "UNORDERED_ITEM"]
    u = by_kind(r, "UNORDERED_ITEM")[0]
    assert (u["sku"], u["expected"], u["actual"]) == ("aavin_milk_1l", 0.0, 2.0)
    lt = by_kind(r, "LINE_TOTAL_MISMATCH")[0]
    assert (lt["expected"], lt["actual"]) == (1550, 1500)


def test_compare_unknown_invoice_line():
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, inv(name="ARUN ICE CREAM FAMILY BOX"))
    assert kinds(r) == ["UNKNOWN_INVOICE_LINE"]
    assert r.discrepancies[0]["name"] == "ARUN ICE CREAM FAMILY BOX"


def test_compare_without_invoice_uses_count_only():
    assert reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, None).status == "match"
    r = reconcile.compare(ORDER, {}, None)
    assert r.discrepancies == [{"kind": "SHORT_DELIVERY", "sku": "sakthi_curd_1l", "expected": 30.0, "actual": 0.0}]


def test_counted_zero_of_an_unordered_item_is_not_a_discrepancy():
    assert reconcile.compare(ORDER, {"sakthi_curd_1l": 30, "aavin_milk_1l": 0}, None).status == "match"


# ---- full dairy range: sizes and units ----

def test_two_sizes_of_one_product_match_by_pack_size_and_pack_units():
    # Bill lists the 500 ml curd in packets at a per-packet price; the order holds litres at a per-litre price.
    b = bill(("SAKTHI CURD 500ML", 20, "pkt", 26, 520), ("SAKTHI CURD 1L", 30, "L", 50, 1500))
    r = reconcile.compare(ORDER2, {"sakthi_curd_500ml": 10.0, "sakthi_curd_1l": 30.0}, b)
    assert r.status == "match", r.discrepancies


def test_pack_bill_vs_litre_count_reports_in_bill_units():
    b = bill(("SAKTHI CURD 500ML", 20, "pkt", 26, 520), ("SAKTHI CURD 1L", 30, "L", 50, 1500))
    r = reconcile.compare(ORDER2, {"sakthi_curd_500ml": 9.0, "sakthi_curd_1l": 30.0}, b)
    assert kinds(r) == ["INVOICE_QTY_MISMATCH", "SHORT_DELIVERY"]
    q = by_kind(r, "INVOICE_QTY_MISMATCH")[0]
    assert (q["sku"], q["expected"], q["actual"]) == ("sakthi_curd_500ml", 18.0, 20.0)   # packets: 18 came, bill 20
    s = by_kind(r, "SHORT_DELIVERY")[0]
    assert (s["sku"], s["expected"], s["actual"]) == ("sakthi_curd_500ml", 10.0, 9.0)    # litres


def test_per_packet_price_is_checked_against_the_per_litre_agreed_price():
    b = bill(("SAKTHI CURD 500ML", 20, "pkt", 28, 560), ("SAKTHI CURD 1L", 30, "L", 50, 1500))
    r = reconcile.compare(ORDER2, {"sakthi_curd_500ml": 10.0, "sakthi_curd_1l": 30.0}, b)
    assert kinds(r) == ["PRICE_MISMATCH"]
    p = r.discrepancies[0]
    assert (p["sku"], p["expected"], p["actual"]) == ("sakthi_curd_500ml", 26, 28)


def test_size_on_the_bill_must_match_the_ordered_size():
    # Only the 1 L curd was ordered; a 500 ml line is not silently booked against it.
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, inv(name="SAKTHI CURD 500ML"))
    assert kinds(r) == ["UNKNOWN_INVOICE_LINE"]


def test_line_without_size_is_not_guessed_when_two_sizes_were_ordered():
    b = bill(("SAKTHI CURD", 30, "L", 50, 1500))
    r = reconcile.compare(ORDER2, {"sakthi_curd_500ml": 10.0, "sakthi_curd_1l": 30.0}, b)
    assert kinds(r) == ["UNKNOWN_INVOICE_LINE"]
    assert r.discrepancies[0]["name"] == "SAKTHI CURD"


def test_similar_names_book_against_the_right_item():
    order = [{"sku": "aavin_butter_100g", "name": "Aavin Butter 100 g", "qty": 10.0, "unit": "pack", "agreedPrice": 48},
             {"sku": "aavin_buttermilk_200ml", "name": "Aavin Buttermilk 200 ml", "qty": 40.0, "unit": "pack",
              "agreedPrice": 8}]
    b = bill(("AAVIN BUTTERMILK", 40, "pkt", 8, 320), ("AAVIN BUTTER 100G", 10, "pkt", 48, 480))
    counted = {"aavin_butter_100g": 10.0, "aavin_buttermilk_200ml": 40.0}
    assert reconcile.compare(order, counted, b).status == "match"


def test_one_product_split_over_two_bill_lines_is_summed():
    b = bill(("SAKTHI CURD 1L", 15, "L", 50, 750), ("SAKTHI CURD 1 LTR", 15, "ltr", 50, 750))
    assert reconcile.compare(ORDER, {"sakthi_curd_1l": 30}, b).status == "match"


def test_kilograms_on_the_bill_vs_counted_packs():
    b = bill(("AAVIN PANEER", 5, "kg", 400, 2000))
    assert reconcile.compare(PANEER, {"aavin_paneer_200g": 25.0}, b).status == "match"
    r = reconcile.compare(PANEER, {"aavin_paneer_200g": 24.0}, b)
    assert kinds(r) == ["INVOICE_QTY_MISMATCH", "SHORT_DELIVERY"]
    q = by_kind(r, "INVOICE_QTY_MISMATCH")[0]
    assert (q["expected"], q["actual"]) == (4.8, 5.0)                                      # kg


def test_tamil_digits_and_unit_words_on_the_bill():
    b = bill(("சக்தி CURD ௧ லிட்டர்", 30, "லிட்டர்", 50, 1500))
    order = [{"sku": "sakthi_curd_1l", "name": "சக்தி Curd 1 L", "qty": 30.0, "unit": "L", "agreedPrice": 50}]
    assert reconcile.compare(order, {"sakthi_curd_1l": 30.0}, b).status == "match"


# ---- payable ----

def test_payable_counts_only_received():
    assert reconcile.payable(ORDER, {"sakthi_curd_1l": 28}) == 1400
    assert reconcile.payable(ORDER, {"sakthi_curd_1l": 31}) == 1500


def test_payable_full_range_and_rounding():
    assert reconcile.payable(ORDER2, {"sakthi_curd_500ml": 9.5, "sakthi_curd_1l": 30.0}) == 494 + 1500
    half = [{"sku": "x", "name": "X 500 ml", "qty": 1.0, "unit": "L", "agreedPrice": 45}]
    assert reconcile.payable(half, {"x": 0.5}) == 23                                        # ₹22.50 -> ₹23
    assert reconcile.payable(half, {}) == 0


def test_payable_without_agreed_price_raises():
    with pytest.raises(ValueError):
        reconcile.payable([{"sku": "x", "name": "X", "qty": 1}], {"x": 1})


# ---- analytics after a reconcile ----

async def test_outbox_discrepancies_and_order_state(published):
    from aira_live.business import outbox
    r = reconcile.compare(ORDER, {"sakthi_curd_1l": 28}, inv(qty=30))
    await outbox.discrepancies(SHOP, "del_1", r)
    await outbox.order_state(SHOP, "ord_1", "DISCREPANCY")
    disc = [m for t, m in published if t == "discrepancies"]
    assert {m["field"] for m in disc} == {"SHORT_DELIVERY:sakthi_curd_1l", "INVOICE_QTY_MISMATCH:sakthi_curd_1l"}
    assert all(m["shop_id"] == SHOP and m["delivery_id"] == "del_1" for m in disc)
    short = next(m for m in disc if m["field"].startswith("SHORT_DELIVERY"))
    assert (short["expected"], short["actual"]) == (30.0, 28.0)
    assert ("orders", {"shop_id": SHOP, "order_id": "ord_1", "state": "DISCREPANCY"}) in published


# ---- the full engine scenario ----

async def test_full_scenario_order_30_count_28(store):
    from aira_live.business import hints, ledger, money, payments, sales
    from aira_live.business.states import transition

    d0 = date(2026, 10, 12)
    state = transition("order", "DRAFT", "ORDER_PLACED")
    state = transition("order", state, "DELIVERY_PENDING")

    # Vendor says 30, the camera count says 28, and the bill says 30: the count wins, the bill is flagged.
    counted = {"sakthi_curd_1l": 28}
    r = reconcile.compare(ORDER, counted, inv(qty=30))
    assert r.status == "discrepancy"
    state = transition("order", state, "DISCREPANCY")
    short = by_kind(r, "SHORT_DELIVERY")[0]
    fact = hints.fact("SHORT_DELIVERY", product="Sakthi Curd 1 L", expected=int(short["expected"]),
                      actual=int(short["actual"]))
    assert (fact["error"], fact["expected"], fact["actual"]) == ("SHORT_DELIVERY", 30, 28)
    assert fact["say_en"] == "Sakthi Curd 1 L: ordered 30, counted 28. Short delivery."

    # Only the verified 28 go into stock.
    await ledger.apply(SHOP, "sakthi_curd_1l", 28, "DELIVERY", "delivery", "d1", "k-deliv", today=d0)
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 28

    # Pay the vendor for what arrived, at the agreed price, and only after a cash count of exactly that.
    state = transition("order", state, "PAYMENT_PENDING")
    due = reconcile.payable(ORDER, counted)
    assert due == 1400 and money.notes_for(due) == [{"denomination": 500, "count": 2},
                                                    {"denomination": 200, "count": 2}]
    vp = await payments.record(SHOP, "out", "cash", due, "order", "o1", "k-pay-vendor")
    vp = await payments.confirm(SHOP, vp["id"], {"type": "cash_count", "total": 1400})
    assert vp["state"] == "CONFIRMED"
    state = transition("order", state, "PAYMENT_CONFIRMED")

    # A customer buys 2 L; ₹200 is counted, ₹80 change; stock drops once even when the tool call is retried.
    sale = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "k-sale")
    assert sale["total"] == 120
    cp = await payments.record(SHOP, "in", "cash", sale["total"], "sale", sale["id"], "k-pay-cust")
    cp = await payments.confirm(SHOP, cp["id"], {"type": "cash_count", "total": 200})
    assert cp["evidence"]["change"] == 80
    await sales.complete(SHOP, sale["id"], cp["id"], "k-sale-complete")
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 26

    await sales.complete(SHOP, sale["id"], cp["id"], "k-sale-complete")       # retried tool call
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 26
    await sales.complete(SHOP, sale["id"], cp["id"], "k-sale-complete-2")     # same sale, new key
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 26
    assert transition("order", state, "CLOSED") == "CLOSED"
