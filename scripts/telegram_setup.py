#!/usr/bin/env python3
"""Point the AIRA Telegram bot at aira-live and print the vendor onboarding links (stdlib only).

Usage (from the repo root, after the bot token is attached to the service; docs/ops/telegram-setup.md):
    TELEGRAM_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... python3 scripts/telegram_setup.py https://<aira-live host>
    (or set AIRA_LIVE_URL instead of the argument)

What it does:
  1. setWebhook(url=<aira-live>/webhooks/telegram, secret_token=TELEGRAM_WEBHOOK_SECRET, allowed_updates=["message"])
  2. getMe, to learn the bot's username
  3. prints https://t.me/<username>?start=<supplier_id> for every supplier whose catalog channel is "telegram"
Send each vendor their link. When they press Start, aira-live stores their chat id on the supplier doc
(messaging/webhooks.py) and replies "AIRA: you will receive Murugan Dairy orders here." Linking is first-come:
the supplier ids are public, so once a chat is linked another chat cannot take the vendor over. To move a vendor
to a new phone, set AIRA_TELEGRAM_LINK_CODE on aira-live and send the re-link form ?start=<supplier_id>-<code>
(printed here when AIRA_TELEGRAM_LINK_CODE is in the environment).

The token is read from the environment and never printed, not even inside an error message.
"""
from __future__ import annotations

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


def start_param(supplier_id: str, code: str | None = None) -> str:
    """The ``?start=`` value of a vendor's link: ``<supplier_id>`` for the first link, ``<supplier_id>-<code>`` to
    move a linked vendor to a new phone (aira_live.messaging.webhooks checks the code against
    AIRA_TELEGRAM_LINK_CODE)."""
    return f"{supplier_id}-{code}" if code else supplier_id


def deep_links(username: str, catalog: dict, code: str | None = None) -> list[tuple[str, str, str]]:
    """(supplier_id, name, https://t.me/<username>?start=<param>) per telegram supplier, in catalog order."""
    return [(s["id"], s.get("name", s["id"]), f"https://t.me/{username}?start={start_param(s['id'], code)}")
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
    print("Send each vendor their link. They open it and press Start; AIRA then messages them orders there:")
    for sid, name, link in deep_links(username, catalog):
        print(f"  {name} ({sid}): {link}")
    code = env.get("AIRA_TELEGRAM_LINK_CODE")
    if code:
        print("Re-link links (a vendor moving to a new phone; AIRA_TELEGRAM_LINK_CODE must be set on aira-live):")
        for sid, name, link in deep_links(username, catalog, code):
            print(f"  {name} ({sid}): {link}")
    else:
        print("A linked vendor stays linked (first come). To move one to a new phone, set AIRA_TELEGRAM_LINK_CODE "
              "on aira-live and in this shell, then run this again for the re-link links.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
