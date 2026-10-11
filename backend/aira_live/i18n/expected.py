"""Which languages a shop usually speaks: the guide for ``detect.judge_lang``.

Source: the shop doc field ``languages`` (BCP-47 codes), overridden by ``config/runtime.languages``
(``aira_live.remote_config``), defaulting to Tamil and English for the demo shop. The list guides detection:
it never limits what AIRA answers in. A language outside it needs three consecutive full sentences before the
session follows it; until then such a transcript is flagged "unclear" and the owner is asked to repeat.
"""
from __future__ import annotations

import logging
import os

from aira_live import remote_config
from aira_live.i18n.detect import _normalise

log = logging.getLogger(__name__)

DEFAULT_LANGUAGES: tuple[str, ...] = ("ta", "en")
FIELD = "languages"


def expected_languages(shop: dict | None) -> list[str]:
    """The expected languages for a shop doc: the runtime override, else the doc's list, else the default.
    Codes are lower-cased base tags ("en-IN" -> "en"); junk entries are dropped. Never empty, never raises."""
    override = remote_config.languages()
    codes = _normalise(override) if override else []
    if not codes and isinstance(shop, dict) and isinstance(shop.get(FIELD), list):
        codes = _normalise(shop[FIELD])
    return codes or list(DEFAULT_LANGUAGES)


async def shop_languages(shop_id: str) -> list[str]:
    """``expected_languages`` for ``shops/{shop_id}`` read from Firestore. The default when the read fails."""
    from aira_live import config
    from aira_live.db import db  # lazy: tests patch aira_live.db.db

    try:
        if config.load().mock and not os.getenv("FIRESTORE_EMULATOR_HOST"):
            return expected_languages({})  # mock mode without an emulator has no shop doc to read
        snap = await db().collection("shops").document(shop_id).get()
        shop = (snap.to_dict() or {}) if snap.exists else {}
    except Exception as exc:
        log.warning("shop languages for %s unavailable, using %s: %s", shop_id, DEFAULT_LANGUAGES, exc)
        shop = {}
    return expected_languages(shop)
