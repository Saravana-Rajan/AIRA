"""Supplier order delivery: Telegram (when the vendor's chat is known) → WhatsApp Cloud API (runtime-flag gated)
→ click-to-chat / call fallback.

`send_order` never raises for a channel failure: it falls back to a link the owner opens and sends himself.
It only sends; the order's state is the caller's job (tools/orders.py commits ORDER_PLACED or keeps DRAFT).
"""
import logging

from aira_live.business import paths
from aira_live.business.store import get_store
from aira_live.messaging import clicktochat, telegram, whatsapp
from aira_live.tools import _repo

log = logging.getLogger(__name__)


def _whatsapp_enabled() -> bool:
    from aira_live import remote_config  # lazy: the flag cache needs no import at module load

    return remote_config.flag("whatsapp_enabled", True)


async def send_order(shop_id: str, supplier: dict, order: dict) -> dict:
    """Send `order` ({id, items: [{name, qty, unit}]}) to `supplier` ({id, phone, channel, telegramChatId?}).

    Returns {"channel": "whatsapp" | "telegram", "messageId": str} when a message went out, otherwise
    {"channel": "whatsapp_link" | "call", "fallbackUrl": str} for the app to open (OPEN_URL hand-off).
    A Telegram supplier that could not be messaged also gets "reason": "telegram_not_linked" (no telegramChatId:
    the vendor has not opened the bot's /start link), "telegram_not_configured" (no TELEGRAM_TOKEN on the
    service) or "telegram_failed", so the caller can say why the owner has to send it himself.
    """
    shop = await get_store().get(paths.shop(shop_id)) or {}
    shop_name = shop.get("name") or shop_id
    phone = supplier.get("phone") or ""
    channel = supplier.get("channel") or "whatsapp"
    items = order["items"]

    if channel == "call":
        return {"channel": "call", "fallbackUrl": clicktochat.tel_link(phone)}

    reason: str | None = None
    chat = str(supplier.get("telegramChatId") or "")
    if channel == "telegram":
        if not chat:
            reason = "telegram_not_linked"
        elif not telegram.configured():
            reason = "telegram_not_configured"
        else:
            try:
                mid = await telegram.send_message(chat, clicktochat.order_text(shop_name, items))
            except telegram.TelegramError as exc:
                log.warning("telegram order %s to %s failed: %s", order["id"], supplier.get("id"), exc)
                reason = "telegram_failed"
            else:
                await _repo.index_message("telegram", f"{chat}:{mid}", shop_id, order["id"])
                await _repo.index_contact("telegram", chat, shop_id, supplier["id"])
                return {"channel": "telegram", "messageId": mid}
        log.warning("telegram order %s to %s not sent (%s): falling back to click-to-chat",
                    order["id"], supplier.get("id"), reason)

    to = clicktochat.normalize_in_phone(phone)
    if to and _whatsapp_enabled() and whatsapp.configured():
        try:
            mid = await whatsapp.send_template(phone, shop_name, clicktochat.items_text(items))
        except whatsapp.WhatsAppError as exc:
            log.warning("whatsapp order %s to %s failed: %s", order["id"], supplier.get("id"), exc)
        else:
            await _repo.index_message("whatsapp", mid, shop_id, order["id"])
            await _repo.index_contact("whatsapp", to, shop_id, supplier["id"])
            return {"channel": "whatsapp", "messageId": mid}

    text = clicktochat.order_text(shop_name, items)
    out = {"channel": "whatsapp_link", "fallbackUrl": clicktochat.wa_link(phone, text)}
    if reason:
        out["reason"] = reason
    return out
