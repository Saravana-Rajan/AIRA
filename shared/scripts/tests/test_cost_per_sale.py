import pytest

import cost_per_sale as c

# Hand-computed from the constants, so a silent price or allowance change shows up here.
ER2_COUNT = (1300 * 1.00 + (200 + 2000) * 5.00) / 1e6          # 0.0123
ER2_IDENTIFY = (1300 * 1.00 + (200 + 300) * 5.00) / 1e6        # 0.0038
FLASH_READ = (1300 * 0.75 + (200 + 800) * 3.75) / 1e6          # 0.004725
TWO_READS = 2 * FLASH_READ
SALE = 0.75 * 0.005 + 0.25 * 0.018 + ER2_IDENTIFY + TWO_READS
DELIVERY = 2.0 * 0.005 + 1.0 * 0.018 + 30 * ER2_COUNT + 2 * TWO_READS


def test_thinking_tokens_follow_the_thinking_level_each_role_asks_for():
    # count.py asks for "high", cash/invoice for "medium", identify/point for "low": the allowances must rank so.
    assert c.THINKING_TOKENS["count"] > c.THINKING_TOKENS["read"] > c.THINKING_TOKENS["identify"]
    assert c.THINKING_TOKENS["point"] == c.THINKING_TOKENS["identify"]
    assert c.er2_call("count") > c.er2_call("identify") == c.er2_call("point")


def test_thinking_is_billed_at_the_output_rate():
    assert c.er2_call("count") == pytest.approx(ER2_COUNT, abs=1e-12)
    assert c.er2_call("identify") == pytest.approx(ER2_IDENTIFY, abs=1e-12)
    assert c.flash_call("a") == pytest.approx(FLASH_READ, abs=1e-12)
    assert c.flash_call("b") == pytest.approx(FLASH_READ, abs=1e-12)  # same price today, priced separately


def test_default_sale_cost():
    # Live 0.75 min in + 0.25 min out, 1 ER 2 identify, one cash check = reads A + B
    assert c.usd_per_sale() == pytest.approx(SALE, abs=1e-12)


def test_default_delivery_cost():
    # 30 ER 2 count frames at thinking "high", bill and cash each read twice, 2 min in + 1 min out
    assert c.usd_per_delivery() == pytest.approx(DELIVERY, abs=1e-12)
    assert c.usd_per_delivery() > 30 * ER2_COUNT  # the count dominates once thinking is counted


def test_monthly_cost_and_inr():
    m = c.monthly_usd(sales_per_day=60, deliveries_per_day=2)
    assert m == pytest.approx(30 * (60 * SALE + 2 * DELIVERY), abs=1e-9)
    assert c.to_inr(m, 88.0) == pytest.approx(m * 88.0, abs=1e-9)


def test_custom_profiles_change_the_cost():
    # No product check and no cash read means a cheaper sale; nothing is hard-wired.
    cheap = c.usd_per_sale(c.SaleProfile(er2_checks=0, cash_reads=0))
    assert cheap == pytest.approx(0.75 * 0.005 + 0.25 * 0.018, abs=1e-12)
    assert c.usd_per_delivery(c.DeliveryProfile(packs=0)) < c.usd_per_delivery()


def test_prices_carry_a_source_and_match_the_pricing_page():
    assert "ai.google.dev/gemini-api/docs/pricing" in c.PRICE_SOURCE and "checked" in c.PRICE_SOURCE
    assert (c.USD_PER_1M["er2_in"], c.USD_PER_1M["er2_out"]) == (1.00, 5.00)
    assert (c.USD_PER_1M["flash_a_in"], c.USD_PER_1M["flash_a_out"]) == (0.75, 3.75)
    assert (c.LIVE_AUDIO_IN_PER_MIN, c.LIVE_AUDIO_OUT_PER_MIN) == (0.005, 0.018)


def test_rate_is_required(capsys):
    with pytest.raises(SystemExit) as info:
        c.main([])
    assert info.value.code == 2
    assert "--rate" in capsys.readouterr().err


def test_cli_output(capsys):
    assert c.main(["--rate", "88"]) == 0
    out = capsys.readouterr().out
    month = c.monthly_usd(60, 2)
    assert f"Per sale: ${SALE:.4f} = ₹{SALE * 88:.2f}" in out
    assert f"Per delivery: ${DELIVERY:.4f} = ₹{DELIVERY * 88:.2f}" in out
    assert f"Per shop-month (60 sales/day, 2 deliveries/day): ${month:.2f} = ₹{month * 88:,.0f}" in out
    assert "Gemini API only" in out and "Thinking tokens included" in out


def test_cli_custom_volume(capsys):
    assert c.main(["--rate", "88", "--sales-per-day", "30", "--deliveries-per-day", "1"]) == 0
    out = capsys.readouterr().out
    month = c.monthly_usd(30, 1)
    assert f"Per shop-month (30 sales/day, 1 deliveries/day): ${month:.2f} = ₹{month * 88:,.0f}" in out
