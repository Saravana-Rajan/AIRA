"""What the camera has seen in this conversation: each object's last-seen place, kept after the camera turns away.

Written from Gemini Live's own silent ``scene_report`` answers to [SCENE CHECK n] notes (aira_live.scene_watch)
and from the walk's ``nav_report`` obstacles; read by ``memory_where`` ("where did I put my glasses?").

Every answer separates what is in view NOW (seen in the newest scene report, a few seconds old) from what was
LAST SEEN earlier, says how long ago, and flags an old sighting or two look-alike objects. It never claims an
object is still where it was seen. In memory only, per conversation: nothing is written to Firestore and nothing
survives a restart (a reconnect within the session-resume window keeps the same Session, so the same memory).
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

SIDES = ("left", "ahead", "right")
IN_VIEW_S = 6.0       # seen in a scene report this recent and not missing from a newer one: "in view now"
OLD_S = 15 * 60.0     # older than this: "a while ago, it may have moved"
MAX_ITEMS = 60        # the oldest sightings drop off first


def norm(name: str) -> str:
    """'My Reading Glasses ' -> 'reading glasses'; plural 'apples' -> 'apple' (simple English plural)."""
    n = re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", (name or "").casefold())).strip()
    n = re.sub(r"^(my|the|a|an|his|her|our|some)\s+", "", n)
    if n.endswith("glasses") or n.endswith("ss"):
        return n
    if n.endswith("ies") and len(n) > 4:
        return n[:-3] + "y"
    if n.endswith("es") and n[:-2].endswith(("ch", "sh", "x", "o")):
        return n[:-2]
    if n.endswith("s") and len(n) > 3:
        return n[:-1]
    return n


def _ago(seconds: float) -> str:
    if seconds < 45:
        return "a few seconds ago"
    if seconds < 90:
        return "about a minute ago"
    if seconds < 3600:
        return f"about {round(seconds / 60)} minutes ago"
    hours = round(seconds / 3600)
    return "about an hour ago" if hours == 1 else f"about {hours} hours ago"


@dataclass
class Sighting:
    name: str                      # as Live said it ("glasses")
    on: str | None                 # what it was on / in ("table")
    near: list[str] = field(default_factory=list)   # what was next to it ("apples")
    side: str | None = None        # left / ahead / right in that view
    at: float = 0.0                # when (memory clock)
    report: int = 0                # which scene report saw it
    count: int = 1                 # how many of these were in that view (2+: not sure which one)

    def place(self) -> str:
        parts = []
        if self.on:
            parts.append(f"on the {self.on}" if not re.match(r"^(on|in|under|next|by|near|inside)\b", self.on)
                         else self.on)
        if self.near:
            parts.append("beside the " + " and the ".join(self.near[:2]))
        return " ".join(parts) or "in the camera view"


class SceneMemory:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._items: dict[str, Sighting] = {}
        self.reports = 0
        self.last_report_at: float | None = None
        self._in_last_report: set[str] = set()

    def observe(self, items: Iterable[dict], *, at: float | None = None, replace_view: bool = True) -> int:
        """Record one scene report: ``items`` are dicts {name, on, near, side}. ``replace_view``: this report is
        the whole current view (a scene report), so anything not in it is no longer in view; False for partial
        reports (walk obstacles). Returns how many objects were recorded."""
        at = self._clock() if at is None else at
        self.reports += 1
        counts: dict[str, int] = {}
        clean: list[tuple[str, dict]] = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            key = norm(str(it.get("name") or it.get("label") or ""))
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            clean.append((key, it))
        seen: set[str] = set()
        for key, it in clean:
            if key in seen:
                continue   # the same thing twice in one view: one sighting, marked ambiguous below
            seen.add(key)
            near = it.get("near") or []
            if isinstance(near, str):
                near = [near]
            side = it.get("side") if it.get("side") in SIDES else None
            on = (str(it.get("on")).strip() or None) if it.get("on") else None
            self._items[key] = Sighting(name=str(it.get("name") or it.get("label")).strip(), on=on,
                                        near=[str(n).strip() for n in near if str(n).strip()][:3], side=side,
                                        at=at, report=self.reports, count=counts[key])
        if replace_view:
            self._in_last_report = seen
            self.last_report_at = at
        if len(self._items) > MAX_ITEMS:
            for key, _ in sorted(self._items.items(), key=lambda kv: kv[1].at)[:len(self._items) - MAX_ITEMS]:
                del self._items[key]
        return len(seen)

    def find(self, name: str) -> Sighting | None:
        key = norm(name)
        if not key:
            return None
        if key in self._items:
            return self._items[key]
        # "glasses" finds "reading glasses" and the reverse; the most recent match wins
        matches = [s for k, s in self._items.items() if key in k.split() or k in key.split()
                   or k.endswith(" " + key) or key.endswith(" " + k)]
        return max(matches, key=lambda s: s.at) if matches else None

    def in_view(self, s: Sighting, now: float) -> bool:
        return (self.last_report_at is not None and s.report == self.reports and norm(s.name) in self._in_last_report
                and now - s.at <= IN_VIEW_S)

    def recall(self, name: str, now: float | None = None) -> dict:
        """Facts for "where is my <name>": found, in_view_now, place, side, ago_s, old, ambiguous, say_en."""
        now = self._clock() if now is None else now
        s = self.find(name)
        if s is None:
            return {"found": False, "item": name, "source": "scene_memory",
                    "say_en": f"I haven't seen your {name} in this conversation. Slowly turn the camera around "
                              "the room and I'll tell you if I spot it."}
        ago = max(0.0, now - s.at)
        in_view = self.in_view(s, now)
        old = ago > OLD_S
        ambiguous = s.count > 1
        where = s.place()
        side = f", {'straight ahead' if s.side == 'ahead' else 'on the ' + s.side}" if s.side else ""
        many = s.name.endswith("s")          # "glasses", "keys": they / them
        is_, it, its = ("are", "them", "they're") if many else ("is", "it", "it's")
        if in_view:
            say = f"Your {s.name} {is_} in front of the camera now, {where}{side}."
        else:
            say = (f"I last saw your {s.name} {where}, {_ago(ago)}. I can't see {it} now, so I can't confirm "
                   f"{its} still there.")
            if old:
                say += " That was a while ago, so it may have moved."
        if ambiguous:
            say += f" I saw more than one {s.name} there, so I'm not sure which one is yours."
        return {"found": True, "item": s.name, "source": "scene_memory", "in_view_now": in_view, "place": where,
                "on": s.on, "near": s.near, "side": s.side if in_view else None, "last_side": s.side,
                "ago_s": round(ago), "old": old, "ambiguous": ambiguous, "say_en": say}

    def nearby(self, name: str, now: float | None = None) -> list[str]:
        """What was seen next to ``name`` ("what was beside the apples?")."""
        s = self.find(name)
        return list(s.near) if s else []
