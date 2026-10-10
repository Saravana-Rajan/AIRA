"""Optional Telegram channel (free). A bot can message a vendor only after the vendor has started a chat with it.

Env (never in git): TELEGRAM_TOKEN. API: https://core.telegram.org/bots/api#sendmessage
"""
import httpx

from aira_live import config

TIMEOUT_S = 8.0


class TelegramError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.load().telegram_token)


async def send_message(chat_id: str, text: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Send a plain-text message. Returns the Telegram message id as a string; raises TelegramError on any failure."""
    token = config.load().telegram_token
    if not token:
        raise TelegramError("Telegram is not configured")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text})
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # never put the exception text in the error: httpx messages include the URL, and the URL holds the token
        raise TelegramError(f"request failed: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or not data.get("ok"):
        description = data.get("description") if isinstance(data, dict) else None
        raise TelegramError(str(description or f"HTTP {resp.status_code}"))
    try:
        return str(data["result"]["message_id"])
    except (KeyError, TypeError) as exc:
        raise TelegramError("no message id in response") from exc
