"""Live tools over the last-seen memory: memory_where, memory_forget, memory_remembering (spec §5).

Read-only for the shop: these never touch stock, money or state, so no confirmation is needed. The answer is
always "I last saw", never "it is": a sighting is not proof the item is still there. Results are FACTS
(item, place, side, when, old) plus an English ``say_en`` hint; Live phrases them in the owner's language.
"""
from __future__ import annotations

from aira_live.memory import items
from aira_live.session import get_session

OFF_HINT = "I am not remembering new things right now. Say start remembering to turn it on."


async def _item(spoken: str, custom: list[str]) -> str:
    return items.canonical_item(spoken, custom) or items.normalize_name(spoken)


async def memory_where(item: str) -> dict:
    """Where the camera last saw one of the owner's personal things (keys, wallet, phone, glasses, remote, bag,
    medicine, umbrella, charger, or an item he asked AIRA to remember).

    Call it for "where are my keys", "did you see my phone", "where did I keep my glasses". The reply says
    where it was LAST SEEN ('place', 'side', 'when') and never proves the item is still there: say "I last
    saw", not "it is"; when 'old' is true add that it may have moved. For "take me there", call camera_ask for
    the landmark, then product_find with the item.
    """
    s = get_session()
    cfg = await items.settings(s.shop_id)
    name = await _item(item, cfg["items"])
    now = items._utcnow()
    sighting = await items.last_seen(s.shop_id, name, now=now)
    if sighting is None:
        return {"found": False, "item": name, "remembering": cfg["remembering"],
                "say_en": f"I have not seen your {name} yet. Show me around and I will remember."}
    say_en = items.describe(name, sighting, "en", now)
    if not cfg["remembering"]:
        say_en = f"{say_en} {OFF_HINT}"
    return {"found": True, "item": name, **items.place_facts(sighting), "at": sighting.at.isoformat(),
            "when": items.when_phrase(sighting.at, now), "last_seen_ago_s": int((now - sighting.at).total_seconds()),
            "old": items.is_old(sighting, now), "kind": sighting.kind, "remembering": cfg["remembering"],
            "say_en": say_en}


async def memory_forget(item: str | None = None) -> dict:
    """Forget where things were seen: everything ("forget everything") or one item ("forget my keys")."""
    s = get_session()
    cfg = await items.settings(s.shop_id)
    name = await _item(item, cfg["items"]) if item else None
    n = await items.forget(s.shop_id, name)
    if name:
        return {"ok": True, "forgotten": n, "item": name, "say_en": f"Done. I forgot where your {name} was."}
    return {"ok": True, "forgotten": n, "item": None, "say_en": "Done. I forgot everything I saw."}


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
        say_en = f"Okay. I will remember where I see your {name}."
    elif on:
        say_en = "Okay. I will remember where I see your things."
    else:
        say_en = "Okay. I stopped remembering. Nothing new will be saved."
    return {"ok": True, "remembering": bool(on), "item": name, "say_en": say_en}
