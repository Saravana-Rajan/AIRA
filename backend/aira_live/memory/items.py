"""Last-seen visual memory of the owner's personal items (keys, wallet, phone, glasses...), spec §5.

Whenever a frame is analysed (camera_ask, describe mode, passive frames), ``observe`` is called through
``vision.observers`` with the objects the model found and records sightings of PERSONAL items as text:
{item, place, at, confidence, kind}. ``place`` is relative to a landmark the camera saw next to it ("on the
fridge, to your far left, at arm's length"). A placing event (a hand put the keys down) is a strong memory
and beats a later passive sighting unless that one is confident.

A memory is always spoken as "I last saw", never "it is": a sighting is not proof the item is still there.
Older than 24 h says "that was a while ago". Privacy: text only (no image ever reaches this module),
every sighting expires after 7 days, and "forget everything" / "stop remembering" are honoured here.
Stored under shops/{shopId}/memory/{id} (server-only in the Firestore rules); the owner's switches live in
shops/{shopId}/memory/settings.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError, field_validator

from aira_live import db as dbmod
from aira_live import embeddings
from aira_live.i18n import t
from aira_live.vision import observers

log = logging.getLogger(__name__)

VECTOR_LIMIT = 3
VECTOR_THRESHOLD = 0.45    # cosine distance: "keys" ~ "key bunch" is well inside; "keys" vs "wallet" is outside

EXPIRY = timedelta(days=7)
OLD_AFTER = timedelta(hours=24)
STRONG_CONF = 0.8          # a passive sighting this sure can override an earlier placing event
MIN_CONF = 0.5             # below this the camera read is not evidence of anything
DEDUPE_S = 60.0            # the same item in the same place within a minute is one sighting
LANDMARK_RADIUS = 300      # a landmark counts only within this many normalised units of the item
ON_DX = 150                # item above a surface and this close in x: "on the <surface>"
SETTINGS_DOC = "settings"
IST = ZoneInfo("Asia/Kolkata")

# canonical item -> words the model or the owner may use for it (English, Tamil, Hindi)
PERSONAL_ITEMS: dict[str, tuple[str, ...]] = {
    "keys": ("keys", "key", "keychain", "key ring", "key bunch", "சாவி", "चाबी", "चाभी"),
    "wallet": ("wallet", "purse", "பர்ஸ்", "पर्स", "बटुआ"),
    "phone": ("phone", "mobile", "mobile phone", "cell phone", "smartphone", "போன்", "மொபைல்", "फ़ोन", "फोन", "मोबाइल"),
    "glasses": ("glasses", "spectacles", "specs", "eyeglasses", "sunglasses", "கண்ணாடி", "चश्मा"),
    "remote": ("remote", "remote control", "tv remote", "ரிமோட்", "रिमोट"),
    "bag": ("bag", "handbag", "backpack", "shoulder bag", "பை", "बैग", "थैला"),
    "medicine": ("medicine", "medicines", "tablets", "pills", "pill bottle", "medicine box", "மருந்து", "दवा", "दवाई"),
    "umbrella": ("umbrella", "குடை", "छाता", "छतरी"),
    "charger": ("charger", "phone charger", "charging cable", "சார்ஜர்", "चार्जर"),
}
SURFACES = frozenset({"fridge", "refrigerator", "freezer", "table", "counter", "desk", "shelf", "rack", "chair",
                      "stool", "bench", "cupboard", "cabinet", "box", "crate", "bed", "sofa", "stand"})
_PLACING = re.compile(r"\b(put|puts|putting|placed|placing|places|kept|keeps|keeping|leaves|left|drops?|dropped)\b", re.I)
_OWN = re.compile(r"^(my|the|a|an|your|our)\s+", re.I)
_WS = re.compile(r"\s+")


class Sighting(BaseModel):
    item: str = Field(min_length=1)
    place: str = Field(min_length=1, max_length=200)
    at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    kind: Literal["seen", "placed"] = "seen"
    place_parts: dict | None = None     # {side, dist, landmark, on}: i18n keys, rendered in the session language

    @field_validator("at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)

    @field_validator("place")
    @classmethod
    def _text(cls, v: str) -> str:
        v = _WS.sub(" ", v).strip()
        if not v:
            raise ValueError("place is empty")
        if v.startswith("data:") or len(v) > 200:
            raise ValueError("place must be short text, never image data")
        return v

    def is_strong(self) -> bool:
        return self.kind == "placed" or self.confidence >= STRONG_CONF


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coll(shop_id: str):
    return dbmod.db().collection("shops").document(shop_id).collection("memory")


def normalize_name(text: str) -> str:
    """"My Walking Stick" -> "walking stick"."""
    return _WS.sub(" ", _OWN.sub("", (text or "").strip().casefold())).strip(" .,?!")


def _mentions(text: str, word: str) -> bool:
    if word.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None
    return word in text


def canonical_item(text: str | None, custom: list[str] | tuple[str, ...] = ()) -> str | None:
    """The personal item named in ``text`` ("car keys" -> "keys", "my walking stick" -> an owner-named item), or None."""
    low = (text or "").casefold()
    if not low.strip():
        return None
    for name in custom:
        n = normalize_name(name)
        if n and _mentions(low, n):
            return n
    for item, words in PERSONAL_ITEMS.items():
        if any(_mentions(low, w) for w in words):
            return item
    return None


def speak_item(item: str, lang: str) -> str:
    """The item's name in ``lang`` (an owner-named item is spoken as the owner said it)."""
    key = f"mem.item.{item}"
    spoken = t(key, lang)
    return item if spoken == key else spoken


# --- settings -----------------------------------------------------------------------------------------------------


async def settings(shop_id: str) -> dict:
    """{"remembering": bool, "items": [owner-named items]}; remembering is on until the owner stops it."""
    snap = await _coll(shop_id).document(SETTINGS_DOC).get()
    d = (snap.to_dict() or {}) if snap.exists else {}
    custom = [normalize_name(x) for x in d.get("items", []) if isinstance(x, str) and normalize_name(x)]
    return {"remembering": d.get("remembering", True) is not False, "items": custom}


async def set_remembering(shop_id: str, on: bool) -> None:
    await _coll(shop_id).document(SETTINGS_DOC).set({"remembering": bool(on)}, merge=True)


async def watch_item(shop_id: str, name: str) -> str:
    """"Remember my X": add X to the items the camera watches for. Returns the canonical name."""
    item = normalize_name(name)
    if not item:
        raise ValueError("item name is empty")
    cfg = await settings(shop_id)
    if item not in cfg["items"]:
        await _coll(shop_id).document(SETTINGS_DOC).set({"items": [*cfg["items"], item]}, merge=True)
    return item


# --- sightings ----------------------------------------------------------------------------------------------------


async def record_sighting(shop_id: str, item: str, place: str, confidence: float, kind: str = "seen",
                          at: datetime | None = None, place_parts: dict | None = None) -> Sighting | None:
    """Store one text sighting (ValueError on bad input). None, and nothing stored, while remembering is off."""
    s = Sighting(item=normalize_name(item), place=place, at=at or _utcnow(), confidence=confidence, kind=kind,
                 place_parts=place_parts)
    if not (await settings(shop_id))["remembering"]:
        return None
    doc = {"item": s.item, "place": s.place, "placeParts": s.place_parts, "at": s.at, "confidence": s.confidence,
           "kind": s.kind, "expiresAt": s.at + EXPIRY}
    vec = await embeddings.embed(s.item)          # the label's words only; never the place, never the frame
    if vec:
        doc[embeddings.FIELD] = embeddings.to_vector(vec)
    await _coll(shop_id).document().set(doc)
    return s


def _parse(doc_id: str, d: dict) -> Sighting | None:
    if "item" not in d:
        return None          # the settings doc
    try:
        parts = d.get("placeParts")
        return Sighting(item=d["item"], place=d["place"], at=d["at"], confidence=d.get("confidence", 0.0),
                        kind=d.get("kind", "seen"), place_parts=parts if isinstance(parts, dict) else None)
    except (KeyError, ValidationError, TypeError):
        log.warning("ignoring malformed sighting shops/%s/memory/%s", "?", doc_id)
        return None


async def _all(shop_id: str) -> list[tuple[str, Sighting]]:
    out: list[tuple[str, Sighting]] = []
    async for snap in _coll(shop_id).stream():
        s = _parse(snap.id, snap.to_dict() or {})
        if s is not None:
            out.append((snap.id, s))
    return out


def _expired(s: Sighting, now: datetime) -> bool:
    return now - s.at > EXPIRY


async def last_seen(shop_id: str, item: str, now: datetime | None = None) -> Sighting | None:
    """The most useful unexpired sighting of ``item``: the newest strong one (a placing, or a confident read),
    else the newest of any. None when nothing was seen."""
    now = now or _utcnow()
    item = normalize_name(item)
    cands = [s for _, s in await _all(shop_id) if s.item == item and not _expired(s, now)]
    if not cands:
        cands = [s for s in await similar_sightings(shop_id, item) if not _expired(s, now)]
    if not cands:
        return None
    strong = [s for s in cands if s.is_strong()]
    return max(strong or cands, key=lambda s: s.at)


async def similar_sightings(shop_id: str, item: str, limit: int = VECTOR_LIMIT) -> list[Sighting]:
    """Sightings whose label means the same as ``item`` ("keys" ~ "key bunch"), by Firestore ``find_nearest``
    over the shop's memory (COSINE). Empty when embeddings or the vector query are unavailable."""
    query = await embeddings.embed(item, query=True)
    if not query:
        return []
    try:
        from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

        q = _coll(shop_id).find_nearest(
            vector_field=embeddings.FIELD, query_vector=embeddings.to_vector(query), limit=limit,
            distance_measure=DistanceMeasure.COSINE, distance_threshold=VECTOR_THRESHOLD)
        rows = [(snap.id, snap.to_dict() or {}) async for snap in q.stream()]
    except Exception as exc:  # noqa: BLE001 - no index yet, or a transport error: as if nothing matched
        log.warning("vector search over memory failed for %r in %s: %s", item, shop_id, exc)
        return []
    out = []
    for doc_id, d in rows:
        s = _parse(doc_id, d)
        if s is not None:
            out.append(s)
    return out


async def forget(shop_id: str, item: str | None = None) -> int:
    """Delete every sighting (or those of one item). Returns how many were removed; settings stay."""
    want = normalize_name(item) if item else None
    n = 0
    for doc_id, s in await _all(shop_id):
        if want is None or s.item == want:
            await _coll(shop_id).document(doc_id).delete()
            n += 1
    return n


async def purge_expired(shop_id: str, now: datetime | None = None) -> int:
    now = now or _utcnow()
    n = 0
    for doc_id, s in await _all(shop_id):
        if _expired(s, now):
            await _coll(shop_id).document(doc_id).delete()
            n += 1
    return n


# --- wording ------------------------------------------------------------------------------------------------------


def is_old(s: Sighting, now: datetime) -> bool:
    return now - s.at > OLD_AFTER


def _time(at: datetime, lang: str) -> str:
    local = at.astimezone(IST)
    h = local.hour % 12 or 12
    return t("mem.time", lang, h=h, m=f"{local.minute:02d}", ampm=t("mem.pm" if local.hour >= 12 else "mem.am", lang))


def when_phrase(at: datetime, now: datetime, lang: str) -> str:
    """"just now", "today at 7:10 pm", "yesterday at 6:10 pm" or "3 days ago" (shop time, Asia/Kolkata)."""
    if now - at < timedelta(minutes=2):
        return t("mem.when.now", lang)
    days = (now.astimezone(IST).date() - at.astimezone(IST).date()).days
    if days <= 0:
        return t("mem.when.today", lang, time=_time(at, lang))
    if days == 1:
        return t("mem.when.yesterday", lang, time=_time(at, lang))
    return t("mem.when.days", lang, days=days)


def speak_place(s: Sighting, lang: str) -> str:
    """The place in ``lang`` from its parts; the stored English text when there are none."""
    p = s.place_parts
    if not p or not p.get("side"):
        return s.place
    return _render_place(p, lang)


def _render_place(p: dict, lang: str) -> str:
    side, dist = t(p["side"], lang), t(p["dist"], lang)
    landmark = p.get("landmark")
    if landmark:
        key = "mem.place.on" if p.get("on") else "mem.place.near"
        return t(key, lang, landmark=landmark, side=side, dist=dist)
    return t("mem.place.plain", lang, side=side, dist=dist)


def describe(item_spoken: str, s: Sighting, lang: str, now: datetime) -> str:
    """"I last saw your keys on the fridge, today at 7:10 pm. Check there first." Never "is"."""
    key = "mem.last_seen_old" if is_old(s, now) else "mem.last_seen"
    return t(key, lang, item=item_spoken, place=speak_place(s, lang), when=when_phrase(s.at, now, lang))


# --- from an ER result --------------------------------------------------------------------------------------------


def _side_key(x: float) -> str:
    if x < 200:
        return "cam.side.far_left"
    if x < 400:
        return "cam.side.left"
    if x <= 600:
        return "cam.side.centre"
    if x <= 800:
        return "cam.side.right"
    return "cam.side.far_right"


def _dist_key(d: Any) -> str:
    d = str(d or "").lower()
    return f"cam.dist.{d}" if d in ("near", "arm", "far") else "cam.dist.arm"


def _point(obj: dict) -> tuple[float, float] | None:
    p = obj.get("point")
    if isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(v, (int, float)) for v in p):
        return float(p[0]), float(p[1])
    return None


def place_from_objects(obj: dict, objects: list[dict], custom: list[str] | tuple[str, ...] = ()) -> tuple[str, dict]:
    """(English place text, parts) for ``obj``: side and distance from its point, plus the nearest other
    non-personal object as the landmark ("on the fridge" when the item sits just above a surface)."""
    pt = _point(obj) or (500.0, 500.0)
    parts: dict = {"side": _side_key(pt[1]), "dist": _dist_key(obj.get("distance")), "landmark": None, "on": False}
    explicit = str(obj.get("on") or obj.get("place") or "").strip().casefold()
    if explicit:
        parts.update(landmark=explicit, on=bool(obj.get("on")))
    else:
        best: tuple[float, dict] | None = None
        for other in objects:
            label = str(other.get("label") or "").strip()
            op = _point(other)
            if other is obj or not label or op is None or canonical_item(label, custom):
                continue
            d = math.dist(pt, op)
            if d <= LANDMARK_RADIUS and (best is None or d < best[0]):
                best = (d, other)
        if best is not None:
            other = best[1]
            op = _point(other) or pt
            label = str(other["label"]).strip().casefold()
            surface = any(w in SURFACES for w in label.split())
            parts.update(landmark=label, on=surface and op[0] > pt[0] and abs(op[1] - pt[1]) <= ON_DX)
    return _render_place(parts, "en"), parts


def personal_in_view(objects: list[dict], lang: str, custom: list[str] | tuple[str, ...] = ()) -> dict[str, str]:
    """Describe mode helper: one short line per personal item in view, keyed by item ("Your bag: on the chair...")."""
    out: dict[str, str] = {}
    for obj in objects:
        item = canonical_item(obj.get("label"), custom)
        if item and item not in out:
            _, parts = place_from_objects(obj, objects, custom)
            out[item] = t("mem.in_view", lang, item=speak_item(item, lang), place=_render_place(parts, lang))
    return out


_recent: dict[tuple[str, str, str], datetime] = {}


def _conf(obj: dict, context: dict) -> float:
    for v in (obj.get("confidence"), context.get("confidence")):
        try:
            if v is not None:
                return min(max(float(v), 0.0), 1.0)
        except (TypeError, ValueError):
            pass
    return 0.6


async def observe(session: Any, objects: list[dict], context: dict) -> None:
    """vision.observers callback: record every personal item among ``objects`` (text only, never the frame)."""
    shop_id = getattr(session, "shop_id", None)
    if not shop_id or not objects:
        return
    cfg = await settings(shop_id)
    if not cfg["remembering"]:
        return
    custom = cfg["items"]
    now = _utcnow()
    text = " ".join(str(context.get(k) or "") for k in ("answer", "question", "event")).casefold()
    for obj in objects:
        label = str(obj.get("label") or "")
        item = canonical_item(label, custom)
        if not item:
            continue
        conf = _conf(obj, context)
        if conf < MIN_CONF:
            continue
        placed = context.get("event") == "placed" or bool(_PLACING.search(label)) or (
            bool(_PLACING.search(text)) and canonical_item(text, custom) == item)
        place, parts = place_from_objects(obj, objects, custom)
        key = (shop_id, item, place)
        last = _recent.get(key)
        if not placed and last is not None and (now - last).total_seconds() < DEDUPE_S:
            continue
        _recent[key] = now
        await record_sighting(shop_id, item, place, conf, kind="placed" if placed else "seen", at=now,
                              place_parts=parts)


observers.register(observe)
