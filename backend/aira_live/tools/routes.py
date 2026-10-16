"""Walk to a fixed point of the shop map (e.g. the light switch board) with the route follower.

The bridge between low light (tools.light: "where is the light switch") and step-by-step routes (shop_map,
vision.route, tools.walk). Steps need no light, so this works in the dark. Only with AIRA_WALK=1, AIRA_ROUTES=1
and a usable shop map that has the point and a route to its zone; otherwise None and the caller falls back to the
camera alone.
"""
from __future__ import annotations

import logging

from aira_live import config, shop_map
from aira_live.diag import diag
from aira_live.session import Session

log = logging.getLogger(__name__)


def find_point(m: shop_map.ShopMap, place: str) -> tuple[shop_map.Zone, dict] | None:
    """The zone and fixed point whose id or name matches ``place`` ("switch_board" matches "light_switch_board"
    and "light switch board"); None when the map has none."""
    want = place.casefold().replace("_", " ").strip()
    for zone in m.zones.values():
        for point in zone.fixed_points:
            pid = str(point.get("id", "")).casefold().replace("_", " ")
            name = str(point.get("name", "")).casefold()
            if want and (want in pid or want in name):
                return zone, point
    return None


async def route_to(session: Session, place: str) -> dict | None:
    """Start a walk to the zone holding ``place`` when a route exists; the facts for Live, or None."""
    from aira_live.tools import walk   # lazy: tools.walk imports a lot

    settings = config.load()
    if not (settings.walk and settings.routes):
        return None
    m = shop_map.load_for(session.shop_id, settings.routes_map)
    if m is None:
        return None
    found = find_point(m, place)
    if found is None:
        return None
    zone, point = found
    if walk.route_for(session, zone.name) is None:
        return None
    await walk.start(session, zone.name)
    diag("route_to", session=session.session_id, place=place, zone=zone.id)
    facts = {"active": True, "target": zone.name, "place": point.get("name") or place,
             "where": point.get("where") or None, "switch_labels": list(point.get("switches") or [])}
    facts["say_en"] = f"I'll guide you to the {facts['place']} by the {zone.name} first, step by step."
    return facts
