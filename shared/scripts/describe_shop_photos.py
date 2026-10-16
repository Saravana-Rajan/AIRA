"""Draft the short view texts of a shop map (shared/catalog/<shop>_map.json) from reference photos.

  uv run --project backend python shared/scripts/describe_shop_photos.py \\
      --map shared/catalog/demo_cricket_map.json --photos shared/catalog/photos/demo_cricket/ \\
      [--overwrite] [--dry-run] [--zone <id>]

Photo naming convention: ``<zone_id>_<facing>.jpg`` (also ``.jpeg`` / ``.png``), facing one of
front / left / right / back, e.g. ``entrance_front.jpg`` or ``bat_wall_left.png``. The script looks for photos
in two places: every file in ``--photos`` that follows the convention, and the names listed in each zone's
``photos`` array (resolved inside ``--photos``) when they follow it. A file that does not match is ignored with
one line.

For each photo the ``describe`` model (aira_live.models, role "describe") writes ONE short landmark sentence
(plain English, no people, no guessed distances) as ``{"view": "..."}``. The text is trimmed and capped at
160 characters (the schema limit).

The model proposes, this script commits, and a person checks: a view is written when it is empty or missing,
when the facing is already in the zone's ``draft_views`` (a machine draft may be refreshed), or with
``--overwrite``. A text written by a person (non-empty and not in ``draft_views``) is never replaced without
``--overwrite``. Every written facing is added to ``draft_views`` and the map's top-level ``"draft"`` is true
while any zone still has unchecked drafts; a person clears a facing from ``draft_views`` after reading it.
The result is validated against the map schema before it is written back; ``--dry-run`` writes nothing.

Needs GEMINI_API_KEY (from the environment or the repo's .env, through aira_live.config).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from aira_live import shop_map  # noqa: E402
from aira_live.models import model_for  # noqa: E402
from aira_live.vision.gemini import client as gemini_client, extract_json  # noqa: E402

log = logging.getLogger("describe_shop_photos")

FACINGS = shop_map.FACINGS
MAX_VIEW_CHARS = 160
MIME_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
_NAME_RE = re.compile(r"^(?P<zone>[a-z][a-z0-9_]*)_(?P<facing>front|left|right|back)\.(?P<ext>jpe?g|png)$",
                      re.IGNORECASE)

PROMPT = (
    "This photo was taken by a chest-mounted phone inside a small shop, in the zone called {zone_name!r}. "
    "Write ONE short sentence in plain English (at most 120 characters) naming the fixed landmarks a blind "
    "person could be told about: furniture, shelves, racks, doors, walls, big objects, their left/right "
    "position. No people, no guessed distances or measurements, no brand claims you cannot read, no opinions. "
    'Return ONLY JSON: {{"view": "<sentence>"}}'
)

Describe = Callable[[bytes, str, str], str]   # (image_bytes, mime, zone_name) -> view text
Action = tuple[str, str, str]                 # (zone_id, facing, "written" | "kept" | "overwritten")


# ---------- photo names ----------
def parse_photo_name(name: str) -> tuple[str, str] | None:
    """``("bat_wall", "left")`` for ``bat_wall_left.jpg``; None when the name does not follow the convention."""
    m = _NAME_RE.match(Path(name).name)
    if not m:
        return None
    return m.group("zone").lower(), m.group("facing").lower()


def find_photos(map_data: dict, photos_dir: Path, only_zone: str | None = None) -> dict[tuple[str, str], Path]:
    """``{(zone_id, facing): path}`` for every photo found: the zones' ``photos`` arrays first, then every
    conventional file name in ``photos_dir``. A name that does not follow the convention is skipped with one
    log line; a missing file is skipped with one log line."""
    zone_ids = {z["id"] for z in map_data.get("zones", [])}
    found: dict[tuple[str, str], Path] = {}
    for z in map_data.get("zones", []):
        for name in z.get("photos") or []:
            parsed = parse_photo_name(name)
            if parsed is None:
                log.info("skip %s: name does not follow <zone_id>_<facing>.jpg", name)
                continue
            path = photos_dir / Path(name).name
            if not path.is_file():
                log.info("skip %s: file not found in %s", name, photos_dir)
                continue
            found.setdefault(parsed, path)
    if photos_dir.is_dir():
        for path in sorted(photos_dir.iterdir()):
            if not path.is_file():
                continue
            parsed = parse_photo_name(path.name)
            if parsed is None:
                if path.suffix.lower() in MIME_TYPES:
                    log.info("skip %s: name does not follow <zone_id>_<facing>.jpg", path.name)
                continue
            found.setdefault(parsed, path)
    out = {k: v for k, v in found.items() if k[0] in zone_ids and (only_zone is None or k[0] == only_zone)}
    for (zid, facing), path in found.items():
        if zid not in zone_ids:
            log.info("skip %s: no zone %r in the map", path.name, zid)
    return out


# ---------- the model ----------
def describe_photo(client: Any, model: str, image_bytes: bytes, mime: str, zone_name: str) -> str:
    """One short view text for the photo from the vision model, or "" when the reply is unusable (not JSON,
    no ``view`` string, or empty). Never raises on a bad reply; an API error propagates."""
    from google.genai import types

    part = types.Part.from_bytes(data=image_bytes, mime_type=mime)
    resp = client.models.generate_content(model=model, contents=[part, PROMPT.format(zone_name=zone_name)])
    return clean_view(parse_reply(getattr(resp, "text", None)))


def parse_reply(text: str | None) -> str:
    """The ``view`` string from the model reply, or "" when the reply is not JSON or has no usable view."""
    try:
        data = extract_json(text)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    view = data.get("view")
    return view if isinstance(view, str) else ""


def clean_view(text: str) -> str:
    """Whitespace-collapsed, trimmed, cut at the schema's 160 characters."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) > MAX_VIEW_CHARS:
        text = text[:MAX_VIEW_CHARS].rstrip()
    return text


# ---------- the pure core ----------
def apply_drafts(map_data: dict, drafts: dict[tuple[str, str], str], overwrite: bool = False
                 ) -> tuple[dict, list[Action]]:
    """A new map dict with the drafts applied, and what happened per (zone, facing).

    A view is written when it is missing/empty, when the facing is in the zone's ``draft_views`` (a machine draft
    is refreshed), or with ``overwrite``; a human-edited text is otherwise ``kept``. Every written facing joins
    ``draft_views``; the top-level ``draft`` is true while any zone has a non-empty ``draft_views``. An empty
    draft text is never written. The result is validated (``shop_map.validate``) and raises ``MapError``.
    """
    out = json.loads(json.dumps(map_data))   # deep copy of plain JSON
    zones = {z["id"]: z for z in out.get("zones", [])}
    actions: list[Action] = []
    for (zid, facing), text in drafts.items():
        if zid not in zones or facing not in FACINGS:
            continue
        text = clean_view(text)
        if not text:
            continue
        zone = zones[zid]
        views = zone.setdefault("views", {})
        draft_views = list(zone.get("draft_views") or [])
        current = (views.get(facing) or "").strip()
        is_draft = facing in draft_views
        if not current:
            action = "written"
        elif is_draft or overwrite:
            action = "overwritten"
        else:
            actions.append((zid, facing, "kept"))
            continue
        views[facing] = text
        if facing not in draft_views:
            draft_views.append(facing)
        zone["draft_views"] = [f for f in FACINGS if f in draft_views]
        actions.append((zid, facing, action))
    out["draft"] = any(z.get("draft_views") for z in out.get("zones", []))
    shop_map.validate(out)
    return out, actions


def dump_map(map_data: dict) -> str:
    return json.dumps(map_data, indent=2, ensure_ascii=False) + "\n"


# ---------- CLI ----------
def _args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--map", required=True, help="shared/catalog/<shop>_map.json")
    p.add_argument("--photos", required=True, help="folder with <zone_id>_<facing>.jpg photos")
    p.add_argument("--overwrite", action="store_true", help="replace human-written view texts too")
    p.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
    p.add_argument("--zone", default=None, help="only this zone id")
    return p.parse_args(argv)


def main(argv: list[str] | None = None, describe: Describe | None = None) -> int:
    """Returns 0 on success, 1 when the map cannot be read or the result does not validate, 2 when nothing was
    done because no photo matched. ``describe(image_bytes, mime, zone_name) -> str`` is injected by tests;
    the default asks the ``describe`` role's model through the Gemini client."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    a = _args(argv)
    map_path = Path(a.map)
    photos_dir = Path(a.photos)
    try:
        map_data = json.loads(map_path.read_text(encoding="utf-8"))
        shop_map.validate(map_data)
    except (OSError, ValueError, shop_map.MapError) as exc:
        log.error("cannot use map %s: %s", map_path, exc)
        return 1
    if a.zone and a.zone not in {z["id"] for z in map_data["zones"]}:
        log.error("no zone %r in %s", a.zone, map_path)
        return 1

    photos = find_photos(map_data, photos_dir, a.zone)
    if not photos:
        log.warning("no photos named <zone_id>_<facing>.jpg|.jpeg|.png found in %s", photos_dir)
        return 2

    if describe is None:
        model = model_for("describe")
        client = gemini_client()

        def describe(image_bytes: bytes, mime: str, zone_name: str) -> str:
            return describe_photo(client, model, image_bytes, mime, zone_name)

    names = {z["id"]: z.get("name", z["id"]) for z in map_data["zones"]}
    drafts: dict[tuple[str, str], str] = {}
    for (zid, facing), path in sorted(photos.items()):
        mime = MIME_TYPES[path.suffix.lower()]
        try:
            text = clean_view(describe(path.read_bytes(), mime, names[zid]))
        except Exception as exc:  # noqa: BLE001 - one bad photo must not stop the others
            log.warning("skip %s/%s (%s): model call failed: %s", zid, facing, path.name, exc)
            continue
        if not text:
            log.warning("skip %s/%s (%s): the model reply had no usable view text", zid, facing, path.name)
            continue
        drafts[(zid, facing)] = text

    try:
        result, actions = apply_drafts(map_data, drafts, overwrite=a.overwrite)
    except shop_map.MapError as exc:
        log.error("result does not follow the map schema, nothing written: %s", exc)
        return 1
    for zid, facing, action in actions:
        text = map_view(result if action != "kept" else map_data, zid, facing)
        log.info("%-11s %s/%s: %s", action, zid, facing, text)
    changed = [x for x in actions if x[2] != "kept"]
    if a.dry_run:
        log.info("dry run: %d view(s) would be written to %s", len(changed), map_path)
        return 0
    if not changed:
        log.info("nothing to write (%d view(s) kept)", len(actions))
        return 0
    map_path.write_text(dump_map(result), encoding="utf-8")
    log.info("wrote %d view(s) to %s (draft=%s)", len(changed), map_path, result.get("draft"))
    return 0


def map_view(map_data: dict, zid: str, facing: str) -> str:
    return next((z.get("views", {}).get(facing, "") for z in map_data["zones"] if z["id"] == zid), "")



if __name__ == "__main__":
    sys.exit(main())
