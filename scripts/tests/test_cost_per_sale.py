import pytest

import cost_per_sale as c


def test_default_sale_cost():
    # Live 0.75 min in + 0.25 min out, 1 ER 2 product check, 2 Flash cash reads
    assert c.usd_per_sale() == pytest.approx(0.0140, abs=1e-9)


def test_default_delivery_cost():
    # 30 one-by-one ER 2 identifications, 2 invoice reads, 2 cash reads, 2 min in + 1 min out
    assert c.usd_per_delivery() == pytest.approx(0.1039, abs=1e-9)


def test_monthly_cost_and_inr():
    m = c.monthly_usd(sales_per_day=60, deliveries_per_day=2)
    assert m == pytest.approx(31.434, abs=1e-6)
    assert c.to_inr(m, 88.0) == pytest.approx(2766.192, abs=1e-3)


def test_custom_profiles_change_the_cost():
    # Fewer cash reads and no product check means a cheaper sale; nothing is hard-wired.
    cheap = c.usd_per_sale(c.SaleProfile(er2_checks=0, cash_reads=0))
    assert cheap == pytest.approx(0.75 * 0.005 + 0.25 * 0.018, abs=1e-12)
    assert c.usd_per_delivery(c.DeliveryProfile(packs=0)) < c.usd_per_delivery()


def test_cli_output(capsys):
    assert c.main(["--rate", "88"]) == 0
    out = capsys.readouterr().out
    assert "Per sale: $0.0140 = ₹1.23" in out
    assert "Per delivery: $0.1039 = ₹9.14" in out
    assert "Per shop-month (60 sales/day, 2 deliveries/day): $31.43 = ₹2,766" in out
    assert "Gemini API only" in out


def test_cli_custom_volume(capsys):
    assert c.main(["--rate", "88", "--sales-per-day", "30", "--deliveries-per-day", "1"]) == 0
    out = capsys.readouterr().out
    assert "Per shop-month (30 sales/day, 1 deliveries/day): $15.72 = ₹1,383" in out
