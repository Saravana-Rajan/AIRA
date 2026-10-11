"""Who is on the phone: the ``hello`` check (build overrides §1: no Firebase Auth).

There is one user and one shop. A ``hello`` is accepted only in mock mode or when ``AIRA_ALLOW_NO_TOKEN=1`` (set
on the deployed service), and it always lands in the configured demo shop (env ``AIRA_DEMO_SHOP``, default
``sunrise_dairy``) as its owner. The ``shopId`` the client sends is ignored, so no phone can reach another
shop's data by naming it.

Without Firebase Auth there is no user identity to verify. What keeps a stranger with the URL out is the shared
demo key: when ``AIRA_APP_KEY`` is set (Secret Manager ``aira-app-key``), ``hello.idToken`` must equal it. The
app builds the same value in as ``VITE_AIRA_APP_KEY``. It is one key for the one demo phone, not user auth
(shared/docs/ops/security.md). ``main.py`` adds the browser Origin allowlist on ``/ws`` and the per-address cap.
"""
import hmac
import logging
from dataclasses import dataclass
from typing import Literal

from aira_live import config

log = logging.getLogger(__name__)

DEMO_SHOP = config.load().demo_shop_id  # env AIRA_DEMO_SHOP, default "sunrise_dairy"


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class AuthResult:
    uid: str
    shop_id: str
    role: Literal["owner", "family", "demo"]


def _app_key_ok(id_token: str | None, app_key: str) -> bool:
    """Constant-time compare of the hello token with the shared demo key."""
    return bool(id_token) and hmac.compare_digest(id_token.encode(), app_key.encode())


async def verify_hello(id_token: str | None, uid: str, shop_id: str, mock: bool) -> AuthResult:
    """Accept a ``hello`` into the demo shop, or raise ``AuthError`` (the socket then gets ``error/permission``)."""
    settings = config.load()
    if not (mock or settings.allow_no_token):
        raise AuthError("hello refused: tokenless mode is off and there is no Firebase Auth to check a token")
    if not mock and settings.app_key and not _app_key_ok(id_token, settings.app_key):
        raise AuthError("hello refused: idToken does not match AIRA_APP_KEY")
    if shop_id != settings.demo_shop_id:
        log.info("hello for shop %r served as demo shop %r", shop_id, settings.demo_shop_id)
    return AuthResult(uid=uid, shop_id=settings.demo_shop_id, role="owner")
