"""Who is on the phone: the ``hello`` check (build overrides §1: no Firebase Auth).

There is one user and one shop. A ``hello`` is accepted only in mock mode or when ``AIRA_ALLOW_NO_TOKEN=1`` (set
on the deployed service), and it always lands in the configured demo shop (env ``AIRA_DEMO_SHOP``, default
``murugan_dairy``) as its owner. The ``shopId`` the client sends is ignored, so no phone can reach another
shop's data by naming it.

Without Firebase Auth there is nothing to verify an ``idToken`` against. The deployed service instead holds a
shared app key (``AIRA_APP_KEY``, Secret Manager ``aira-app-key``) that the hosting build bakes into the app as
``VITE_AIRA_APP_KEY`` and the app sends as ``hello.idToken``. It is a speed bump on the public URL, not a login:
the owner taps Start as before. When no key is configured (local runs) a token is ignored, as before.
"""
import hmac
import logging
from dataclasses import dataclass
from typing import Literal

from aira_live import config

log = logging.getLogger(__name__)

DEMO_SHOP = config.load().demo_shop_id  # env AIRA_DEMO_SHOP, default "murugan_dairy"


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class AuthResult:
    uid: str
    shop_id: str
    role: Literal["owner", "family", "demo"]


async def verify_hello(id_token: str | None, uid: str, shop_id: str, mock: bool) -> AuthResult:
    """Accept a ``hello`` into the demo shop, or raise ``AuthError`` (the socket then gets ``error/permission``)."""
    settings = config.load()
    if not (mock or settings.allow_no_token):
        raise AuthError("hello refused: tokenless mode is off and there is no Firebase Auth to check a token")
    if settings.app_key and not mock:
        if not id_token or not hmac.compare_digest(id_token.encode(), settings.app_key.encode()):
            raise AuthError("hello refused: idToken does not match AIRA_APP_KEY")
    if shop_id != settings.demo_shop_id:
        log.info("hello for shop %r served as demo shop %r", shop_id, settings.demo_shop_id)
    return AuthResult(uid=uid, shop_id=settings.demo_shop_id, role="owner")
