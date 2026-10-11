"""Supplier order channels (kanish/02 Task 1): click-to-chat helpers, WhatsApp Cloud API, Telegram, send_order."""
import json

import httpx
import pytest

import aira_live.messaging as messaging
from aira_live.business import paths
from aira_live.messaging import clicktochat, telegram, whatsapp
from aira_live.tools import _repo

SHOP = "sunrise_dairy"
ORDER = {"id": "o1", "items": [
    {"sku": "sakthi_curd_1l", "name": "Sakthi Curd 1 L", "qty": 30.0, "unit": "L", "agreedPrice": 50},
    {"sku": "aavin_paneer_200g", "name": "Aavin Paneer 200 g", "qty": 25.0, "unit": "pack", "agreedPrice": 80}]}


async def supplier_doc(store, sid: str) -> dict:
    return {"id": sid, **(await store.get(paths.supplier(SHOP, sid)))}


def _wa_env(monkeypatch, template=None):
    monkeypatch.setenv("WHATSAPP_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "123")
    if template:
        monkeypatch.setenv("WHATSAPP_TEMPLATE", template)


def _no_whatsapp_api(monkeypatch):
    async def must_not_send(*a, **kw):
        raise AssertionError("the WhatsApp API must not be called")

    monkeypatch.setattr(whatsapp, "send_template", must_not_send)


# ---------------------------------------------------------------- click-to-chat helpers

def test_phone_normalization_and_links():
    assert clicktochat.normalize_in_phone("+91 90000 00002") == "919000000002"
    assert clicktochat.normalize_in_phone("9000000002") == "919000000002"
    assert clicktochat.normalize_in_phone("09000000002") == "919000000002"
    assert clicktochat.normalize_in_phone("") == ""
    assert clicktochat.wa_link("9000000002", "30 L curd") == "https://wa.me/919000000002?text=30%20L%20curd"
    assert clicktochat.tel_link("9000000003") == "tel:+919000000003"
    assert clicktochat.tel_link("") == "tel:"                 # no number yet: open the dialer, never "tel:+"


def test_order_text_lists_quantities_and_units():
    assert clicktochat.items_text(ORDER["items"]) == "30 L Sakthi Curd 1 L, 25 pack Aavin Paneer 200 g"
    assert clicktochat.order_text("Sunrise Dairy", ORDER["items"]) == (
        "New order from Sunrise Dairy\n"
        "- 30 L Sakthi Curd 1 L\n"
        "- 25 pack Aavin Paneer 200 g\n"
        "Reply here to confirm.")


def test_order_text_adds_delivery_time_and_note_lines():
    text = clicktochat.order_text("Sunrise Dairy", ORDER["items"],
                                  delivery_by={"text": "6 am tomorrow", "at": "2026-10-12T06:00:00+05:30"},
                                  note="send fresh stock")
    assert text == (
        "New order from Sunrise Dairy\n"
        "- 30 L Sakthi Curd 1 L\n"
        "- 25 pack Aavin Paneer 200 g\n"
        "Deliver by: 6 am tomorrow (Mon 12 Oct, 6:00 am)\n"
        "Note: send fresh stock\n"
        "Reply here to confirm.")
    only_note = clicktochat.order_text("Sunrise Dairy", ORDER["items"], note="send fresh stock")
    assert "Deliver by:" not in only_note and "Note: send fresh stock\nReply here to confirm." in only_note


def test_order_summary_is_one_line_for_the_whatsapp_template():
    assert clicktochat.order_summary(ORDER["items"], {"text": "6 am tomorrow", "at": None}, "send fresh stock") == (
        "30 L Sakthi Curd 1 L, 25 pack Aavin Paneer 200 g; deliver by 6 am tomorrow; note: send fresh stock")
    assert clicktochat.order_summary(ORDER["items"], None, None) == "30 L Sakthi Curd 1 L, 25 pack Aavin Paneer 200 g"


def test_items_text_keeps_fractions():
    assert clicktochat.items_text([{"name": "Sakthi Curd 500 ml", "qty": 1.5, "unit": "L"}]) == \
        "1.5 L Sakthi Curd 500 ml"


# ---------------------------------------------------------------- WhatsApp Cloud API client

def test_whatsapp_configured_needs_token_and_phone_id(monkeypatch):
    assert whatsapp.configured() is False
    monkeypatch.setenv("WHATSAPP_TOKEN", "tok")
    assert whatsapp.configured() is False
    monkeypatch.setenv("WHATSAPP_PHONE_ID", "123")
    assert whatsapp.configured() is True


async def test_send_template_builds_graph_request(monkeypatch):
    _wa_env(monkeypatch)
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(url=str(req.url), auth=req.headers["authorization"], body=json.loads(req.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    mid = await whatsapp.send_template("+919000000002", "Sunrise Dairy", "30 L Sakthi Curd 1 L",
                                       transport=httpx.MockTransport(handler))
    assert mid == "wamid.ABC"
    assert seen["url"] == "https://graph.facebook.com/v21.0/123/messages"
    assert seen["auth"] == "Bearer tok"
    assert seen["body"]["messaging_product"] == "whatsapp" and seen["body"]["type"] == "template"
    assert seen["body"]["to"] == "919000000002"
    tpl = seen["body"]["template"]
    assert tpl["name"] == "order_request" and tpl["language"] == {"code": "en"}
    assert [p["text"] for p in tpl["components"][0]["parameters"]] == ["Sunrise Dairy", "30 L Sakthi Curd 1 L"]


async def test_send_template_graph_version_from_env(monkeypatch):
    _wa_env(monkeypatch)
    monkeypatch.setenv("WHATSAPP_GRAPH_VERSION", "v99.0")
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"messages": [{"id": "wamid.V"}]})

    await whatsapp.send_template("9000000002", "Sunrise Dairy", "x", transport=httpx.MockTransport(handler))
    assert seen["url"] == "https://graph.facebook.com/v99.0/123/messages"


async def test_hello_world_template_has_no_components(monkeypatch):
    _wa_env(monkeypatch, template="hello_world")
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.H"}]})

    await whatsapp.send_template("9000000002", "Sunrise Dairy", "x", transport=httpx.MockTransport(handler))
    assert seen["body"]["template"] == {"name": "hello_world", "language": {"code": "en_US"}}


async def test_send_template_http_error_raises(monkeypatch):
    _wa_env(monkeypatch)
    transport = httpx.MockTransport(lambda req: httpx.Response(401, json={"error": {"message": "bad token"}}))
    with pytest.raises(whatsapp.WhatsAppError):
        await whatsapp.send_template("9000000002", "Sunrise Dairy", "x", transport=transport)


async def test_send_template_without_message_id_raises(monkeypatch):
    _wa_env(monkeypatch)
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"messages": []}))
    with pytest.raises(whatsapp.WhatsAppError):
        await whatsapp.send_template("9000000002", "Sunrise Dairy", "x", transport=transport)


async def test_send_template_network_error_raises(monkeypatch):
    _wa_env(monkeypatch)

    def handler(req):
        raise httpx.ConnectError("no route", request=req)

    with pytest.raises(whatsapp.WhatsAppError):
        await whatsapp.send_template("9000000002", "Sunrise Dairy", "x", transport=httpx.MockTransport(handler))


async def test_send_template_not_configured_raises():
    with pytest.raises(whatsapp.WhatsAppError):
        await whatsapp.send_template("9000000002", "Sunrise Dairy", "x")


# ---------------------------------------------------------------- Telegram Bot API client

async def test_telegram_send_message_posts_to_bot_api(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "bot-tok")
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(url=str(req.url), body=json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    assert telegram.configured() is True
    mid = await telegram.send_message("777", "hi", transport=httpx.MockTransport(handler))
    assert mid == "42"
    assert seen["url"] == "https://api.telegram.org/botbot-tok/sendMessage"
    assert seen["body"] == {"chat_id": "777", "text": "hi"}


async def test_telegram_not_ok_raises(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "bot-tok")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(403, json={"ok": False, "description": "bot was blocked by the user"}))
    with pytest.raises(telegram.TelegramError):
        await telegram.send_message("777", "hi", transport=transport)


async def test_telegram_not_configured_raises():
    assert telegram.configured() is False
    with pytest.raises(telegram.TelegramError):
        await telegram.send_message("777", "hi")


# ---------------------------------------------------------------- send_order: channel choice and fallbacks

def test_whatsapp_gate_reads_runtime_flag(monkeypatch):
    from aira_live import remote_config

    asked = []

    def fake_flag(name, default):
        asked.append((name, default))
        return False

    monkeypatch.setattr(remote_config, "flag", fake_flag)
    assert messaging._whatsapp_enabled() is False
    assert asked == [("whatsapp_enabled", True)]


async def test_send_order_whatsapp_success_indexes_message(store, monkeypatch):
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: True)
    monkeypatch.setattr(whatsapp, "configured", lambda: True)

    async def fake_send(to, shop_name, items_text, **kw):
        assert (to, shop_name) == ("+919000000002", "Sunrise Dairy")
        assert items_text == "30 L Sakthi Curd 1 L, 25 pack Aavin Paneer 200 g; deliver by 6 am tomorrow"
        return "wamid.1"

    monkeypatch.setattr(whatsapp, "send_template", fake_send)
    order = {**ORDER, "deliveryBy": {"text": "6 am tomorrow", "at": None}}
    res = await messaging.send_order(SHOP, await supplier_doc(store, "sakthi_vendor"), order)
    assert res == {"channel": "whatsapp", "messageId": "wamid.1"}
    assert await _repo.lookup_message("whatsapp", "wamid.1") == {"shopId": SHOP, "orderId": "o1"}
    assert await _repo.lookup_contact("whatsapp", "919000000002") == {"shopId": SHOP, "supplierId": "sakthi_vendor"}
    # stored under the shop (server-only collections in the interfaces file)
    assert any(p.startswith(f"shops/{SHOP}/msgIndex/") for p in store.data)
    assert any(p.startswith(f"shops/{SHOP}/contactIndex/") for p in store.data)


async def test_send_order_falls_back_to_click_to_chat(store, monkeypatch):
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: True)
    monkeypatch.setattr(whatsapp, "configured", lambda: True)

    async def failing(*a, **kw):
        raise whatsapp.WhatsAppError("HTTP 500")

    monkeypatch.setattr(whatsapp, "send_template", failing)
    res = await messaging.send_order(SHOP, await supplier_doc(store, "sakthi_vendor"), ORDER)
    assert res["channel"] == "whatsapp_link"
    assert res["fallbackUrl"].startswith("https://wa.me/919000000002?text=New%20order%20from%20Sunrise%20Dairy")
    assert not any("/msgIndex/" in p for p in store.data)


async def test_send_order_flag_off_uses_click_to_chat(store, monkeypatch):
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: False)
    monkeypatch.setattr(whatsapp, "configured", lambda: True)
    _no_whatsapp_api(monkeypatch)
    res = await messaging.send_order(SHOP, await supplier_doc(store, "aavin_vendor"), ORDER)
    assert res["channel"] == "whatsapp_link" and "919000000001" in res["fallbackUrl"]


async def test_send_order_not_configured_uses_click_to_chat(store, monkeypatch):
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: True)
    _no_whatsapp_api(monkeypatch)
    res = await messaging.send_order(SHOP, await supplier_doc(store, "sakthi_vendor"), ORDER)
    assert res["channel"] == "whatsapp_link"


async def test_send_order_without_phone_never_calls_the_api(store, monkeypatch):
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: True)
    monkeypatch.setattr(whatsapp, "configured", lambda: True)
    _no_whatsapp_api(monkeypatch)
    supplier = {**(await supplier_doc(store, "sakthi_vendor")), "phone": ""}
    res = await messaging.send_order(SHOP, supplier, ORDER)
    assert res["channel"] == "whatsapp_link"
    assert res["fallbackUrl"].startswith("https://wa.me/?text=New%20order")


async def test_send_order_call_channel_returns_tel(store, monkeypatch):
    _no_whatsapp_api(monkeypatch)
    res = await messaging.send_order(SHOP, await supplier_doc(store, "arun_distributor"), ORDER)
    assert res == {"channel": "call", "fallbackUrl": "tel:+919000000003"}


async def test_send_order_telegram_channel_indexes_chat(store, monkeypatch):
    monkeypatch.setattr(telegram, "configured", lambda: True)
    _no_whatsapp_api(monkeypatch)

    async def fake_send(chat_id, text, **kw):
        assert chat_id == "555"
        assert text == ("New order from Sunrise Dairy\n- 30 L Sakthi Curd 1 L\n- 25 pack Aavin Paneer 200 g\n"
                        "Deliver by: 6 am tomorrow\nNote: send fresh stock\nReply here to confirm.")
        return "9"

    monkeypatch.setattr(telegram, "send_message", fake_send)
    supplier = {**(await supplier_doc(store, "sakthi_vendor")), "telegramChatId": 555}
    assert supplier["channel"] == "telegram"            # the catalog's vendor channel for now
    order = {**ORDER, "deliveryBy": {"text": "6 am tomorrow", "at": None}, "note": "send fresh stock"}
    res = await messaging.send_order(SHOP, supplier, order)
    assert res == {"channel": "telegram", "messageId": "9"}
    assert await _repo.lookup_message("telegram", "555:9") == {"shopId": SHOP, "orderId": "o1"}
    assert await _repo.lookup_contact("telegram", "555") == {"shopId": SHOP, "supplierId": "sakthi_vendor"}


async def test_send_order_telegram_without_chat_id_uses_whatsapp(store, monkeypatch):
    monkeypatch.setattr(telegram, "configured", lambda: True)
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: True)
    monkeypatch.setattr(whatsapp, "configured", lambda: True)

    async def no_telegram(*a, **kw):
        raise AssertionError("no chat id: Telegram must not be tried")

    async def fake_send(*a, **kw):
        return "wamid.2"

    monkeypatch.setattr(telegram, "send_message", no_telegram)
    monkeypatch.setattr(whatsapp, "send_template", fake_send)
    res = await messaging.send_order(SHOP, await supplier_doc(store, "sakthi_vendor"), ORDER)
    assert res == {"channel": "whatsapp", "messageId": "wamid.2"}


async def test_send_order_telegram_failure_falls_back_to_link(store, monkeypatch):
    monkeypatch.setattr(telegram, "configured", lambda: True)
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: False)

    async def failing(*a, **kw):
        raise telegram.TelegramError("chat not found")

    monkeypatch.setattr(telegram, "send_message", failing)
    supplier = {**(await supplier_doc(store, "aavin_vendor")), "telegramChatId": "555"}
    res = await messaging.send_order(SHOP, supplier, ORDER)
    assert res["channel"] == "whatsapp_link" and "919000000001" in res["fallbackUrl"]
    assert not any("/msgIndex/" in p for p in store.data)


# ---------------------------------------------------------------- Telegram is the vendor channel: say why it fell back
async def test_send_order_telegram_not_linked_falls_back_with_a_reason(store, monkeypatch):
    monkeypatch.setattr(telegram, "configured", lambda: True)
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: False)
    res = await messaging.send_order(SHOP, await supplier_doc(store, "sakthi_vendor"), ORDER)
    assert res["channel"] == "whatsapp_link" and "919000000002" in res["fallbackUrl"]
    assert res["reason"] == "telegram_not_linked"


async def test_send_order_telegram_not_configured_falls_back_with_a_reason(store, monkeypatch):
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: False)
    supplier = {**(await supplier_doc(store, "sakthi_vendor")), "telegramChatId": "555"}
    res = await messaging.send_order(SHOP, supplier, ORDER)       # TELEGRAM_TOKEN is cleared by the conftest
    assert res["channel"] == "whatsapp_link" and res["reason"] == "telegram_not_configured"


async def test_send_order_telegram_failure_reason(store, monkeypatch):
    monkeypatch.setattr(telegram, "configured", lambda: True)
    monkeypatch.setattr(messaging, "_whatsapp_enabled", lambda: False)

    async def failing(*a, **kw):
        raise telegram.TelegramError("chat not found")

    monkeypatch.setattr(telegram, "send_message", failing)
    supplier = {**(await supplier_doc(store, "aavin_vendor")), "telegramChatId": "555"}
    res = await messaging.send_order(SHOP, supplier, ORDER)
    assert res["reason"] == "telegram_failed"


async def test_send_order_whatsapp_fallback_has_no_telegram_reason(store, monkeypatch):
    supplier = {**(await supplier_doc(store, "sakthi_vendor")), "channel": "whatsapp"}
    res = await messaging.send_order(SHOP, supplier, ORDER)
    assert res["channel"] == "whatsapp_link" and "reason" not in res


async def test_a_database_blip_after_sending_still_reports_the_order_as_sent(store, monkeypatch):
    """Telegram delivered it; a failed index write must not make the owner's retry send it again."""
    monkeypatch.setattr(telegram, "configured", lambda: True)
    _no_whatsapp_api(monkeypatch)

    async def fake_send(chat_id, text, **kw):
        return "9"

    async def broken(*a, **k):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(telegram, "send_message", fake_send)
    monkeypatch.setattr(_repo, "index_message", broken)
    monkeypatch.setattr(_repo, "index_contact", broken)
    supplier = {**(await supplier_doc(store, "sakthi_vendor")), "telegramChatId": 555}
    res = await messaging.send_order(SHOP, supplier, ORDER)
    assert res == {"channel": "telegram", "messageId": "9"}
