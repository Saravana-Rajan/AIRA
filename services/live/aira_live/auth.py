"""Who is on the phone: the ``hello`` check (build overrides §1: no Firebase Auth).

There is one user and one shop. A ``hello`` is accepted only in mock mode or when ``AIRA_ALLOW_NO_TOKEN=1`` (set
on the deployed service), and it always lands in the configured demo shop (env ``AIRA_DEMO_SHOP``, default
``murugan_dairy``) as its owner. The ``shopId`` the client sends is ignored, so no phone can reach another
shop's data by naming it.

Without Firebase Auth there is nothing to verify an ``idToken`` against, so a token never grants more than
tokenless mode: it is ignored when tokenless mode is on and refused when it is off.
"""
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
    if shop_id != settings.demo_shop_id:
        log.info("hello for shop %r served as demo shop %r", shop_id, settings.demo_shop_id)
    return AuthResult(uid=uid, shop_id=settings.demo_shop_id, role="owner")
