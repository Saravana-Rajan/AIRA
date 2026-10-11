#!/usr/bin/env python3
"""Point the AIRA Telegram bot at aira-live and print the vendor onboarding links (stdlib only).

Usage (from the repo root, after the bot token is attached to the service; docs/ops/telegram-setup.md):
    TELEGRAM_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... python3 scripts/telegram_setup.py https://<aira-live host>
    (or set AIRA_LIVE_URL instead of the argument)

What it does:
  1. setWebhook(url=<aira-live>/webhooks/telegram, secret_token=TELEGRAM_WEBHOOK_SECRET, allowed_updates=["message"])
  2. getMe, to learn the bot's username
  3. prints https://t.me/<username>?start=<supplier_id>.<tag> for every supplier whose catalog channel is
     "telegram", where <tag> = HMAC-SHA256(TELEGRAM_WEBHOOK_SECRET, supplier_id)[:20] (the same tag
     aira_live.messaging.webhooks checks, so the secret here must be the one attached to aira-live)
Send each vendor their link, privately: the link is the credential. When they press Start, aira-live stores
their chat id on the supplier doc and replies "AIRA: you will receive Sunrise Dairy orders here." A /start
without the right tag is ignored, so nobody can link a vendor from the public supplier id alone. A vendor who
changes phones opens the same link again on the new phone; the old chat is unlinked.

The token and the secret are read from the environment and never printed, not even inside an error message.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "content" / "catalog" / "murugan_dairy.json"
API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT_S = 15.0
TAG_HEX = 20  # keep equal to aira_live.messaging.webhooks.TAG_HEX

Http = Callable[[str, dict | None], dict]


class SetupError(RuntimeError):
    """A Bot API or input error, worded without the token."""


def _http(url: str, data: dict | None = None) -> dict:
    """POST (or GET when data is None) a Bot API method; returns the decoded JSON body."""
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:                      # the Bot API answers errors with a JSON body
        try:
            return json.loads(e.read().decode())
        except ValueError:
            raise SetupError(f"HTTP {e.code} from the Telegram Bot API") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SetupError(f"could not reach the Telegram Bot API: {type(e).__name__}") from None


def _call(token: str, method: str, data: dict | None, http: Http) -> dict:
    res = http(API.format(token=token, method=method), data)
    if not isinstance(res, dict) or not res.get("ok"):
        description = res.get("description") if isinstance(res, dict) else None
        raise SetupError(f"{method} failed: {description or 'no description'}")
    return res


def webhook_url(live_url: str) -> str:
    live_url = live_url.strip().rstrip("/")
    if not live_url.startswith("https://"):
        raise SetupError("the aira-live URL must start with https:// (Telegram only calls HTTPS webhooks)")
    return f"{live_url}/webhooks/telegram"


def set_webhook(token: str, live_url: str, secret: str, http: Http = _http) -> str:
    """Register <live_url>/webhooks/telegram with the secret header; returns the webhook URL."""
    url = webhook_url(live_url)
    _call(token, "setWebhook", {"url": url, "secret_token": secret, "allowed_updates": ["message"]}, http)
    return url


def get_me(token: str, http: Http = _http) -> str:
    """The bot's username (the part after t.me/)."""
    res = _call(token, "getMe", None, http)
    username = (res.get("result") or {}).get("username")
    if not username:
        raise SetupError("getMe returned no username")
    return str(username)


def start_param(supplier_id: str, secret: str) -> str:
    """The ``?start=`` value of a vendor's link: ``<supplier_id>.<tag>``, tag = HMAC-SHA256(secret, id)[:20].

    ``secret`` is TELEGRAM_WEBHOOK_SECRET as attached to aira-live; aira_live.messaging.webhooks recomputes the
    tag and ignores any /start that does not carry it."""
    if not secret:
        raise SetupError("TELEGRAM_WEBHOOK_SECRET is empty: no vendor link can be made")
    tag = hmac.new(secret.encode(), supplier_id.encode(), hashlib.sha256).hexdigest()[:TAG_HEX]
    return f"{supplier_id}.{tag}"


def deep_links(username: str, catalog: dict, secret: str) -> list[tuple[str, str, str]]:
    """(supplier_id, name, https://t.me/<username>?start=<id>.<tag>) per telegram supplier, in catalog order."""
    return [(s["id"], s.get("name", s["id"]), f"https://t.me/{username}?start={start_param(s['id'], secret)}")
            for s in catalog.get("suppliers", []) if s.get("channel") == "telegram"]


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, http: Http = _http,
         catalog_path: Path = CATALOG) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    live_url = argv[0] if argv else env.get("AIRA_LIVE_URL", "")
    if not live_url:
        print("error: pass the aira-live URL as the first argument or set AIRA_LIVE_URL", file=sys.stderr)
        return 2
    missing = [name for name in ("TELEGRAM_TOKEN", "TELEGRAM_WEBHOOK_SECRET") if not env.get(name)]
    if missing:
        print(f"error: set {' and '.join(missing)} in the environment (never on the command line)", file=sys.stderr)
        return 2
    token, secret = env["TELEGRAM_TOKEN"], env["TELEGRAM_WEBHOOK_SECRET"]
    try:
        url = set_webhook(token, live_url, secret, http=http)
        username = get_me(token, http=http)
    except SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    print(f"Webhook set: {url} (allowed_updates: message, secret header on)")
    print(f"Bot: @{username}")
    print("Send each vendor their own link, privately (the link is the credential). They open it and press Start;")
    print("AIRA then messages them orders there. A new phone opens the same link again; the old chat is unlinked:")
    for sid, name, link in deep_links(username, catalog, secret):
        print(f"  {name} ({sid}): {link}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
