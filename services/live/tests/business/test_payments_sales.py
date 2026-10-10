from datetime import date

import pytest

from aira_live.business import ledger, payments, sales
from aira_live.business.ledger import InsufficientStock
from aira_live.business.payments import PaymentMismatch
from aira_live.business.sales import SaleError

SHOP = "murugan_dairy"
D0 = date(2026, 10, 12)


async def _stock(n=10):
    await ledger.apply(SHOP, "sakthi_curd_1l", n, "DELIVERY", "delivery", "d1", "stock-k", today=D0)


async def test_record_payment_idempotent(store):
    a = await payments.record(SHOP, "in", "cash", 120, "sale", "s1", "pay-k")
    b = await payments.record(SHOP, "in", "cash", 120, "sale", "s1", "pay-k")
    assert a["id"] == b["id"] and a["state"] == "PENDING"
    assert len([p for p in store.data if p.startswith(f"shops/{SHOP}/payments/")]) == 1


async def test_record_rejects_bad_method_and_amount(store):
    with pytest.raises(ValueError):
        await payments.record(SHOP, "in", "card", 120, "sale", "s1", "k1")
    with pytest.raises(ValueError):
        await payments.record(SHOP, "in", "cash", 0, "sale", "s1", "k2")
    with pytest.raises(ValueError):
        await payments.record(SHOP, "in", "cash", 12.5, "sale", "s1", "k3")
    assert not [p for p in store.data if p.startswith(f"shops/{SHOP}/payments/")]


async def test_confirm_upi_soundbox_amount_must_match(store):
    p = await payments.record(SHOP, "in", "upi", 120, "sale", "s1", "k")
    with pytest.raises(PaymentMismatch):
        await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 100})
    ok = await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 120, "provider": "Paytm"})
    assert ok["state"] == "CONFIRMED" and ok["evidence"]["type"] == "soundbox"


async def test_confirm_soundbox_only_for_incoming_upi(store):
    p = await payments.record(SHOP, "in", "cash", 120, "sale", "s1", "k")
    with pytest.raises(PaymentMismatch):
        await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 120})
    assert (await store.get(f"shops/{SHOP}/payments/{p['id']}"))["state"] == "PENDING"


async def test_confirm_cash_out_exact(store):
    p = await payments.record(SHOP, "out", "cash", 1400, "order", "o1", "k")
    with pytest.raises(PaymentMismatch):
        await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 1500})
    assert (await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 1400}))["state"] == "CONFIRMED"


async def test_confirm_cash_in_tendered_at_least_total(store):
    p = await payments.record(SHOP, "in", "cash", 120, "sale", "s1", "k")
    with pytest.raises(PaymentMismatch):
        await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 100})
    c = await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 200})
    assert c["state"] == "CONFIRMED" and c["evidence"]["change"] == 80


async def test_confirm_idempotent(store):
    p = await payments.record(SHOP, "in", "upi", 60, "sale", "s1", "k")
    a = await payments.confirm(SHOP, p["id"], {"type": "user_confirm"})
    b = await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 999})   # already confirmed → unchanged
    assert a == b


async def test_fail_payment(store):
    p = await payments.record(SHOP, "in", "upi", 60, "sale", "s1", "k")
    f = await payments.fail(SHOP, p["id"], "customer left")
    assert f["state"] == "FAILED" and f["failReason"] == "customer left"


async def test_sale_create_prices_from_catalog_and_aggregates(store):
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 1}, {"sku": "sakthi_curd_1l", "qty": 1}], "sale-k")
    assert s["state"] == "SALE_PENDING" and s["total"] == 120
    assert s["items"] == [{"sku": "sakthi_curd_1l", "qty": 2.0, "unitPrice": 60, "lineTotal": 120}]


async def test_sale_create_idempotent(store):
    await _stock()
    a = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    b = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    assert a["id"] == b["id"]
    assert len([p for p in store.data if p.startswith(f"shops/{SHOP}/sales/")]) == 1


async def test_sale_create_insufficient_stock(store):
    await _stock(1)
    with pytest.raises(InsufficientStock):
        await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    assert not [p for p in store.data if p.startswith(f"shops/{SHOP}/sales/")]


async def test_sale_create_rejects_bad_input(store):
    await _stock()
    with pytest.raises(SaleError):
        await sales.create(SHOP, [], "k1")
    with pytest.raises(SaleError):
        await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 0}], "k2")
    with pytest.raises(KeyError):
        await sales.create(SHOP, [{"sku": "no_such_sku", "qty": 1}], "k3")
    assert not [p for p in store.data if p.startswith(f"shops/{SHOP}/sales/")]


async def test_sale_complete_requires_confirmed_payment(store):
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    p = await payments.record(SHOP, "in", "cash", 120, "sale", s["id"], "pay-k")
    with pytest.raises(SaleError):
        await sales.complete(SHOP, s["id"], p["id"], "complete-k")
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 10


async def test_sale_complete_rejects_wrong_amount(store):
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    p = await payments.record(SHOP, "in", "upi", 60, "sale", s["id"], "pay-k")
    await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 60})
    with pytest.raises(SaleError):
        await sales.complete(SHOP, s["id"], p["id"], "complete-k")
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 10


async def test_sale_complete_rejects_payment_for_another_sale(store):
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    p = await payments.record(SHOP, "in", "upi", 120, "sale", "some-other-sale", "pay-k")
    await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 120})
    with pytest.raises(SaleError):
        await sales.complete(SHOP, s["id"], p["id"], "complete-k")
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 10


async def test_sale_complete_deducts_once_on_retry(store):
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    p = await payments.record(SHOP, "in", "cash", 120, "sale", s["id"], "pay-k")
    await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 200})
    done = await sales.complete(SHOP, s["id"], p["id"], "complete-k")
    assert done["state"] == "SALE_COMPLETED"
    await sales.complete(SHOP, s["id"], p["id"], "complete-k")        # same key retry
    await sales.complete(SHOP, s["id"], p["id"], "complete-k-2")      # new key, same sale
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 8


async def test_sale_complete_stock_gone_writes_nothing(store):
    await _stock(2)
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    await ledger.apply(SHOP, "sakthi_curd_1l", -1, "EXPIRED", "expiry", "x1", "exp-k", today=D0)
    p = await payments.record(SHOP, "in", "upi", 120, "sale", s["id"], "pay-k")
    await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 120})
    with pytest.raises(InsufficientStock):
        await sales.complete(SHOP, s["id"], p["id"], "complete-k")
    assert (await store.get(f"shops/{SHOP}/sales/{s['id']}"))["state"] == "SALE_PENDING"
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 1


async def test_sale_void(store):
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 1}], "void-k")
    v = await sales.void(SHOP, s["id"])
    assert v["state"] == "SALE_VOID"
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 10


async def test_sale_timestamps(store):
    from datetime import datetime
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 1}], "ts-sale")
    assert isinstance(s["createdAt"], datetime) and "completedAt" not in s
    p = await payments.record(SHOP, "in", "upi", 60, "sale", s["id"], "ts-pay")
    await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 60})
    done = await sales.complete(SHOP, s["id"], p["id"], "ts-complete")
    assert isinstance(done["completedAt"], datetime)


async def test_sale_complete_publishes_once(store, published):
    await _stock()
    published.clear()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "pub-sale")
    p = await payments.record(SHOP, "in", "upi", 120, "sale", s["id"], "pub-pay")
    await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 120})
    await sales.complete(SHOP, s["id"], p["id"], "pub-complete")
    await sales.complete(SHOP, s["id"], p["id"], "pub-complete")                   # retry → no new events
    sales_msgs = [m for t, m in published if t == "sales"]
    moves = [m for t, m in published if t == "inventory-movements"]
    assert len(sales_msgs) == 1 and len(moves) == 1
    assert sales_msgs[0] == {"shop_id": SHOP, "sale_id": s["id"], "sku": "sakthi_curd_1l", "qty": 2.0,
                             "unit_price": 60, "ts": sales_msgs[0]["ts"]}
    assert moves[0]["delta"] == -2.0 and moves[0]["reason"] == "SALE"


async def test_evidence_has_contract_detail_string(store):
    """packages/contracts PaymentEvidence is {type, detail: string}; the engine keeps its own fields too."""
    up = await payments.record(SHOP, "in", "upi", 120, "sale", "s1", "k-upi")
    ev = (await payments.confirm(SHOP, up["id"], {"type": "soundbox", "amount": 120, "provider": "Paytm"}))["evidence"]
    assert isinstance(ev["detail"], str) and "120" in ev["detail"] and ev["amount"] == 120
    cash = await payments.record(SHOP, "in", "cash", 120, "sale", "s1", "k-cash")
    ev = (await payments.confirm(SHOP, cash["id"], {"type": "cash_count", "total": 200}))["evidence"]
    assert "200" in ev["detail"] and "80" in ev["detail"]
    own = await payments.record(SHOP, "in", "upi", 60, "sale", "s1", "k-own")
    ev = (await payments.confirm(SHOP, own["id"], {"type": "user_confirm", "detail": "owner heard the soundbox"}))[
        "evidence"]
    assert ev["detail"] == "owner heard the soundbox"


async def test_cash_count_rejected_for_upi_and_missing_total(store):
    up = await payments.record(SHOP, "in", "upi", 60, "sale", "s1", "k1")
    with pytest.raises(PaymentMismatch):
        await payments.confirm(SHOP, up["id"], {"type": "cash_count", "total": 60})
    cash = await payments.record(SHOP, "out", "cash", 60, "order", "o1", "k2")
    with pytest.raises(PaymentMismatch):
        await payments.confirm(SHOP, cash["id"], {"type": "cash_count"})
    with pytest.raises(ValueError):
        await payments.confirm(SHOP, cash["id"], {"type": "counterfeit_check"})
    assert (await store.get(f"shops/{SHOP}/payments/{cash['id']}"))["state"] == "PENDING"


async def test_failed_payment_cannot_be_confirmed(store):
    from aira_live.business.states import InvalidTransition
    p = await payments.record(SHOP, "in", "upi", 60, "sale", "s1", "k")
    await payments.fail(SHOP, p["id"], "customer left")
    with pytest.raises(InvalidTransition):
        await payments.confirm(SHOP, p["id"], {"type": "user_confirm"})


async def test_line_total_rounds_half_up(store):
    await ledger.apply(SHOP, "aavin_buttermilk_200ml", 5, "DELIVERY", "delivery", "d1", "bm-k", today=D0)
    s = await sales.create(SHOP, [{"sku": "aavin_buttermilk_200ml", "qty": 0.25}], "half-k")   # 10 x 0.25 = 2.5
    assert s["items"][0]["lineTotal"] == 3 and s["total"] == 3


async def test_complete_voided_sale_writes_nothing(store):
    from aira_live.business.states import InvalidTransition
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    await sales.void(SHOP, s["id"])
    p = await payments.record(SHOP, "in", "upi", 120, "sale", s["id"], "pay-k")
    await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 120})
    before = dict(store.data)
    with pytest.raises(InvalidTransition):
        await sales.complete(SHOP, s["id"], p["id"], "complete-k")
    assert store.data == before
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 10


async def test_completed_sale_cannot_be_voided(store):
    from aira_live.business.states import InvalidTransition
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 1}], "sale-k")
    p = await payments.record(SHOP, "in", "upi", 60, "sale", s["id"], "pay-k")
    await payments.confirm(SHOP, p["id"], {"type": "soundbox", "amount": 60})
    await sales.complete(SHOP, s["id"], p["id"], "complete-k")
    with pytest.raises(InvalidTransition):
        await sales.void(SHOP, s["id"])
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 9


async def test_sale_complete_writes_ledger_and_audit(store):
    await _stock()
    s = await sales.create(SHOP, [{"sku": "sakthi_curd_1l", "qty": 2}], "sale-k")
    p = await payments.record(SHOP, "in", "cash", 120, "sale", s["id"], "pay-k")
    await payments.confirm(SHOP, p["id"], {"type": "cash_count", "total": 120})
    await sales.complete(SHOP, s["id"], p["id"], "complete-k")
    sale_rows = [d for path, d in store.data.items()
                 if path.startswith(f"shops/{SHOP}/ledger/") and d["reason"] == "SALE"]
    assert len(sale_rows) == 1 and sale_rows[0]["refId"] == s["id"] and sale_rows[0]["delta"] == -2.0
    audits = [d for path, d in store.data.items() if path.startswith(f"shops/{SHOP}/auditLogs/")]
    assert [(a["action"], a["refId"]) for a in audits] == [("sale_completed", s["id"])]
