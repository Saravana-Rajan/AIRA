"""Live tools over the last-seen memory: memory_where, memory_forget, memory_remembering (spec §5).

Read-only for the shop: these never touch stock, money or state, so no confirmation is needed. The answer is
always "I last saw", never "it is": a sighting is not proof the item is still there.
"""
from __future__ import annotations

from aira_live.i18n import t
from aira_live.memory import items
from aira_live.session import get_session


async def _item(spoken: str, custom: list[str]) -> str:
    return items.canonical_item(spoken, custom) or items.normalize_name(spoken)


async def memory_where(item: str) -> dict:
    """Where the camera last saw one of the owner's personal things (keys, wallet, phone, glasses, remote, bag,
    medicine, umbrella, charger, or an item he asked AIRA to remember).

    Call it for "where are my keys", "did you see my phone", "where did I keep my glasses". The reply says
    where it was LAST SEEN and when; it is not proof the item is still there. For "take me there", call
    camera_ask for the landmark, then product_find with the item.
    """
    s = get_session()
    cfg = await items.settings(s.shop_id)
    name = await _item(item, cfg["items"])
    spoken = items.speak_item(name, s.lang)
    now = items._utcnow()
    sighting = await items.last_seen(s.shop_id, name, now=now)
    if sighting is None:
        return {"say": t("mem.none", s.lang, item=spoken), "found": False, "item": name}
    say = items.describe(spoken, sighting, s.lang, now)
    if not cfg["remembering"]:
        say = f"{say} {t('mem.off', s.lang)}"
    return {"say": say, "found": True, "item": name, "place": items.speak_place(sighting, s.lang),
            "at": sighting.at.isoformat(), "old": items.is_old(sighting, now), "kind": sighting.kind}


async def memory_forget(item: str | None = None) -> dict:
    """Forget where things were seen: everything ("forget everything") or one item ("forget my keys")."""
    s = get_session()
    cfg = await items.settings(s.shop_id)
    name = await _item(item, cfg["items"]) if item else None
    n = await items.forget(s.shop_id, name)
    if name:
        return {"say": t("mem.forgot_item", s.lang, item=items.speak_item(name, s.lang)), "forgotten": n, "item": name}
    return {"say": t("mem.forgot_all", s.lang), "forgotten": n, "item": None}


async def memory_remembering(on: bool, item: str | None = None) -> dict:
    """Turn the last-seen memory on or off: "stop remembering" (on=false), "start remembering" (on=true).

    With item set ("remember my walking stick"), AIRA also watches for that item from now on.
    """
    s = get_session()
    name = None
    if on and item:
        name = await items.watch_item(s.shop_id, item)
    await items.set_remembering(s.shop_id, bool(on))
    if name:
        say = t("mem.watch_item", s.lang, item=items.speak_item(name, s.lang))
    else:
        say = t("mem.remember_on" if on else "mem.remember_off", s.lang)
    return {"say": say, "remembering": bool(on), "item": name}
