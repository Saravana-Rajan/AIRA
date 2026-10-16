"""The shop map behind step-by-step routes (AIRA_ROUTES=1): zones, views, hazards and hand-written routes.

The map is a JSON file (schema: shared/catalog/schemas/shop_map.schema.json) found through ``AIRA_ROUTES_MAP``
(a path) or, in a repo checkout, ``shared/catalog/<shop_id>_map.json``. ``load_for`` validates it on load; a
missing or invalid map means **no routes**: it logs one clear line and returns None, and the walk behaves exactly
as without the flag. Nothing here calls a model.

Everything is read-only data: ``ShopMap.resolve`` matches a spoken target (any language, through the aliases) to
a zone id, ``ShopMap.plan`` finds the legs from one zone to another (a direct route, else the shortest chain of
routes), and ``prompt_text`` is the short zone list for the navigation session's system instruction.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "shared" / "catalog" / "schemas" / "shop_map.schema.json"
FACINGS = ("front", "left", "right", "back")
TURNS = ("left", "right", "around")
UNKNOWN = "unknown"
# The zone list in the navigation session's system instruction is cut at this many characters (the session
# compresses its context at 32k tokens; the list must stay a small part of the prompt).
ZONE_PROMPT_MAX_CHARS = 2000
_STOP_WORDS = frozenset({"the", "a", "an", "my", "to", "of", "near", "by", "at", "me", "go", "take", "walk"})


class MapError(ValueError):
    """The map file is missing, unreadable or does not follow the schema."""


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    aliases: dict[str, tuple[str, ...]]
    landmarks: str
    views: dict[str, str]
    fixed_points: tuple[dict[str, Any], ...] = ()
    photos: tuple[str, ...] = ()


@dataclass(frozen=True)
class Leg:
    type: str                      # walk | turn | checkpoint
    steps: int = 0                 # walk
    dir: str = ""                  # turn: left | right | around
    zone: str | None = None        # checkpoint: the zone that confirms it
    landmark: str | None = None    # checkpoint: a landmark passed (confirmed by steps only)
    say: str = ""                  # checkpoint: the English meaning said when it is reached


@dataclass(frozen=True)
class Route:
    from_zone: str
    to_zone: str
    legs: tuple[Leg, ...]
    start_facing: str = "front"


@dataclass(frozen=True)
class Hazard:
    id: str
    type: str
    text: str
    zone: str | None = None
    between: tuple[str, str] | None = None
    reflects: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShopMap:
    shop_id: str
    name: str
    zones: dict[str, Zone]
    routes: tuple[Route, ...]
    hazards: tuple[Hazard, ...] = ()
    draft: bool = False
    path: str | None = None
    _alias_index: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    # ---------- targets ----------
    def resolve(self, text: str) -> str | None:
        """The zone id a spoken target names, through the zone id, name or any alias in any language; None when
        nothing matches. "take me to the bat wall" matches "bat wall"; a Tamil or Hindi alias matches as it is."""
        norm = _norm(text)
        if not norm:
            return None
        if norm in self._alias_index:
            return self._alias_index[norm]
        words = _words(norm)
        best: tuple[int, str] | None = None
        for alias, zone_id in self._alias_index.items():
            if alias in norm and not _is_latin(alias):      # a Tamil / Hindi alias inside a longer phrase
                return zone_id
            aw = _words(alias)
            if aw and aw <= words and (best is None or len(aw) > best[0]):
                best = (len(aw), zone_id)
        return best[1] if best else None

    def mirror_reflects(self, zone_id: str) -> bool:
        """True when a mirror in the shop can show this zone: a report naming it needs more evidence."""
        return any(h.type == "mirror" and zone_id in h.reflects for h in self.hazards)

    # ---------- routes ----------
    def route(self, from_zone: str, to_zone: str) -> Route | None:
        return next((r for r in self.routes if r.from_zone == from_zone and r.to_zone == to_zone), None)

    def plan(self, from_zone: str, to_zone: str) -> tuple[Leg, ...] | None:
        """The legs from one zone to another: the direct route, else the shortest chain of routes (fewest hops)
        joined end to end. None when no route leads there or a zone is unknown. The same zone: an arrival
        checkpoint only."""
        if from_zone not in self.zones or to_zone not in self.zones:
            return None
        if from_zone == to_zone:
            return (Leg("checkpoint", zone=to_zone, say=f"The {self.zones[to_zone].name} is in front of you."),)
        direct = self.route(from_zone, to_zone)
        if direct is not None:
            return direct.legs
        prev: dict[str, tuple[str, Route] | None] = {from_zone: None}
        queue: deque[str] = deque([from_zone])
        while queue:
            here = queue.popleft()
            if here == to_zone:
                break
            for r in self.routes:
                if r.from_zone == here and r.to_zone not in prev:
                    prev[r.to_zone] = (here, r)
                    queue.append(r.to_zone)
        if to_zone not in prev:
            return None
        chain: list[Route] = []
        at = to_zone
        while prev[at] is not None:
            here, r = prev[at]      # type: ignore[misc]
            chain.append(r)
            at = here
        legs: list[Leg] = []
        for r in reversed(chain):
            legs.extend(r.legs)
        return tuple(legs)

    def start_facing(self, from_zone: str, to_zone: str) -> str:
        """The facing the first leg of the plan assumes (the first route of the chain)."""
        direct = self.route(from_zone, to_zone)
        if direct is not None:
            return direct.start_facing
        for r in self.routes:
            if r.from_zone == from_zone:
                plan = self.plan(from_zone, to_zone)
                if plan and plan[:len(r.legs)] == r.legs:
                    return r.start_facing
        return "front"

    # ---------- the prompt ----------
    def prompt_text(self, max_chars: int = ZONE_PROMPT_MAX_CHARS) -> str:
        """The zone list for the navigation session: one short line per zone with its views, then the mirrors.
        Cut at ``max_chars`` (whole lines)."""
        lines = ["ZONES of this shop (report current_zone with one of these ids, or unknown):"]
        for z in self.zones.values():
            views = "; ".join(f"{f}: {z.views[f]}" for f in FACINGS if z.views.get(f))
            lines.append(f"- {z.id}: {z.landmarks}." + (f" Views {views}." if views else ""))
        for h in self.hazards:
            if h.type == "mirror":
                lines.append(f"- MIRROR: {h.text}. A view of a reflection: reflection=true and current_zone unknown.")
        out: list[str] = []
        used = 0
        for line in lines:
            if used + len(line) + 1 > max_chars:
                break
            out.append(line)
            used += len(line) + 1
        return "\n".join(out)


# ---------- loading ----------
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def _is_latin(text: str) -> bool:
    return all(ord(c) < 0x250 for c in text)


def _words(text: str) -> frozenset[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.casefold()):
        if w in _STOP_WORDS:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return frozenset(out)


def validate(data: Any) -> None:
    """Raise ``MapError`` unless ``data`` follows the schema and its zone references resolve."""
    import jsonschema

    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MapError(f"map schema missing: {SCHEMA_PATH}") from exc
    try:
        jsonschema.Draft202012Validator(schema).validate(data)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "root"
        raise MapError(f"{where}: {exc.message}") from exc
    ids = [z["id"] for z in data["zones"]]
    if len(set(ids)) != len(ids):
        raise MapError("duplicate zone id")
    known = set(ids)
    for r in data.get("routes", []):
        for key in ("from", "to"):
            if r[key] not in known:
                raise MapError(f"route {r['from']}->{r['to']}: unknown zone {r[key]!r}")
        last = r["legs"][-1]
        if last["type"] != "checkpoint":
            raise MapError(f"route {r['from']}->{r['to']}: the last leg must be the arrival checkpoint")
        for leg in r["legs"]:
            if leg["type"] == "checkpoint" and leg.get("zone") not in known and not leg.get("landmark"):
                raise MapError(f"route {r['from']}->{r['to']}: a checkpoint needs a known zone or a landmark")
    for h in data.get("hazards", []):
        for zid in [h.get("zone")] + list(h.get("between") or []) + list(h.get("reflects") or []):
            if zid is not None and zid not in known:
                raise MapError(f"hazard {h['id']}: unknown zone {zid!r}")


def from_dict(data: dict, path: str | None = None) -> ShopMap:
    """Validate and build a ``ShopMap``. Raises ``MapError``."""
    validate(data)
    zones: dict[str, Zone] = {}
    index: dict[str, str] = {}
    for z in data["zones"]:
        aliases = {lang: tuple(z["aliases"].get(lang, ())) for lang in ("en", "ta", "hi")}
        zone = Zone(z["id"], z["name"], aliases, z["landmarks"], dict(z.get("views") or {}),
                    tuple(z.get("fixed_points") or ()), tuple(z.get("photos") or ()))
        zones[zone.id] = zone
        for text in (zone.id, zone.id.replace("_", " "), zone.name, *aliases["en"], *aliases["ta"], *aliases["hi"]):
            index.setdefault(_norm(text), zone.id)
    routes = tuple(
        Route(r["from"], r["to"], tuple(_leg(leg) for leg in r["legs"]), r.get("start_facing", "front"))
        for r in data.get("routes", []))
    hazards = tuple(
        Hazard(h["id"], h["type"], h["text"], h.get("zone"),
               tuple(h["between"]) if h.get("between") else None, tuple(h.get("reflects") or ()))
        for h in data.get("hazards", []))
    return ShopMap(data["shop"]["id"], data["shop"]["name"], zones, routes, hazards, bool(data.get("draft")),
                   path, index)


def _leg(raw: dict) -> Leg:
    if raw["type"] == "walk":
        return Leg("walk", steps=int(raw["steps"]))
    if raw["type"] == "turn":
        return Leg("turn", dir=raw["dir"])
    return Leg("checkpoint", zone=raw.get("zone"), landmark=raw.get("landmark"), say=raw["say"])


def load_file(path: str | os.PathLike) -> ShopMap:
    """Read and validate one map file. Raises ``MapError`` (missing, bad JSON, bad schema)."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as exc:
        raise MapError(f"map not found: {p}") from exc
    except ValueError as exc:
        raise MapError(f"map is not JSON: {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise MapError(f"map is not an object: {p}")
    return from_dict(data, str(p))


def map_path(shop_id: str, override: str | None = None) -> Path:
    """Where the map of ``shop_id`` is: ``AIRA_ROUTES_MAP`` when set, else ``shared/catalog/<shop_id>_map.json``."""
    return Path(override) if override else REPO_ROOT / "shared" / "catalog" / f"{shop_id}_map.json"


_cache: dict[tuple[str, str | None], ShopMap | None] = {}


def load_for(shop_id: str, override: str | None = None, *, use_cache: bool = True) -> ShopMap | None:
    """The map for ``shop_id``, or None (and one log line) when it is missing or invalid: the feature is then off
    and the walk works as without the flag. Cached per path; never raises."""
    key = (shop_id, override)
    if use_cache and key in _cache:
        return _cache[key]
    path = map_path(shop_id, override)
    try:
        m = load_file(path)
    except MapError as exc:
        log.warning("routes off: shop map for %s unusable (%s)", shop_id, exc)
        m = None
    except Exception:  # noqa: BLE001 - a map must never crash the service
        log.warning("routes off: shop map for %s failed to load", shop_id, exc_info=True)
        m = None
    else:
        log.info("routes: shop map %s loaded (%d zones, %d routes%s)", path, len(m.zones), len(m.routes),
                 ", DRAFT view texts" if m.draft else "")
    if use_cache:
        _cache[key] = m
    return m


def clear_cache() -> None:
    _cache.clear()
