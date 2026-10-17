"""shared/scripts/telegram_setup.py: points the AIRA Telegram bot at aira-live and prints the vendor deep links.

Everything here runs against a fake HTTP function. No test reaches Telegram, and the bot token never appears
in any output.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

import telegram_setup as ts

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "shared" / "catalog" / "sunrise_dairy.json"
TOKEN = "123456:ABC-secret-token"
SECRET = "whsec-example"
LIVE = "https://aira-live.example.run.app"


def tag(supplier_id: str, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), supplier_id.encode(), hashlib.sha256).hexdigest()[:20]


class FakeHttp:
    """Records every call; answers getMe and setWebhook like the Bot API."""

    def __init__(self, username: str = "AiraShopBot", webhook_ok: bool = True):
        self.calls: list[tuple[str, dict | None]] = []
        self.username = username
        self.webhook_ok = webhook_ok

    def __call__(self, url: str, data: dict | None = None) -> dict:
        self.calls.append((url, data))
        if url.endswith("/setWebhook"):
            return {"ok": self.webhook_ok, "result": self.webhook_ok,
                    "description": "Webhook was set" if self.webhook_ok else "bad webhook: HTTPS url must be provided"}
        if url.endswith("/getMe"):
            return {"ok": True, "result": {"id": 1, "is_bot": True, "username": self.username}}
        raise AssertionError(f"unexpected call {url}")


def env(**extra) -> dict[str, str]:
    return {"TELEGRAM_TOKEN": TOKEN, "TELEGRAM_WEBHOOK_SECRET": SECRET, **extra}


def test_set_webhook_request_shape():
    http = FakeHttp()
    ts.set_webhook(TOKEN, LIVE, SECRET, http=http)
    url, data = http.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/setWebhook"
    assert data == {"url": f"{LIVE}/webhooks/telegram", "secret_token": SECRET, "allowed_updates": ["message"]}


def test_set_webhook_strips_a_trailing_slash_and_rejects_http():
    http = FakeHttp()
    ts.set_webhook(TOKEN, LIVE + "/", SECRET, http=http)
    assert http.calls[0][1]["url"] == f"{LIVE}/webhooks/telegram"
    with pytest.raises(ts.SetupError, match="https://"):
        ts.set_webhook(TOKEN, "http://aira-live.example.run.app", SECRET, http=FakeHttp())


def test_set_webhook_failure_never_leaks_the_token():
    with pytest.raises(ts.SetupError) as info:
        ts.set_webhook(TOKEN, LIVE, SECRET, http=FakeHttp(webhook_ok=False))
    assert "bad webhook" in str(info.value)
    assert TOKEN not in str(info.value)


def test_get_me_returns_the_bot_username():
    assert ts.get_me(TOKEN, http=FakeHttp(username="SunriseDairyBot")) == "SunriseDairyBot"


def test_start_param_is_the_supplier_id_with_its_hmac_tag():
    """The same tag aira_live.messaging.webhooks checks: HMAC-SHA256(TELEGRAM_WEBHOOK_SECRET, supplier_id)[:20]."""
    assert ts.start_param("aavin_vendor", SECRET) == f"aavin_vendor.{tag('aavin_vendor')}"
    assert ts.start_param("aavin_vendor", "other") != ts.start_param("aavin_vendor", SECRET)
    assert ts.start_param("aavin_vendor", SECRET) != ts.start_param("sakthi_vendor", SECRET)
    with pytest.raises(ts.SetupError):
        ts.start_param("aavin_vendor", "")


def test_start_param_matches_the_webhook_check():
    from aira_live.messaging import webhooks

    assert ts.start_param("aavin_vendor", SECRET) == webhooks.tagged_start_param("aavin_vendor", SECRET)


def test_deep_links_cover_every_telegram_supplier_only():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    links = ts.deep_links("AiraShopBot", catalog, SECRET)
    assert links == [
        ("aavin_vendor", "Aavin vendor", f"https://t.me/AiraShopBot?start=aavin_vendor.{tag('aavin_vendor')}"),
        ("sakthi_vendor", "Sakthi curd vendor", f"https://t.me/AiraShopBot?start=sakthi_vendor.{tag('sakthi_vendor')}"),
    ]
    assert all(sid != "arun_distributor" for sid, _, _ in links)  # channel "call" gets no bot link


def test_main_sets_the_webhook_and_prints_links_without_the_token(capsys):
    http = FakeHttp()
    rc = ts.main([LIVE], env=env(), http=http, catalog_path=CATALOG)
    out = capsys.readouterr().out
    assert rc == 0
    assert [u.rsplit("/", 1)[1] for u, _ in http.calls] == ["setWebhook", "getMe"]
    assert f"{LIVE}/webhooks/telegram" in out
    assert f"https://t.me/AiraShopBot?start=aavin_vendor.{tag('aavin_vendor')}\n" in out
    assert f"https://t.me/AiraShopBot?start=sakthi_vendor.{tag('sakthi_vendor')}\n" in out
    assert "?start=aavin_vendor\n" not in out and "?start=sakthi_vendor\n" not in out  # never an untagged link
    assert "AIRA_TELEGRAM_LINK_CODE" not in out
    assert "arun_distributor" not in out
    assert TOKEN not in out and "123456" not in out and SECRET not in out


def test_main_reads_the_live_url_from_the_environment(capsys):
    http = FakeHttp()
    assert ts.main([], env=env(AIRA_LIVE_URL=LIVE), http=http, catalog_path=CATALOG) == 0
    assert http.calls[0][1]["url"] == f"{LIVE}/webhooks/telegram"


@pytest.mark.parametrize("missing", ["TELEGRAM_TOKEN", "TELEGRAM_WEBHOOK_SECRET"])
def test_main_fails_cleanly_without_a_secret(missing, capsys):
    e = env()
    del e[missing]
    http = FakeHttp()
    assert ts.main([LIVE], env=e, http=http, catalog_path=CATALOG) == 2
    assert http.calls == []
    assert missing in capsys.readouterr().err


def test_main_fails_cleanly_without_a_live_url(capsys):
    http = FakeHttp()
    assert ts.main([], env=env(), http=http, catalog_path=CATALOG) == 2
    assert http.calls == []
    assert "AIRA_LIVE_URL" in capsys.readouterr().err


def test_main_reports_a_webhook_error_without_the_token(capsys):
    assert ts.main([LIVE], env=env(), http=FakeHttp(webhook_ok=False), catalog_path=CATALOG) == 1
    err = capsys.readouterr().err
    assert "bad webhook" in err and TOKEN not in err
