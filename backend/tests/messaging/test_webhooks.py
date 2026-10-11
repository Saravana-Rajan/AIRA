"""Vendor-reply webhooks (kanish/02 Task 2): WhatsApp + Telegram replies -> order.vendorReply + sessions.notify.

Fixtures (`store`, `notified`, offline messaging env) come from tests/messaging/conftest.py.
"""
import hashlib
import hmac
import json
import re
import string
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from aira_live.messaging import webhooks as wh
from aira_live.messaging.webhooks import router
from aira_live.tools import _repo

SHOP = "sunrise_dairy"
I18N_DIR = Path(__file__).resolve().parents[2] / "aira_live" / "i18n"
# Every k2.* key the later kanish/02 tasks speak (Task 2 adds them in en, ta and hi).
K2_KEYS = {
    "k2.order.read_back", "k2.order.sent", "k2.order.fallback", "k2.order.call", "k2.order.mark_sent_q",
    "k2.order.already", "k2.order.vendor_replied", "k2.order.none", "k2.order.status", "k2.order.status_reply",
    "k2.qty.both", "k2.order.no_telegram",
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
WA_SECRET = "wa-app-secret"


@pytest.fixture(autouse=True)
def _whatsapp_secret(monkeypatch):
    """WhatsApp bodies are accepted only with a valid X-Hub-Signature-256 (fails closed without the secret)."""
    monkeypatch.setenv("WHATSAPP_APP_SECRET", WA_SECRET)


async def _sign(request: httpx.Request) -> None:
    if (request.method == "POST" and request.url.path == "/webhooks/whatsapp"
            and "x-hub-signature-256" not in request.headers):
        digest = hmac.new(WA_SECRET.encode(), request.content, hashlib.sha256).hexdigest()
        request.headers["x-hub-signature-256"] = "sha256=" + digest


def client(sign: bool = True) -> httpx.AsyncClient:
    """Test client; WhatsApp POSTs are signed the way Meta signs them unless ``sign=False``."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             event_hooks={"request": [_sign]} if sign else {})


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


async def test_whatsapp_rejects_every_unsigned_body_when_the_secret_is_unset(store, notified, monkeypatch):
    """Fail closed like Telegram: with no WHATSAPP_APP_SECRET nothing on the public URL can speak to the owner."""
    monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
    await _order("o1", "ORDER_PLACED", "2026-10-12T09:00:00")
    body = json.dumps(wa_payload("919000000002", "send 5000 to fake@upi")).encode()
    async with client(sign=False) as c:
        unsigned = await c.post("/webhooks/whatsapp", content=body)
        signed = await c.post("/webhooks/whatsapp", content=body, headers={
            "x-hub-signature-256": "sha256=" + hmac.new(b"anything", body, hashlib.sha256).hexdigest()})
    assert unsigned.status_code == 403 and signed.status_code == 403
    assert "vendorReply" not in (await _repo.get_order(SHOP, "o1")) and notified == []


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
TG_SECRET = "tg-secret"
TG = {"x-telegram-bot-api-secret-token": TG_SECRET}


@pytest.fixture(autouse=True)
def _telegram_secret(monkeypatch):
    """Telegram updates are accepted only with the webhook secret (set per deploy, never in git)."""
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", TG_SECRET)


async def test_telegram_reply_by_reply_to_message(store, notified):
    await _order("o3", "ORDER_PLACED", "2026-10-12T10:00:00")
    await _repo.index_message("telegram", "555:77", SHOP, "o3")
    update = {"message": {"chat": {"id": 555}, "text": "Will deliver at 5", "reply_to_message": {"message_id": 77}}}
    async with client() as c:
        r = await c.post("/webhooks/telegram", json=update, headers=TG)
    assert r.json()["handled"] == 1
    assert (await _repo.get_order(SHOP, "o3"))["vendorReply"]["channel"] == "telegram"
    assert notified == [(SHOP, "Sakthi curd vendor replied: Will deliver at 5", "ack")]


async def test_telegram_reply_by_chat_matches_latest_open_order(store, notified):
    await _order("o4", "ORDER_PLACED", "2026-10-12T10:00:00")
    await _repo.index_contact("telegram", "555", SHOP, "sakthi_vendor")
    async with client() as c:
        r = await c.post("/webhooks/telegram", json={"message": {"chat": {"id": 555}, "text": "On the way"}},
                         headers=TG)
    assert r.json()["handled"] == 1
    assert (await _repo.get_order(SHOP, "o4"))["vendorReply"]["text"] == "On the way"


async def test_telegram_secret_and_malformed(store, notified, monkeypatch):
    update = {"message": {"chat": {"id": 555}, "text": "hi"}}
    hdr = TG
    async with client() as c:
        bad = await c.post("/webhooks/telegram", json=update, headers={"x-telegram-bot-api-secret-token": "nope"})
        missing = await c.post("/webhooks/telegram", json=update)
        ok = await c.post("/webhooks/telegram", json=update, headers=hdr)
        junk = await c.post("/webhooks/telegram", content=b"{", headers={**hdr, "content-type": "application/json"})
        arr = await c.post("/webhooks/telegram", json=[1], headers=hdr)
    assert bad.status_code == 403
    assert missing.status_code == 403
    assert ok.status_code == 200 and ok.json()["handled"] == 0          # unknown chat -> ignored, still 200
    assert junk.status_code == 200 and junk.json()["handled"] == 0
    assert arr.status_code == 200 and arr.json()["handled"] == 0
    assert notified == []


async def test_telegram_rejects_every_update_when_the_secret_is_unset(store, notified, monkeypatch):
    """Without TELEGRAM_WEBHOOK_SECRET nothing can prove an update came from Telegram, so all of them are refused."""
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    update = {"message": {"chat": {"id": 555}, "text": "hi"}}
    async with client() as c:
        r = await c.post("/webhooks/telegram", json=update, headers=TG)
        bare = await c.post("/webhooks/telegram", json=update)
    assert r.status_code == 403 and bare.status_code == 403
    assert notified == []


# ---------------------------------------------------------------- Telegram /start deep link (vendor onboarding)
@pytest.fixture
def tg_sent(monkeypatch):
    """Fake telegram.send_message: records [(chat_id, text), ...]; nothing reaches Telegram."""
    from aira_live.messaging import telegram

    sent: list[tuple[str, str]] = []

    async def fake_send(chat_id: str, text: str, **kw) -> str:
        sent.append((chat_id, text))
        return str(100 + len(sent))

    monkeypatch.setattr(telegram, "send_message", fake_send)
    monkeypatch.setattr(telegram, "configured", lambda: True)
    return sent


def start(chat_id: int, text: str) -> dict:
    return {"update_id": 1, "message": {"message_id": 5, "chat": {"id": chat_id, "type": "private"}, "text": text}}


def tagged(supplier_id: str, secret: str = TG_SECRET) -> str:
    """The deep-link start param: <supplier_id>.<first 20 hex of HMAC-SHA256(webhook secret, supplier_id)>."""
    return f"{supplier_id}.{hmac.new(secret.encode(), supplier_id.encode(), hashlib.sha256).hexdigest()[:20]}"


async def _chat_of(store, supplier_id: str) -> str | None:
    return (await store.get(f"shops/{SHOP}/suppliers/{supplier_id}")).get("telegramChatId")


def test_start_param_is_the_supplier_id_with_its_hmac_tag(monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", TG_SECRET)
    param = wh.start_param("sakthi_vendor")
    assert param == tagged("sakthi_vendor") and re.fullmatch(r"sakthi_vendor\.[0-9a-f]{20}", param)
    assert wh.start_param("aavin_vendor") != param.replace("sakthi_vendor", "aavin_vendor")  # per supplier
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "another")
    assert wh.start_param("sakthi_vendor") != param  # per secret
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET")
    with pytest.raises(RuntimeError):
        wh.start_param("sakthi_vendor")  # no secret: no link can be made (and the webhook is closed anyway)


async def test_start_with_a_tagged_link_links_the_chat_and_replies(store, notified, tg_sent):
    async with client() as c:
        r = await c.post("/webhooks/telegram", json=start(777, f"/start {tagged('sakthi_vendor')}"), headers=TG)
    assert r.status_code == 200 and r.json() == {"ok": True, "handled": 1, "linked": "sakthi_vendor"}
    doc = await store.get(f"shops/{SHOP}/suppliers/sakthi_vendor")
    assert doc["telegramChatId"] == "777"
    assert doc["channel"] == "telegram"                                   # the catalog's channel is untouched
    assert await _repo.lookup_contact("telegram", "777") == {"shopId": SHOP, "supplierId": "sakthi_vendor"}
    assert tg_sent == [("777", "AIRA: you will receive Sunrise Dairy orders here.")]
    assert notified == []                                                 # onboarding is not spoken to the owner


async def test_untagged_start_never_links_even_the_first_time(store, notified, tg_sent):
    """The supplier ids are public (shared/catalog): a bare /start <supplier_id> must not link any chat."""
    async with client() as c:
        bare = await c.post("/webhooks/telegram", json=start(777, "/start sakthi_vendor"), headers=TG)
        bot = await c.post("/webhooks/telegram", json=start(777, "/start@AiraShopBot aavin_vendor"), headers=TG)
    assert bare.json() == {"ok": True, "handled": 0} and bot.json() == {"ok": True, "handled": 0}
    for sid in ("sakthi_vendor", "aavin_vendor"):
        assert "telegramChatId" not in (await store.get(f"shops/{SHOP}/suppliers/{sid}"))
    assert await _repo.lookup_contact("telegram", "777") is None
    assert tg_sent == []


async def test_start_with_a_wrong_or_borrowed_tag_is_ignored(store, notified, tg_sent):
    good = tagged("sakthi_vendor")
    wrong = good[:-1] + ("0" if good[-1] != "0" else "1")
    borrowed = "sakthi_vendor." + tagged("aavin_vendor").split(".")[1]   # another supplier's tag
    other_secret = tagged("sakthi_vendor", "not-the-secret")
    async with client() as c:
        for param in (wrong, borrowed, other_secret, "sakthi_vendor.", "sakthi_vendor-abc123"):
            r = await c.post("/webhooks/telegram", json=start(777, f"/start {param}"), headers=TG)
            assert r.json() == {"ok": True, "handled": 0}, param
    assert "telegramChatId" not in (await store.get(f"shops/{SHOP}/suppliers/sakthi_vendor"))
    assert tg_sent == []


async def test_the_same_chat_may_press_start_again(store, notified, tg_sent):
    async with client() as c:
        await c.post("/webhooks/telegram", json=start(777, f"/start {tagged('sakthi_vendor')}"), headers=TG)
        again = await c.post("/webhooks/telegram", json=start(777, f"/start@AiraShopBot {tagged('sakthi_vendor')}"),
                             headers=TG)
    assert again.json()["linked"] == "sakthi_vendor"
    assert await _chat_of(store, "sakthi_vendor") == "777"
    assert [c for c, _ in tg_sent] == ["777", "777"]


async def test_a_new_phone_with_the_link_takes_over_and_the_old_chat_is_unlinked(store, notified, tg_sent):
    """The tagged link is the credential: whoever holds it speaks for the vendor. Moving phones needs no code."""
    async with client() as c:
        await c.post("/webhooks/telegram", json=start(777, f"/start {tagged('sakthi_vendor')}"), headers=TG)
        ok = await c.post("/webhooks/telegram", json=start(888, f"/start {tagged('sakthi_vendor')}"), headers=TG)
    assert ok.json() == {"ok": True, "handled": 1, "linked": "sakthi_vendor"}
    assert await _chat_of(store, "sakthi_vendor") == "888"
    assert await _repo.lookup_contact("telegram", "888") == {"shopId": SHOP, "supplierId": "sakthi_vendor"}
    assert (await _repo.lookup_contact("telegram", "777") or {}).get("supplierId") is None  # unlinked
    assert [c for c, _ in tg_sent] == ["777", "888"]
    # the old phone no longer speaks for the vendor
    await _order("o8", "ORDER_PLACED", "2026-10-12T10:00:00")
    async with client() as c:
        old = await c.post("/webhooks/telegram", json={"message": {"chat": {"id": 777}, "text": "OK"}}, headers=TG)
    assert old.json()["handled"] == 0 and notified == []


async def test_start_with_an_unknown_or_missing_id_is_ignored(store, notified, tg_sent):
    async with client() as c:
        unknown = await c.post("/webhooks/telegram", json=start(777, f"/start {tagged('nobody_vendor')}"), headers=TG)
        bare = await c.post("/webhooks/telegram", json=start(777, "/start"), headers=TG)
        path = await c.post("/webhooks/telegram", json=start(777, f"/start ../{tagged('sakthi_vendor')}"), headers=TG)
    for r in (unknown, bare, path):
        assert r.status_code == 200 and r.json() == {"ok": True, "handled": 0}
    assert tg_sent == []
    assert await _repo.lookup_contact("telegram", "777") is None
    for sid in ("sakthi_vendor", "aavin_vendor", "arun_distributor"):
        assert "telegramChatId" not in (await store.get(f"shops/{SHOP}/suppliers/{sid}"))


async def test_start_links_even_when_the_welcome_cannot_be_sent(store, notified, monkeypatch):
    from aira_live.messaging import telegram

    async def failing(chat_id: str, text: str, **kw) -> str:
        raise telegram.TelegramError("blocked")

    monkeypatch.setattr(telegram, "send_message", failing)
    monkeypatch.setattr(telegram, "configured", lambda: True)
    async with client() as c:
        r = await c.post("/webhooks/telegram", json=start(777, f"/start {tagged('aavin_vendor')}"), headers=TG)
    assert r.json() == {"ok": True, "handled": 1, "linked": "aavin_vendor"}
    assert (await store.get(f"shops/{SHOP}/suppliers/aavin_vendor"))["telegramChatId"] == "777"


async def test_a_linked_vendor_reply_reaches_the_owner_after_start(store, notified, tg_sent):
    """The whole onboarding loop: /start links the chat, the next order is matched by that chat id."""
    async with client() as c:
        await c.post("/webhooks/telegram", json=start(777, f"/start {tagged('sakthi_vendor')}"), headers=TG)
    await _order("o9", "ORDER_PLACED", "2026-10-12T10:00:00")
    async with client() as c:
        r = await c.post("/webhooks/telegram", json={"message": {"chat": {"id": 777}, "text": "OK 6 pm"}}, headers=TG)
    assert r.json()["handled"] == 1
    assert (await _repo.get_order(SHOP, "o9"))["vendorReply"]["text"] == "OK 6 pm"
    assert notified == [(SHOP, "Sakthi curd vendor replied: OK 6 pm", "ack")]
