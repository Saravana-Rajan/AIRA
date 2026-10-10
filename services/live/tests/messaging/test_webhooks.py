"""Vendor-reply webhooks (kanish/02 Task 2): WhatsApp + Telegram replies -> order.vendorReply + sessions.notify.

Fixtures (`store`, `notified`, offline messaging env) come from tests/messaging/conftest.py.
"""
import hashlib
import hmac
import json
import string
from pathlib import Path

import httpx
from fastapi import FastAPI

from aira_live.messaging.webhooks import router
from aira_live.tools import _repo

SHOP = "murugan_dairy"
I18N_DIR = Path(__file__).resolve().parents[2] / "aira_live" / "i18n"
# Every k2.* key the later kanish/02 tasks speak (Task 2 adds them in en, ta and hi).
K2_KEYS = {
    "k2.order.read_back", "k2.order.sent", "k2.order.fallback", "k2.order.call", "k2.order.mark_sent_q",
    "k2.order.already", "k2.order.vendor_replied", "k2.order.none", "k2.order.status", "k2.order.status_reply",
    "k2.qty.both",
    "k2.delivery.started", "k2.delivery.none", "k2.delivery.readback", "k2.delivery.saved",
    "k2.delivery.nothing_seen",
    "k2.invoice.match",
    "k2.pay.plan", "k2.pay.cash_q", "k2.pay.cash_wrong", "k2.pay.upi_q", "k2.pay.done", "k2.pay.not_ready",
    "k2.cash.unclear",
    "k2.sale.price", "k2.sale.none", "k2.sale.change", "k2.sale.change_wrong", "k2.sale.cash_q", "k2.sale.upi_wait",
    "k2.sale.upi_heard_q", "k2.sale.upi_wrong", "k2.sale.upi_timeout_q", "k2.sale.done", "k2.sale.low_stock",
    "k2.stock.one", "k2.stock.all",
    "k2.expiry.none", "k2.expiry.list", "k2.expiry.item",
    "k2.when.expired", "k2.when.today", "k2.when.tomorrow",
}

app = FastAPI()
app.include_router(router)


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def wa_payload(frm: str, text: str, context_id: str | None = None) -> dict:
    msg = {"from": frm, "id": "wamid.IN", "type": "text", "text": {"body": text}}
    if context_id:
        msg["context"] = {"id": context_id}
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "messages", "value": {"messages": [msg]}}]}]}


async def _order(oid: str, state: str, updated: str, lang: str = "en") -> None:
    await _repo.save_order(SHOP, {"id": oid, "supplierId": "sakthi_vendor", "supplierName": "Sakthi curd vendor",
                                  "state": state, "lang": lang, "items": [], "updatedAt": updated})


# ---------------------------------------------------------------- k2 speech keys
def test_k2_keys_present_in_every_language_with_the_same_placeholders():
    def fields(s: str) -> set[str]:
        return {f for _, f, _, _ in string.Formatter().parse(s) if f}

    cats = {lang: json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8")) for lang in ("en", "ta", "hi")}
    for lang, cat in cats.items():
        missing = K2_KEYS - set(cat)
        assert not missing, (lang, sorted(missing))
        assert all(cat[k].strip() for k in K2_KEYS), lang
    for key in K2_KEYS:
        assert fields(cats["ta"][key]) == fields(cats["en"][key]), ("ta", key)
        assert fields(cats["hi"][key]) == fields(cats["en"][key]), ("hi", key)


# ---------------------------------------------------------------- WhatsApp verification handshake
async def test_verify_ok(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v123")
    async with client() as c:
        r = await c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "v123",
                                                     "hub.challenge": "4242"})
    assert r.status_code == 200 and r.text == "4242"


async def test_verify_bad_token_403(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "v123")
    async with client() as c:
        r = await c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope",
                                                     "hub.challenge": "1"})
    assert r.status_code == 403


async def test_verify_without_configured_token_403():
    async with client() as c:          # WHATSAPP_VERIFY_TOKEN is cleared by the conftest
        r = await c.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "",
                                                     "hub.challenge": "1"})
    assert r.status_code == 403


# ---------------------------------------------------------------- WhatsApp replies
async def test_vendor_reply_by_context_id_updates_order_and_notifies(store, notified):
    await _order("o1", "ORDER_PLACED", "2026-10-12T09:00:00")
    await _repo.index_message("whatsapp", "wamid.1", SHOP, "o1")
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", json=wa_payload("919000000002", "OK, delivery at 6 pm", "wamid.1"))
    assert r.status_code == 200 and r.json()["handled"] == 1
    reply = (await _repo.get_order(SHOP, "o1"))["vendorReply"]
    assert reply["text"] == "OK, delivery at 6 pm" and reply["channel"] == "whatsapp" and reply["at"]
    assert notified == [(SHOP, "Sakthi curd vendor replied: OK, delivery at 6 pm", "ack")]


async def test_vendor_reply_is_spoken_in_the_order_language(store, notified):
    await _order("o1", "ORDER_PLACED", "2026-10-12T09:00:00", lang="ta")
    await _repo.index_message("whatsapp", "wamid.1", SHOP, "o1")
    async with client() as c:
        await c.post("/webhooks/whatsapp", json=wa_payload("919000000002", "சரி", "wamid.1"))
    assert notified == [(SHOP, "Sakthi curd vendor பதில்: சரி", "ack")]


async def test_vendor_reply_by_sender_phone_matches_latest_open_order(store, notified):
    await _order("o1", "ORDER_PLACED", "2026-10-12T08:00:00")
    await _order("o2", "DELIVERY_PENDING", "2026-10-12T09:30:00")
    await _repo.index_contact("whatsapp", "919000000002", SHOP, "sakthi_vendor")
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", json=wa_payload("919000000002", "Coming in 10 minutes"))
    assert r.json()["handled"] == 1
    assert (await _repo.get_order(SHOP, "o2"))["vendorReply"]["text"] == "Coming in 10 minutes"
    assert "vendorReply" not in await _repo.get_order(SHOP, "o1")


async def test_vendor_reply_by_sender_with_plus_prefix_is_normalized(store, notified):
    await _order("o1", "ORDER_PLACED", "2026-10-12T08:00:00")
    await _repo.index_contact("whatsapp", "919000000002", SHOP, "sakthi_vendor")
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", json=wa_payload("+91 90000 00002", "Done"))
    assert r.json()["handled"] == 1


async def test_vendor_reply_ignores_closed_orders(store, notified):
    await _order("o9", "CLOSED", "2026-10-12T11:00:00")
    await _repo.index_contact("whatsapp", "919000000002", SHOP, "sakthi_vendor")
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", json=wa_payload("919000000002", "Thanks"))
    assert r.status_code == 200 and r.json()["handled"] == 0 and notified == []
    assert "vendorReply" not in await _repo.get_order(SHOP, "o9")


async def test_webhook_unknown_sender_is_ignored(store, notified):
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", json=wa_payload("919999999999", "hello"))
    assert r.status_code == 200 and r.json()["handled"] == 0 and notified == []


async def test_webhook_status_only_payload_is_ignored(store, notified):
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "statuses": [{"id": "wamid.1", "status": "delivered", "recipient_id": "919000000002"}]}}]}]}
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", json=payload)
    assert r.status_code == 200 and r.json()["handled"] == 0 and notified == []


async def test_webhook_malformed_payload_is_200(store, notified):
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", content=b"not json", headers={"content-type": "application/json"})
        odd = await c.post("/webhooks/whatsapp", json={"entry": ["x", {"changes": [None, {"value": "y"}]}]})
        arr = await c.post("/webhooks/whatsapp", json=[1, 2])
    for resp in (r, odd, arr):
        assert resp.status_code == 200 and resp.json()["handled"] == 0
    assert notified == []


async def test_webhook_bad_signature_403(store, notified, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
    body = json.dumps(wa_payload("919000000002", "hi")).encode()
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    async with client() as c:
        bad = await c.post("/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": "sha256=dead"})
        missing = await c.post("/webhooks/whatsapp", content=body)
        ok = await c.post("/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": good})
    assert bad.status_code == 403 and missing.status_code == 403 and ok.status_code == 200


async def test_one_failing_message_does_not_block_the_rest(store, notified, monkeypatch):
    await _order("o1", "ORDER_PLACED", "2026-10-12T09:00:00")
    await _repo.index_message("whatsapp", "wamid.1", SHOP, "o1")
    real_lookup = _repo.lookup_message

    async def flaky(channel, key, shop_id=None):
        if key == "wamid.boom":
            raise RuntimeError("store down")
        return await real_lookup(channel, key, shop_id)

    monkeypatch.setattr(_repo, "lookup_message", flaky)
    first = wa_payload("919000000002", "first", "wamid.boom")
    second = wa_payload("919000000002", "second", "wamid.1")
    payload = {"object": "whatsapp_business_account", "entry": first["entry"] + second["entry"]}
    async with client() as c:
        r = await c.post("/webhooks/whatsapp", json=payload)
    assert r.status_code == 200 and r.json()["handled"] == 1
    assert (await _repo.get_order(SHOP, "o1"))["vendorReply"]["text"] == "second"


# ---------------------------------------------------------------- Telegram replies
async def test_telegram_reply_by_reply_to_message(store, notified):
    await _order("o3", "ORDER_PLACED", "2026-10-12T10:00:00")
    await _repo.index_message("telegram", "555:77", SHOP, "o3")
    update = {"message": {"chat": {"id": 555}, "text": "Will deliver at 5", "reply_to_message": {"message_id": 77}}}
    async with client() as c:
        r = await c.post("/webhooks/telegram", json=update)
    assert r.json()["handled"] == 1
    assert (await _repo.get_order(SHOP, "o3"))["vendorReply"]["channel"] == "telegram"
    assert notified == [(SHOP, "Sakthi curd vendor replied: Will deliver at 5", "ack")]


async def test_telegram_reply_by_chat_matches_latest_open_order(store, notified):
    await _order("o4", "ORDER_PLACED", "2026-10-12T10:00:00")
    await _repo.index_contact("telegram", "555", SHOP, "sakthi_vendor")
    async with client() as c:
        r = await c.post("/webhooks/telegram", json={"message": {"chat": {"id": 555}, "text": "On the way"}})
    assert r.json()["handled"] == 1
    assert (await _repo.get_order(SHOP, "o4"))["vendorReply"]["text"] == "On the way"


async def test_telegram_secret_and_malformed(store, notified, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "tg-secret")
    update = {"message": {"chat": {"id": 555}, "text": "hi"}}
    hdr = {"x-telegram-bot-api-secret-token": "tg-secret"}
    async with client() as c:
        bad = await c.post("/webhooks/telegram", json=update, headers={"x-telegram-bot-api-secret-token": "nope"})
        ok = await c.post("/webhooks/telegram", json=update, headers=hdr)
        junk = await c.post("/webhooks/telegram", content=b"{", headers={**hdr, "content-type": "application/json"})
        arr = await c.post("/webhooks/telegram", json=[1], headers=hdr)
    assert bad.status_code == 403
    assert ok.status_code == 200 and ok.json()["handled"] == 0          # unknown chat -> ignored, still 200
    assert junk.status_code == 200 and junk.json()["handled"] == 0
    assert arr.status_code == 200 and arr.json()["handled"] == 0
    assert notified == []
