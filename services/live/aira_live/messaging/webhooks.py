"""Vendor replies -> order.vendorReply, spoken to the owner via sessions.notify.

Routes (mounted by main.py through OPTIONAL_ROUTERS):
  GET  /webhooks/whatsapp   Meta verification handshake (hub.mode / hub.verify_token / hub.challenge)
  POST /webhooks/whatsapp   WhatsApp Cloud API message notifications
  POST /webhooks/telegram   Telegram Bot API updates (vendor replies, and "/start <supplier_id>" onboarding)

Telegram updates are accepted only with the X-Telegram-Bot-Api-Secret-Token header matching env
TELEGRAM_WEBHOOK_SECRET (constant-time compare); with the secret unset every update is refused.
A reply is matched to an order by the message it answers (msgIndex), else by the sender (contactIndex) to that
supplier's latest open order. Always 200 for unknown senders or malformed bodies (Meta retries non-200
responses); 403 only for bad auth (WhatsApp signature / verify token, Telegram secret token).
A reply is evidence of what the vendor said, nothing more: it never changes the order's state.
"""
import hashlib
import hmac
import json
import logging
import os
import re
from collections.abc import Iterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from aira_live import config
from aira_live.business import paths
from aira_live.business.clock import now_iso
from aira_live.business.store import get_store
from aira_live.i18n import t
from aira_live.messaging import telegram
from aira_live.messaging.clicktochat import normalize_in_phone
from aira_live.tools import _repo

router = APIRouter()
log = logging.getLogger(__name__)
OPEN_STATES = ["ORDER_PLACED", "DELIVERY_PENDING"]

# Logged once at import (startup): each webhook is disabled until its secret is mounted.
for _env, _route in (("WHATSAPP_APP_SECRET", "/webhooks/whatsapp"), ("TELEGRAM_WEBHOOK_SECRET", "/webhooks/telegram")):
    if not os.getenv(_env):
        log.warning("%s is not set: POST %s refuses every request (403) until it is", _env, _route)


def _ignored() -> dict:
    return {"ok": True, "handled": 0}


async def _notify(shop_id: str, text: str) -> None:
    from aira_live import sessions          # looked up at call time so tests can monkeypatch sessions.notify
    await sessions.notify(shop_id, text, "ack")


# ---------------------------------------------------------------- WhatsApp
@router.get("/webhooks/whatsapp")
async def whatsapp_verify(request: Request):
    q = request.query_params
    expected = os.getenv("WHATSAPP_VERIFY_TOKEN")
    token = q.get("hub.verify_token") or ""
    if q.get("hub.mode") == "subscribe" and expected and hmac.compare_digest(token, expected):
        return PlainTextResponse(q.get("hub.challenge", ""))
    return Response(status_code=403)


def _signature_ok(body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(app secret, raw body).

    Fails closed: with no WHATSAPP_APP_SECRET every body is refused, so nothing on the public URL can write a
    vendor reply or speak to the owner. (The deploy mounts the secret; whatsapp-setup.md sets its real value.)
    """
    secret = os.getenv("WHATSAPP_APP_SECRET")
    if not secret:
        return False
    if not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header[len("sha256="):])


def _dicts(value) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _wa_text_messages(payload: dict) -> Iterator[tuple[str, str, str | None]]:
    """(from, text, context message id) for every inbound text message; statuses and other types are skipped."""
    for entry in _dicts(payload.get("entry")):
        for change in _dicts(entry.get("changes")):
            value = change.get("value")
            for m in _dicts(value.get("messages") if isinstance(value, dict) else None):
                body = m.get("text")
                text = body.get("body") if isinstance(body, dict) else None
                if m.get("type") != "text" or not isinstance(text, str) or not text.strip():
                    continue
                ctx = m.get("context")
                ctx_id = ctx.get("id") if isinstance(ctx, dict) else None
                yield str(m.get("from", "")), text.strip(), (str(ctx_id) if ctx_id else None)


@router.post("/webhooks/whatsapp")
async def whatsapp_in(request: Request):
    body = await request.body()
    if not _signature_ok(body, request.headers.get("x-hub-signature-256")):
        return Response(status_code=403)
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return _ignored()
    if not isinstance(payload, dict):
        return _ignored()
    handled = 0
    for frm, text, ctx in _wa_text_messages(payload):
        try:
            handled += int(await _apply_reply("whatsapp", ctx, normalize_in_phone(frm), text))
        except Exception:                                     # one bad message never blocks the rest
            log.exception("whatsapp vendor reply failed from=%s", frm)
    return {"ok": True, "handled": handled}


# ---------------------------------------------------------------- Telegram
# Vendor onboarding by deep link: the vendor opens https://t.me/<bot>?start=<supplier_id>.<tag> and presses Start,
# which sends "/start <supplier_id>.<tag>". The sender's chat id becomes supplier.telegramChatId
# (scripts/telegram_setup.py prints the links; docs/ops/telegram-setup.md has the steps).
# The supplier ids are public (content/catalog), so the link carries ``start_tag``: the first 12 hex digits of
# HMAC-SHA256(TELEGRAM_WEBHOOK_SECRET, supplier_id). Without the right tag a /start links nobody.
START_RE = re.compile(r"^/start(?:@\w+)?\s+([a-z0-9_]{1,64})\.([0-9a-f]{12})\s*$", re.IGNORECASE)
TAG_LEN = 12
WELCOME = "AIRA: you will receive {shop} orders here."


def start_tag(supplier_id: str, secret: str) -> str:
    """The deep-link tag for a supplier (scripts/telegram_setup.py computes the same with stdlib hmac)."""
    return hmac.new(secret.encode(), supplier_id.encode(), hashlib.sha256).hexdigest()[:TAG_LEN]


def start_param(supplier_id: str, secret: str) -> str:
    """The ``?start=`` value of a vendor's deep link: ``<supplier_id>.<tag>``."""
    return f"{supplier_id}.{start_tag(supplier_id, secret)}"


def _telegram_secret_ok(header: str | None) -> bool:
    """Constant-time check of X-Telegram-Bot-Api-Secret-Token. With no secret configured every update is refused."""
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if not secret:
        return False
    return hmac.compare_digest((header or "").encode(), secret.encode())


async def _link_supplier(chat_id: str, supplier_id: str, tag: str) -> str | None:
    """`/start <supplier_id>.<tag>` from a known supplier of the demo shop: store the chat id and welcome the vendor.

    Unknown ids and wrong tags are ignored (nothing is written, nothing is sent). A later /start with the same
    supplier link overwrites the chat id, so a vendor can move to a new phone. The welcome is best effort: the
    link is kept even when Telegram refuses the message.
    """
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET") or ""
    if not secret or not hmac.compare_digest(tag.lower(), start_tag(supplier_id, secret)):
        return None
    shop_id = config.load().demo_shop_id
    path = paths.supplier(shop_id, supplier_id)
    store = get_store()
    doc = await store.get(path)
    if doc is None:
        return None
    await store.set(path, {**doc, "telegramChatId": chat_id, "telegramLinkedAt": now_iso()})
    await _repo.index_contact("telegram", chat_id, shop_id, supplier_id)
    shop = await store.get(paths.shop(shop_id)) or {}
    try:
        await telegram.send_message(chat_id, WELCOME.format(shop=shop.get("name") or shop_id))
    except telegram.TelegramError as exc:
        log.warning("telegram welcome to %s (%s) failed: %s", supplier_id, chat_id, exc)
    return supplier_id


@router.post("/webhooks/telegram")
async def telegram_in(request: Request):
    if not _telegram_secret_ok(request.headers.get("x-telegram-bot-api-secret-token")):
        return Response(status_code=403)
    try:
        update = json.loads(await request.body() or b"{}")
    except ValueError:
        return _ignored()
    msg = update.get("message") if isinstance(update, dict) else None
    if not isinstance(msg, dict):
        return _ignored()
    chat = msg.get("chat")
    chat_id = str(chat.get("id", "")) if isinstance(chat, dict) else ""
    text = msg.get("text")
    if not chat_id or not isinstance(text, str) or not text.strip():
        return _ignored()
    text = text.strip()
    if text.lower().startswith("/start"):
        m = START_RE.match(text)
        if not m:
            return _ignored()
        try:
            linked = await _link_supplier(chat_id, m.group(1).lower(), m.group(2))
        except Exception:
            log.exception("telegram /start failed chat=%s", chat_id)
            linked = None
        return {"ok": True, "handled": 1, "linked": linked} if linked else _ignored()
    reply_to = msg.get("reply_to_message")
    reply_id = reply_to.get("message_id") if isinstance(reply_to, dict) else None
    key = f"{chat_id}:{reply_id}" if reply_id is not None else None
    try:
        handled = int(await _apply_reply("telegram", key, chat_id, text))
    except Exception:
        log.exception("telegram vendor reply failed chat=%s", chat_id)
        handled = 0
    return {"ok": True, "handled": handled}


# ---------------------------------------------------------------- shared
async def _apply_reply(channel: str, msg_key: str | None, contact: str, text: str) -> bool:
    """Match the reply to an order, store order.vendorReply and speak it. False when no order matches."""
    order, shop_id = None, None
    ref = await _repo.lookup_message(channel, msg_key) if msg_key else None
    if ref:
        shop_id = ref["shopId"]
        order = await _repo.get_order(shop_id, ref["orderId"])
    if order is None:
        who = await _repo.lookup_contact(channel, contact) if contact else None
        if not who:
            return False
        shop_id = who["shopId"]
        order = await _repo.latest_order(shop_id, OPEN_STATES, supplier_id=who["supplierId"])
        if order is None:
            return False
    now = now_iso()
    await _repo.save_order(shop_id, {**order, "vendorReply": {"text": text, "at": now, "channel": channel},
                                     "updatedAt": now})
    supplier = order.get("supplierName") or order.get("supplierId", "")
    await _notify(shop_id, t("k2.order.vendor_replied", order.get("lang") or "en", supplier=supplier, text=text))
    return True
