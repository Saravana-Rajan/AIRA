"""WhatsApp Business Cloud API (Meta). Hackathon setup: Meta's free test number, vendors registered as test recipients.

Template `order_request` (utility, en): body "New order from {{1}}: {{2}}. Please confirm delivery time."
Set WHATSAPP_TEMPLATE=hello_world for the very first smoke test (no parameters).

Env (never in git): WHATSAPP_TOKEN and WHATSAPP_PHONE_ID (both required); optional WHATSAPP_TEMPLATE (default
`order_request`) and WHATSAPP_GRAPH_VERSION (default `v21.0`; set a current Graph API version for live use).
Request shape: https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-message-templates
"""
import os

import httpx

from aira_live import config
from aira_live.messaging.clicktochat import normalize_in_phone

DEFAULT_TEMPLATE = "order_request"
DEFAULT_GRAPH_VERSION = "v21.0"
TIMEOUT_S = 8.0


class WhatsAppError(RuntimeError):
    pass


def configured() -> bool:
    settings = config.load()
    return bool(settings.whatsapp_token and settings.whatsapp_phone_id)


def _url(phone_id: str) -> str:
    version = os.getenv("WHATSAPP_GRAPH_VERSION") or DEFAULT_GRAPH_VERSION
    return f"https://graph.facebook.com/{version}/{phone_id}/messages"


def template_body(to_phone: str, shop_name: str, items_text: str) -> dict:
    name = os.getenv("WHATSAPP_TEMPLATE") or DEFAULT_TEMPLATE
    if name == "hello_world":
        template = {"name": "hello_world", "language": {"code": "en_US"}}
    else:
        template = {"name": name, "language": {"code": "en"},
                    "components": [{"type": "body", "parameters": [{"type": "text", "text": shop_name},
                                                                   {"type": "text", "text": items_text}]}]}
    return {"messaging_product": "whatsapp", "to": normalize_in_phone(to_phone), "type": "template",
            "template": template}


async def send_template(to_phone: str, shop_name: str, items_text: str, *,
                        transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Send the order template. Returns the WhatsApp message id (wamid…); raises WhatsAppError on any failure."""
    settings = config.load()
    if not (settings.whatsapp_token and settings.whatsapp_phone_id):
        raise WhatsAppError("WhatsApp is not configured")
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    body = template_body(to_phone, shop_name, items_text)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport) as client:
            resp = await client.post(_url(settings.whatsapp_phone_id), json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise WhatsAppError(f"request failed: {exc}") from exc
    if resp.status_code >= 300:
        raise WhatsAppError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        return str(resp.json()["messages"][0]["id"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise WhatsAppError("no message id in response") from exc
