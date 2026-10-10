"""/jobs/* routes: X-AIRA-Job-Token guard, expiry alerts, day summary, forecast refresh."""
from __future__ import annotations

import importlib.util
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aira_live import jobs
from aira_live.analytics import summary

TOKEN = "test-job-token"
OK = {"X-AIRA-Job-Token": TOKEN}
PATHS = ["/jobs/expiry", "/jobs/day-summary", "/jobs/forecast-refresh"]
FIXED_TODAY = date(2026, 10, 13)
CURD = {"sku": "sakthi_curd_1l", "name": "Sakthi curd", "qty": 6, "daysLeft": 1}
REAL_SCAN = jobs._scan  # captured before any fixture patches it


@pytest.fixture
def env(monkeypatch):
    calls = {"notify": [], "events": [], "scan": [], "today": [], "refresh": [], "shops": 0, "lang_of": []}
    state = {"shops": [("murugan_dairy", "en")],
             "alerts": [dict(CURD)],
             "summary": {"sales_count": 48, "revenue": 2860, "top_sku": "aavin_milk_500ml",
                         "qty_by_sku": {"aavin_milk_500ml": 30}, "low_stock": [], "open_discrepancies": 0,
                         "suggested_orders": [],
                         "names": {"aavin_milk_500ml": "Aavin milk", "sakthi_curd_1l": "Sakthi curd"}},
             "scan_error_for": set(), "shop_lang": "en"}

    async def shops():
        calls["shops"] += 1
        return list(state["shops"])

    async def lang_of(shop_id):
        calls["lang_of"].append(shop_id)
        return state["shop_lang"]

    async def scan(shop_id, today):
        calls["scan"].append((shop_id, today))
        if shop_id in state["scan_error_for"]:
            raise RuntimeError("firestore down")
        return [dict(a) for a in state["alerts"]]

    async def notify(shop_id, text, cue="ack"):
        calls["notify"].append((shop_id, text, cue))
        return 1

    async def publish(topic, payload):
        calls["events"].append((topic, payload))
        return "evt-1"

    async def today(shop_id):
        calls["today"].append(shop_id)
        return dict(state["summary"])

    async def refresh(shop_id=None):
        calls["refresh"].append(shop_id)
        return {"aavin_milk_500ml": 31.5, "sakthi_curd_1l": None}

    monkeypatch.setenv("AIRA_JOB_TOKEN", TOKEN)
    monkeypatch.setattr(jobs, "_shops", shops)
    monkeypatch.setattr(jobs, "_lang_of", lang_of)
    monkeypatch.setattr(jobs, "_today", lambda: FIXED_TODAY)
    monkeypatch.setattr(jobs, "_scan", scan)
    monkeypatch.setattr(jobs.sessions, "notify", notify)
    monkeypatch.setattr(jobs.events, "publish", publish)
    monkeypatch.setattr(jobs.summary, "today", today)
    monkeypatch.setattr(jobs.forecast, "refresh_model", refresh)
    app = FastAPI()
    app.include_router(jobs.router)
    return TestClient(app), calls, state


def _nothing_ran(calls) -> bool:
    return (calls["notify"] == [] and calls["events"] == [] and calls["scan"] == [] and calls["today"] == []
            and calls["refresh"] == [] and calls["shops"] == 0 and calls["lang_of"] == [])


# --- Token guard ---------------------------------------------------------------------------------

@pytest.mark.parametrize("path", PATHS)
def test_jobs_reject_missing_token(env, path):
    c, calls, _ = env
    assert c.post(path).status_code == 401
    assert _nothing_ran(calls)


@pytest.mark.parametrize("path", PATHS)
def test_jobs_reject_wrong_token(env, path):
    c, calls, _ = env
    assert c.post(path, headers={"X-AIRA-Job-Token": "nope"}).status_code == 403
    assert c.post(path, headers={"X-AIRA-Job-Token": TOKEN + "x"}).status_code == 403
    assert _nothing_ran(calls)


@pytest.mark.parametrize("path", PATHS)
def test_jobs_fail_closed_when_token_env_unset(env, path, monkeypatch):
    c, calls, _ = env
    monkeypatch.delenv("AIRA_JOB_TOKEN", raising=False)
    assert c.post(path).status_code == 503
    assert c.post(path, headers={"X-AIRA-Job-Token": ""}).status_code == 503
    assert c.post(path, headers=OK).status_code == 503
    assert _nothing_ran(calls)


def test_jobs_fail_closed_when_token_env_is_empty(env, monkeypatch):
    c, calls, _ = env
    monkeypatch.setenv("AIRA_JOB_TOKEN", "")
    assert c.post("/jobs/expiry", headers={"X-AIRA-Job-Token": ""}).status_code == 503
    assert _nothing_ran(calls)


def test_token_is_compared_in_constant_time(env, monkeypatch):
    c, _, _ = env
    seen = []
    real = jobs.hmac.compare_digest

    def spy(a, b):
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(jobs.hmac, "compare_digest", spy)
    assert c.post("/jobs/forecast-refresh", headers={"X-AIRA-Job-Token": "nope"}).status_code == 403
    assert seen == [(b"nope", TOKEN.encode())]


def test_token_header_name_is_case_insensitive(env):
    c, _, _ = env
    assert c.post("/jobs/forecast-refresh", headers={"x-aira-job-token": TOKEN}).status_code == 200


def test_routes_are_under_jobs_and_post_only(env):
    c, _, _ = env
    paths = {r.path for r in jobs.router.routes}
    assert paths == set(PATHS)
    assert c.get("/jobs/expiry", headers=OK).status_code == 405


# --- /jobs/expiry --------------------------------------------------------------------------------

def test_expiry_job_speaks_alerts_and_publishes(env):
    c, calls, _ = env
    r = c.post("/jobs/expiry", headers=OK)
    assert r.status_code == 200
    body = r.json()
    assert body["shops"] == 1 and body["alerts"] == 1 and body["notified_sessions"] == 1 and body["errors"] == 0
    assert calls["scan"] == [("murugan_dairy", FIXED_TODAY)]
    shop_id, text, cue = calls["notify"][0]
    assert shop_id == "murugan_dairy" and cue == "warn"
    assert "6 Sakthi curd" in text and "Expiring soon" in text
    assert body["messages"] == [{"shop_id": "murugan_dairy", "say": text}]
    topic, payload = calls["events"][0]
    assert topic == "alerts"
    assert payload["shop_id"] == "murugan_dairy" and payload["kind"] == "expiry"
    assert payload["sku"] == "sakthi_curd_1l" and payload["message"] == text
    assert payload["days_left"] == 1 and payload["status"] == "expiring"


def test_expiry_job_one_event_per_alert_but_one_spoken_message(env):
    c, calls, state = env
    state["alerts"] = [dict(CURD), {"sku": "aavin_milk_500ml", "name": "Aavin milk", "qty": 4.0, "daysLeft": 0}]
    body = c.post("/jobs/expiry", headers=OK).json()
    assert body["alerts"] == 2
    assert len(calls["notify"]) == 1
    text = calls["notify"][0][1]
    assert "6 Sakthi curd" in text and "4 Aavin milk" in text and "4.0" not in text
    assert [p["sku"] for _, p in calls["events"]] == ["sakthi_curd_1l", "aavin_milk_500ml"]


def test_expiry_job_says_expired_items_are_expired(env):
    c, calls, state = env
    state["alerts"] = [{"sku": "aavin_paneer_200g", "name": "Aavin paneer", "qty": 2, "daysLeft": -1},
                       dict(CURD)]
    c.post("/jobs/expiry", headers=OK)
    text = calls["notify"][0][1]
    expired_at, soon_at = text.index("Expired"), text.index("Expiring soon")
    assert expired_at < soon_at  # the more urgent part first
    assert "2 Aavin paneer" in text[expired_at:soon_at]
    assert "6 Sakthi curd" in text[soon_at:]
    assert [p["status"] for _, p in calls["events"]] == ["expired", "expiring"]


def test_expiry_job_never_invents_a_quantity(env):
    c, calls, state = env
    state["alerts"] = [{"sku": "aavin_paneer_200g", "name": "Aavin paneer", "daysLeft": 1},
                       {"sku": "aavin_butter_100g", "qty": None}]
    c.post("/jobs/expiry", headers=OK)
    text = calls["notify"][0][1]
    assert "Aavin paneer" in text and "aavin_butter_100g" in text
    assert "None" not in text and not any(ch.isdigit() for ch in text.replace("100g", ""))


def test_expiry_job_quiet_when_nothing_expires(env):
    c, calls, state = env
    state["alerts"] = []
    body = c.post("/jobs/expiry", headers=OK).json()
    assert body["shops"] == 1 and body["alerts"] == 0 and body["messages"] == []
    assert calls["notify"] == [] and calls["events"] == []


def test_expiry_job_one_failing_shop_does_not_stop_the_others(env):
    c, calls, state = env
    state["shops"] = [("broken_shop", "en"), ("murugan_dairy", "en")]
    state["scan_error_for"] = {"broken_shop"}
    body = c.post("/jobs/expiry", headers=OK).json()
    assert body["shops"] == 2 and body["errors"] == 1 and body["alerts"] == 1
    assert [n[0] for n in calls["notify"]] == ["murugan_dairy"]


def test_expiry_job_still_speaks_when_event_publish_fails(env, monkeypatch):
    c, calls, _ = env

    async def boom(topic, payload):
        raise RuntimeError("events down")

    monkeypatch.setattr(jobs.events, "publish", boom)
    r = c.post("/jobs/expiry", headers=OK)
    assert r.status_code == 200 and r.json()["alerts"] == 1 and r.json()["errors"] == 0
    assert len(calls["notify"]) == 1


def test_expiry_job_single_shop_query_in_tamil(env):
    c, calls, _ = env
    r = c.post("/jobs/expiry?shop_id=murugan_dairy&lang=ta", headers=OK)
    assert r.status_code == 200
    assert calls["shops"] == 0 and calls["lang_of"] == []  # did not list every shop or look up the language
    text = calls["notify"][0][1]
    assert "Sakthi curd" in text and "காலாவதி" in text and "jobs." not in text


def test_expiry_job_single_shop_uses_the_shops_language(env):
    c, calls, state = env
    state["shop_lang"] = "hi"
    c.post("/jobs/expiry?shop_id=murugan_dairy", headers=OK)
    assert calls["lang_of"] == ["murugan_dairy"] and calls["shops"] == 0
    assert "एक्सपायर" in calls["notify"][0][1]


def test_expiry_job_rejects_unknown_lang(env):
    c, calls, _ = env
    assert c.post("/jobs/expiry?lang=fr", headers=OK).status_code == 422
    assert calls["notify"] == [] and calls["scan"] == []


@pytest.mark.parametrize("bad", ["a/b", "", "x" * 65, "shops%2Fother"])
def test_expiry_job_rejects_bad_shop_id(env, bad):
    c, calls, _ = env
    assert c.post(f"/jobs/expiry?shop_id={bad}", headers=OK).status_code == 422
    assert calls["scan"] == []


# --- /jobs/day-summary ---------------------------------------------------------------------------

def test_day_summary_speaks_sales_revenue_and_top_seller(env):
    c, calls, _ = env
    body = c.post("/jobs/day-summary", headers=OK).json()
    assert body["shops"] == 1 and body["notified_sessions"] == 1 and body["errors"] == 0
    shop_id, text, cue = calls["notify"][0]
    assert shop_id == "murugan_dairy" and cue == "done"
    assert "48" in text and "2860" in text and "Aavin milk" in text
    assert body["messages"] == [{"shop_id": "murugan_dairy", "say": text}]


def test_day_summary_adds_suggested_order_for_tomorrow(env):
    c, calls, state = env
    state["summary"]["suggested_orders"] = [{"sku": "sakthi_curd_1l", "qty": 30}]
    c.post("/jobs/day-summary", headers=OK)
    assert "30 Sakthi curd" in calls["notify"][0][1]


def test_day_summary_no_sales(env):
    c, calls, state = env
    state["summary"] = {"sales_count": 0, "revenue": 0, "top_sku": None, "suggested_orders": [], "names": {}}
    c.post("/jobs/day-summary", headers=OK)
    text = calls["notify"][0][1]
    assert "No sales" in text and "None" not in text


def test_day_summary_in_hindi(env):
    c, calls, state = env
    state["shops"] = [("murugan_dairy", "hi")]
    c.post("/jobs/day-summary", headers=OK)
    text = calls["notify"][0][1]
    assert "बिक्री" in text and "48" in text


def test_day_summary_failure_is_counted_not_invented(env, monkeypatch):
    c, calls, _ = env

    async def broken(shop_id):
        raise RuntimeError("firestore down")

    monkeypatch.setattr(jobs.summary, "today", broken)
    body = c.post("/jobs/day-summary", headers=OK).json()
    assert body["errors"] == 1 and body["messages"] == [] and calls["notify"] == []


def test_day_summary_matches_the_live_tool_text(env):
    c, calls, state = env
    state["summary"]["suggested_orders"] = [{"sku": "sakthi_curd_1l", "qty": 30}]
    c.post("/jobs/day-summary", headers=OK)
    assert calls["notify"][0][1] == summary.say(state["summary"], "en")


# --- /jobs/forecast-refresh ----------------------------------------------------------------------

def test_forecast_refresh_calls_refresh_model(env):
    c, calls, _ = env
    r = c.post("/jobs/forecast-refresh", headers=OK)
    assert r.status_code == 200 and r.json() == {"ok": True, "skus": 2, "with_forecast": 1}
    assert calls["refresh"] == [None]


def test_forecast_refresh_for_one_shop(env):
    c, calls, _ = env
    assert c.post("/jobs/forecast-refresh?shop_id=murugan_dairy", headers=OK).status_code == 200
    assert calls["refresh"] == ["murugan_dairy"]


# --- helpers -------------------------------------------------------------------------------------

def test_jobs_keys_exist_in_every_language():
    from aira_live.i18n import t
    for key in ("jobs.expiry_alert", "jobs.expired_alert"):
        for lang in ("en", "ta", "hi"):
            text = t(key, lang, items="X")
            assert text != key and "X" in text


def test_today_is_india_time():
    assert jobs.IST.key == "Asia/Kolkata"
    assert isinstance(jobs._today(), date)


async def test_shops_in_mock_mode_is_the_demo_shop(monkeypatch):
    monkeypatch.setattr(jobs.config, "load", lambda: SimpleNamespace(mock=True, demo_shop_id="murugan_dairy"))
    assert await jobs._shops() == [("murugan_dairy", "en")]
    assert await jobs._lang_of("murugan_dairy") == "en"


async def test_shops_reads_language_from_firestore(monkeypatch, fake_db):
    await fake_db.collection("shops").document("murugan_dairy").set({"name": "Murugan Dairy", "ownerUid": "u1"})
    await fake_db.collection("shops").document("other").set({"name": "Other", "lang": "hi"})
    await fake_db.collection("shops").document("bare").set({"name": "Bare", "ownerUid": "ghost", "lang": "fr"})
    await fake_db.collection("users").document("u1").set({"lang": "ta", "role": "owner"})
    monkeypatch.setattr(jobs.config, "load", lambda: SimpleNamespace(mock=False, demo_shop_id="murugan_dairy"))
    assert await jobs._shops() == [("bare", "en"), ("murugan_dairy", "ta"), ("other", "hi")]
    assert await jobs._lang_of("other") == "hi"
    assert await jobs._lang_of("missing") == "en"


# --- with Kanish's real business.expiry (skipped until that lane lands) --------------------------

@pytest.mark.skipif(importlib.util.find_spec("aira_live.business.expiry") is None,
                    reason="business.expiry (Kanish) not merged yet")
def test_expiry_job_with_the_real_scan(env, monkeypatch):
    from aira_live.business import store

    mem = store.MemoryStore()
    mem.data["shops/murugan_dairy/products/sakthi_curd_1l"] = {"name": "Sakthi curd"}
    mem.data["shops/murugan_dairy/inventory/sakthi_curd_1l"] = {
        "onHand": 6, "batches": [{"batchId": "b1", "qty": 6, "expiryDate": "2026-10-14"}]}
    store.set_store(mem)
    monkeypatch.setattr(jobs, "_scan", REAL_SCAN)
    try:
        c, calls, _ = env
        body = c.post("/jobs/expiry", headers=OK).json()
    finally:
        store.set_store(None)
    assert body["alerts"] == 1 and body["errors"] == 0
    assert "6 Sakthi curd" in calls["notify"][0][1]
