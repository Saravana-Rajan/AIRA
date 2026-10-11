"""analytics.events: business events appended to Firestore shops/{shop_id}/events (no Pub/Sub, no BigQuery).

Uses the shared ``fake_db`` fixture (tests/conftest.py), which puts an in-memory Firestore behind
``aira_live.db.db()``. Settings come from the real ``aira_live.config.load()`` via env vars.
"""
import json
from datetime import datetime, timezone

import pytest

from aira_live.analytics import events

SHOP = "sunrise_dairy"
SALE = {"shop_id": SHOP, "sale_id": "x", "sku": "sakthi_curd_1l", "qty": 2}
ALERT = {"shop_id": SHOP, "kind": "low_stock", "message": "m"}


@pytest.fixture
def live_env(monkeypatch):
    """Not mock mode, with a project: publish really writes (to the fake Firestore)."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GCP_PROJECT", "proj")
    monkeypatch.delenv("AIRA_MOCK", raising=False)


def _events(fake_db, shop: str = SHOP) -> dict[str, dict]:
    prefix = f"shops/{shop}/events/"
    return {k[len(prefix):]: v for k, v in fake_db.store.items() if k.startswith(prefix)}


# --- contract -----------------------------------------------------------------------------------


def test_topics_are_exactly_the_five_contract_topics():
    expected = {"sales", "inventory-movements", "orders", "discrepancies", "alerts"}
    assert set(events.TOPICS) == expected
    assert set(events.REQUIRED) == expected
    assert events.TOPICS["inventory-movements"] == "inventory_movements"


def test_required_fields_match_the_interfaces_table():
    assert events.REQUIRED == {
        "sales": {"shop_id", "sale_id", "sku", "qty"},
        "inventory-movements": {"shop_id", "txn_id", "sku", "delta", "reason"},
        "orders": {"shop_id", "order_id", "state"},
        "discrepancies": {"shop_id", "delivery_id", "field"},
        "alerts": {"shop_id", "kind", "message"},
    }


def test_event_error_is_a_value_error():
    assert issubclass(events.EventError, ValueError)


# --- validation ---------------------------------------------------------------------------------


def test_unknown_topic_raises():
    with pytest.raises(events.EventError):
        events.build_message("payments", {"shop_id": "s"})


def test_missing_required_field_raises():
    with pytest.raises(events.EventError, match="sku"):
        events.build_message("sales", {"shop_id": "s", "sale_id": "x", "qty": 2})


def test_none_required_field_counts_as_missing():
    with pytest.raises(events.EventError, match="sku"):
        events.build_message("sales", {"shop_id": "s", "sale_id": "x", "sku": None, "qty": 2})


def test_zero_qty_is_not_missing():
    body = events.build_event("inventory-movements",
                              {"shop_id": "s", "txn_id": "t", "sku": "curd", "delta": 0, "reason": "COUNT"})
    assert body["delta"] == 0


@pytest.mark.parametrize("shop_id", ["", "a/b", 7])
def test_bad_shop_id_raises(shop_id):
    with pytest.raises(events.EventError, match="shop_id"):
        events.build_message("alerts", {"shop_id": shop_id, "kind": "low_stock", "message": "m"})


def test_non_dict_payload_raises():
    with pytest.raises(events.EventError):
        events.build_message("alerts", ["shop_id", "kind", "message"])


# --- message body -------------------------------------------------------------------------------


def test_ts_added_and_payload_preserved():
    body = json.loads(events.build_message("sales", {"shop_id": "s", "sale_id": "x", "sku": "curd", "qty": 2}))
    assert body["sku"] == "curd" and body["qty"] == 2 and body["ts"].endswith("+00:00")


def test_caller_ts_is_kept_and_input_not_mutated():
    payload = {"shop_id": "s", "order_id": "o1", "state": "SENT", "ts": "2026-10-10T01:00:00+00:00"}
    body = events.build_event("orders", payload)
    assert body["ts"] == "2026-10-10T01:00:00+00:00"
    assert body is not payload
    sale = dict(SALE)
    events.build_event("sales", sale)
    assert "ts" not in sale


def test_non_json_values_become_strings():
    when = datetime(2026, 10, 10, 9, 30, tzinfo=timezone.utc)
    body = events.build_event("discrepancies",
                              {"shop_id": "s", "delivery_id": "d1", "field": "qty", "expected": when})
    assert body["expected"] == str(when)


def test_tamil_text_kept_unescaped():
    msg = events.build_message("alerts", {"shop_id": "s", "kind": "expiry", "message": "தயிர் நாளை காலாவதி"})
    assert "தயிர்".encode() in msg


# --- publish ------------------------------------------------------------------------------------


async def test_mock_mode_returns_mock(monkeypatch, fake_db):
    monkeypatch.setenv("AIRA_MOCK", "1")
    monkeypatch.setenv("GCP_PROJECT", "proj")
    assert await events.publish("alerts", dict(ALERT)) == "mock"
    assert fake_db.store == {}


async def test_no_project_returns_mock(monkeypatch, fake_db):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("AIRA_MOCK", raising=False)
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    assert await events.publish("alerts", dict(ALERT)) == "mock"
    assert fake_db.store == {}


async def test_mock_mode_still_validates(monkeypatch, fake_db):
    monkeypatch.setenv("AIRA_MOCK", "1")
    with pytest.raises(events.EventError, match="kind"):
        await events.publish("alerts", {"shop_id": SHOP, "message": "m"})


async def test_publish_appends_to_shop_events(live_env, fake_db):
    doc_id = await events.publish(
        "inventory-movements",
        {"shop_id": SHOP, "txn_id": "t", "sku": "sakthi_curd_1l", "delta": -2, "reason": "SALE"},
    )
    stored = _events(fake_db)
    assert list(stored) == [doc_id] and doc_id and doc_id != "mock"
    assert list(fake_db.store) == [f"shops/{SHOP}/events/{doc_id}"]
    doc = stored[doc_id]
    assert set(doc) == {"topic", "payload", "ts"}
    assert doc["topic"] == "inventory-movements"
    assert doc["payload"]["delta"] == -2 and doc["payload"]["reason"] == "SALE"
    assert isinstance(doc["ts"], datetime) and doc["ts"].tzinfo is not None
    assert doc["payload"]["ts"] == doc["ts"].isoformat()


async def test_events_land_under_their_own_shop(live_env, fake_db):
    await events.publish("orders", {"shop_id": "other_shop", "order_id": "o1", "state": "SENT"})
    assert _events(fake_db, SHOP) == {}
    assert len(_events(fake_db, "other_shop")) == 1


async def test_each_publish_is_a_new_append_only_doc(live_env, fake_db):
    a = await events.publish("sales", dict(SALE))
    b = await events.publish("sales", dict(SALE))
    assert a != b and set(_events(fake_db)) == {a, b}


async def test_invalid_payload_never_written(live_env, fake_db):
    with pytest.raises(events.EventError, match="sku"):
        await events.publish("sales", {"shop_id": SHOP, "sale_id": "x", "qty": 2})
    assert fake_db.store == {}


# --- the business outbox (Kanish) publishes through events.publish -----------------------------


async def test_business_outbox_payloads_pass_validation_and_land(live_env, fake_db):
    from aira_live.business import outbox

    outbox.set_publisher(None)
    await outbox.inventory_movement(SHOP, "t1", "aavin_milk_500ml", -3, "SALE")
    await outbox.sale_lines(SHOP, "s1", [{"sku": "aavin_milk_500ml", "qty": 3, "unitPrice": 27}])
    await outbox.order_state(SHOP, "o1", "SENT")
    topics = sorted(d["topic"] for d in _events(fake_db).values())
    assert topics == ["inventory-movements", "orders", "sales"]
