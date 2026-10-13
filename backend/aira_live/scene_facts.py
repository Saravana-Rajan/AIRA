"""Scene facts: what AIRA last counted in this session, kept briefly so "are you sure?" gets an honest answer.

Each fact is one observation the owner was told: the item, the count (or estimate), where it came from
(``fresh_count``: two frames read by ER 2; ``background_precount``: one quick read), whether it was verified, when
the frame was taken (``observed_at``, phone clock ms) and the scene version it belongs to (the ``ts`` of the last
``changed`` live frame, the same id ``vision.precount`` uses).

A fact is invalid once the camera moved to a new scene (``camera_moved``, fed by ``main.live_frame`` on every
changed live frame), and is dropped after ``EXPIRY_S`` or when a newer observation of the same item supersedes it.
A recalled fact is never a fresh count: it says its age, how it was checked and whether the camera moved since,
and suggests counting again. It describes what was visible then, never the shop's stock.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

EXPIRY_S = 120.0
_monotonic: Callable[[], float] = time.monotonic  # tests swap in a clock


def _key(item: str) -> str:
    return " ".join((item or "").casefold().split())


@dataclass
class SceneFact:
    item: str
    count: int | None
    approx: bool
    verified: bool
    source: str                 # "fresh_count" | "background_precount"
    observed_at: float | None   # capture ts of the newest frame read (phone clock, epoch ms)
    scene: float                # scene version when the frame was taken
    recorded_at: float          # server monotonic s
    evidence: dict = field(default_factory=dict)


class SceneFacts:
    """Session-scoped, short-lived store of the last observations (one per item)."""

    def __init__(self) -> None:
        self.scene = 0.0
        self._facts: dict[str, SceneFact] = {}

    def camera_moved(self, scene: float) -> None:
        """A material camera change (a ``changed`` live frame): every fact of an older scene stops describing the
        current view. An older or equal scene id is ignored."""
        if scene > self.scene:
            self.scene = scene

    def record(self, *, item: str, count: int | None, approx: bool, verified: bool, source: str,
               observed_at: float | None, evidence: dict | None = None,
               scene: float | None = None) -> SceneFact:
        """Remember one observation; it supersedes the previous one of the same item."""
        fact = SceneFact(item=item, count=count, approx=approx, verified=verified, source=source,
                         observed_at=observed_at, scene=self.scene if scene is None else scene,
                         recorded_at=_monotonic(), evidence=dict(evidence or {}))
        self._facts[_key(item)] = fact
        return fact

    def _prune(self) -> None:
        now = _monotonic()
        for k in [k for k, f in self._facts.items() if now - f.recorded_at > EXPIRY_S]:
            del self._facts[k]

    def _view(self, f: SceneFact) -> dict:
        moved = f.scene != self.scene
        return {"item": f.item, "count": f.count, "approx": f.approx, "verified": f.verified, "source": f.source,
                "observed_at": f.observed_at, "scene": f.scene, "age_s": round(_monotonic() - f.recorded_at),
                "camera_moved_since": moved, "current": not moved, "fresh": False, "scope": "visible_then",
                "evidence": dict(f.evidence)}

    def previous(self, item: str) -> dict | None:
        """The last unexpired observation of ``item`` (as a read-only view), or None."""
        self._prune()
        f = self._facts.get(_key(item))
        return self._view(f) if f is not None else None

    def recall(self, item: str = "") -> dict:
        """The unexpired facts (all, or of ``item``), newest first, with an English ``say_en``."""
        self._prune()
        facts = sorted(self._facts.values(), key=lambda f: f.recorded_at, reverse=True)
        if item:
            want = _key(item)
            facts = [f for f in facts if _key(f.item) and (want in _key(f.item) or _key(f.item) in want)]
        views = [self._view(f) for f in facts]
        if not views:
            say = "I have no recent count to go back to. Ask me to count and I will look again."
            return {"found": False, "facts": [], "say_en": say, "facts_en": say}
        say = " ".join(_say(v) for v in views[:3]) + " Ask me to count again for a fresh count."
        return {"found": True, "facts": views, "say_en": say, "facts_en": say}


def _say(v: dict) -> str:
    age = v["age_s"]
    when = "just now" if age < 2 else f"{age} seconds ago"
    if v["count"] is None:
        what = f"I could not count the {v['item']}"
    elif v["verified"]:
        what = f"I counted {v['count']} {v['item']}, checked on two frames"
    else:
        what = f"I estimated about {v['count']} {v['item']} from one quick look, not double-checked"
    moved = " The camera has moved since, so that may no longer match what is in front of you." \
        if v["camera_moved_since"] else ""
    return f"{when.capitalize()}, {what}.{moved}"
