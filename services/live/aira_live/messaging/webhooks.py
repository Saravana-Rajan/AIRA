"""Vendor replies -> order.vendorReply, spoken to the owner via sessions.notify.

Routes (mounted by main.py through OPTIONAL_ROUTERS):
  GET  /webhooks/whatsapp   Meta verification handshake (hub.mode / hub.verify_token / hub.challenge)
  POST /webhooks/whatsapp   WhatsApp Cloud API message notifications
  POST /webhooks/telegram   Telegram Bot API updates

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
from collections.abc import Iterator

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from aira_live.business.clock import now_iso
from aira_live.i18n import t
from aira_live.messaging.clicktochat import normalize_in_phone
from aira_live.tools import _repo

router = APIRouter()
log = logging.getLogger(__name__)
OPEN_STATES = ["ORDER_PLACED", "DELIVERY_PENDING"]


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
    """X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(app secret, raw body). Checked only when the secret is set."""
    secret = os.getenv("WHATSAPP_APP_SECRET")
    if not secret:
        return True
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
@router.post("/webhooks/telegram")
async def telegram_in(request: Request):
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
    if secret and not hmac.compare_digest(request.headers.get("x-telegram-bot-api-secret-token") or "", secret):
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
    reply_to = msg.get("reply_to_message")
    reply_id = reply_to.get("message_id") if isinstance(reply_to, dict) else None
    key = f"{chat_id}:{reply_id}" if reply_id is not None else None
    try:
        handled = int(await _apply_reply("telegram", key, chat_id, text.strip()))
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
