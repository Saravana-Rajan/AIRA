""""Walk with me": the navigation policy, in code, over Gemini Live's own nav reports. No model call here.

Perception is Gemini Live itself (the same Live camera + voice session that answers the owner): the walk loop
(``tools.walk``) asks it every ~2 s with a ``[NAV CHECK n]`` note and Live answers by calling the ``nav_report``
tool with a small structured observation (``NavReport``): is the target visible (side, size), the obstacles
(label, side, in the walking path or not, size), whether the path looks clear / blocked / uncertain in the current
view, and whether the view is usable at all. There are no boxes and no distances: every line uses a side and
"ahead" / "close" only, never a number of steps or metres.

``decide`` turns one report (plus the last three) into ONE instruction deterministically; ``Speech`` decides
whether it is said now (dedupe, minimum gap, reassurance); ``gate`` applies freshness. The phone's own MediaPipe
boxes (``fast_stop_label``) are the instant safety detector: a person or large object close in the corridor
says "Stop." before Live answers; the walk's own target never triggers it.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from aira_live.vision.point import parse_box

SIDES = ("left", "ahead", "right")
SIZES = ("small", "medium", "large")
PATHS = ("clear_in_view", "blocked", "uncertain")

HAZARD = re.compile(r"\b(steps?|stairs?|staircase|ledge|drop|edge|hole|wet|spill|slippery|water|oil|"
                    r"glass|cable|wire)\b", re.I)
PERSON = re.compile(r"\b(person|people|man|woman|child|kid|boy|girl|customer)\b", re.I)
MAX_OBSTACLES = 5

# The walk's target and the phone detector's / Live's labels for the same thing.
_SYNONYMS = (
    frozenset({"fridge", "refrigerator", "freezer", "cooler", "chiller"}),
    frozenset({"door", "doorway", "entrance", "exit"}),
    frozenset({"shelf", "shelves", "rack"}),
    frozenset({"counter", "desk", "table"}),
)
_STOP_WORDS = frozenset({"the", "a", "an", "my", "to", "of", "near", "by", "big", "small"})


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z]+", (text or "").casefold()):
        if w in _STOP_WORDS:
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def same_thing(label: str, target: str) -> bool:
    """True when ``label`` names the walk's own target ("refrigerator" for the "fridge", "curd shelf" for the
    "shelf"). The target is never an obstacle and never a fast stop."""
    a, b = _words(label), _words(target)
    if not a or not b:
        return False
    if a & b:
        return True
    return any(a & group and b & group for group in _SYNONYMS)


@dataclass(frozen=True)
class Obstacle:
    label: str
    side: str = "ahead"       # left | ahead | right, in the current view
    in_path: bool = True      # in his walking path (straight ahead of his feet)
    size: str = "medium"      # small | medium | large: large means close, never a distance

    @property
    def hazard(self) -> bool:
        return HAZARD.search(self.label) is not None

    @property
    def person(self) -> bool:
        return PERSON.search(self.label) is not None

    @property
    def key(self) -> str:
        return f"{' '.join(sorted(_words(self.label))) or self.label.casefold()}@{self.side}"


@dataclass(frozen=True)
class NavReport:
    """One observation from Live's ``nav_report`` call, normalised (``parse_report``)."""
    check_id: int = 0
    target_visible: bool = False
    target_side: str = ""
    target_size: str = ""
    obstacles: tuple[Obstacle, ...] = ()
    path: str = "uncertain"
    view_ok: bool = True

    @property
    def usable(self) -> bool:
        """A view the policy may give directions from."""
        return self.view_ok and self.path != "uncertain"

    @property
    def in_path(self) -> tuple[Obstacle, ...]:
        """Obstacles in his walking path, the closest (largest) first."""
        rank = {"large": 0, "medium": 1, "small": 2}
        return tuple(sorted((o for o in self.obstacles if o.in_path), key=lambda o: rank.get(o.size, 1)))


def _choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    v = str(value or "").strip().casefold()
    return v if v in allowed else default


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().casefold()
        if v in ("true", "yes", "1"):
            return True
        if v in ("false", "no", "0"):
            return False
    return default


def parse_report(target: str, *, check_id: Any = 0, target_visible: Any = False, target_side: Any = "",
                 target_size: Any = "", obstacles: Any = None, path: Any = "uncertain",
                 view_ok: Any = True) -> NavReport:
    """Live's raw ``nav_report`` arguments -> ``NavReport``. Unknown values fall back to the cautious reading: an
    obstacle with an unknown side is ahead and in the path, an unknown path is "uncertain". The target listed
    as an obstacle is dropped (the fridge never stops a walk to the fridge)."""
    try:
        cid = int(check_id)
    except (TypeError, ValueError):
        cid = -1
    obs: list[Obstacle] = []
    for raw in obstacles if isinstance(obstacles, (list, tuple)) else []:
        if not isinstance(raw, dict):
            continue
        label = re.sub(r"\s+", " ", str(raw.get("label") or "")).strip()[:30]
        if not label or same_thing(label, target):
            continue
        obs.append(Obstacle(label, _choice(raw.get("side"), SIDES, "ahead"), _bool(raw.get("in_path"), True),
                            _choice(raw.get("size"), SIZES, "medium")))
    visible = _bool(target_visible, False)
    return NavReport(
        check_id=cid,
        target_visible=visible,
        target_side=_choice(target_side, SIDES, "") if visible else "",
        target_size=_choice(target_size, SIZES, "") if visible else "",
        obstacles=tuple(obs[:MAX_OBSTACLES]),
        path=_choice(path, PATHS, "uncertain"),
        view_ok=_bool(view_ok, False),
    )


# --- the instruction ------------------------------------------------------------------------------------------------

# Kinds: urgent ones are stops, said at once from one report however old; the rest need a fresh, confirmed view.
HAZARD_KIND, OBSTACLE_KIND, DEGRADED_KIND = "hazard", "obstacle", "degraded"
DIRECTION_KIND, ARRIVAL_KIND, SEARCH_KIND = "direction", "arrival", "search"
HOLD_KIND = "hold"   # something maybe in the path, not confirmed yet: say nothing, give no direction

DEGRADED_TEXT = "I can't see the path clearly right now. Please stop for a moment."
KEEP_GOING_TEXT = "Keep going slowly."
NOT_FOUND_S = 8.0


@dataclass(frozen=True)
class Instruction:
    key: str                     # the instruction identity (dedupe, confirmation, diag)
    text: str                    # the English meaning only: Live says it in the owner's language
    kind: str = DIRECTION_KIND
    urgent: bool = False         # a stop: said at once (speak_notice urgent), never held back
    facts: dict = field(default_factory=dict, compare=False)
    short: str | None = None     # an urgent line said again soon after: its shorter form


@dataclass
class Search:
    """What the loop knows while the target is out of view."""
    lost_s: float = 0.0               # seconds since it was last seen (or since the walk began)
    last_side: str | None = None      # where it was last seen ("left" / "right")
    memory_place: str | None = None   # the saved "last seen" place, spoken once
    memory_side: str | None = None    # "left" / "right" from that place, if any
    memory_said: bool = False


def _cap(label: str) -> str:
    return label[:1].upper() + label[1:]


def _a(label: str) -> str:
    return f"{'an' if label[:1].casefold() in 'aeiou' else 'a'} {label}"


def stop_word(name: str | None) -> str:
    """"Sam, stop." when the owner's name is known, else "Stop."."""
    return f"{name}, stop." if name else "Stop."


def seen_in(recent: Sequence[NavReport], key: str, n: int = 3) -> int:
    """In how many of the last ``n`` reports an in-path obstacle with this key was seen."""
    return sum(1 for r in list(recent)[-n:] if any(o.key == key for o in r.in_path))


def _side_options(report: NavReport, ob: Obstacle) -> list[str]:
    if ob.side == "left":
        return ["right"]
    if ob.side == "right":
        return ["left"]
    options = [report.target_side] if report.target_side in ("left", "right") else []
    return options + [s for s in ("left", "right") if s not in options]


def open_side(report: NavReport, ob: Obstacle) -> str | None:
    """The side around ``ob`` with no obstacle at all in this view (the evidence rules not yet checked)."""
    for side in _side_options(report, ob):
        if not any(o.side == side for o in report.obstacles):
            return side
    return None


def clearer_side(report: NavReport, ob: Obstacle, recent: Sequence[NavReport]) -> str | None:
    """"left" / "right" only when the evidence rules hold: the path is blocked, the view is usable, the same
    obstacle was seen in at least 2 of the last 3 reports, and the other side has no obstacle at all in this
    view. Otherwise None: a direction is never invented."""
    if report.path != "blocked" or not report.usable or seen_in(recent, ob.key) < 2:
        return None
    return open_side(report, ob)


def decide(report: NavReport, target: str, recent: Sequence[NavReport] = (), search: Search | None = None,
           name: str | None = None) -> Instruction:
    """The ONE instruction for this report. Hazards, then obstacles in the path, then an unusable view, then
    the target (arrival candidate, side), then searching. ``recent`` ends with ``report`` itself; ``name`` is
    the owner's name when known (a stop then starts "Sam, stop.")."""
    search = search or Search()
    recent = list(recent) or [report]
    stop = stop_word(name)
    in_path = report.in_path
    for o in in_path:
        if o.hazard:
            return Instruction(f"walk.hazard:{o.key}", f"{stop} {_cap(o.label)} ahead.", HAZARD_KIND, True,
                               {"action": "stop", "hazard": o.label, "address": name},
                               short=f"Stop. {_cap(o.label)}.")
    for o in in_path:
        if o.person and o.size == "large":
            return Instruction(f"walk.person:{o.key}", f"{stop} Someone is right in front of you.", HAZARD_KIND,
                               True, {"action": "stop", "hazard": "person", "close": True, "address": name},
                               short="Stop. Someone in front.")
    if in_path:
        o = in_path[0]
        if o.size != "large" and seen_in(recent, o.key) < 2:
            # Live's reports flicker: a small / medium obstacle needs 2 of the last 3 reports before a stop is
            # said (the phone's own detector stops at once for anything close). Meanwhile no direction either.
            return Instruction(f"walk.unconfirmed:{o.key}", "", HOLD_KIND, facts={"obstacle": o.label})
        side = clearer_side(report, o, recent)
        if side is not None:
            return Instruction(f"walk.obstacle:{o.key}:{side}",
                               f"{stop} There's {_a(o.label)} ahead. The {side} side looks clearer in the "
                               "current view. Step that way slowly.",
                               OBSTACLE_KIND, True,
                               {"action": "stop", "obstacle": o.label, "clearer_side": side,
                                "in_current_view": True, "address": name},
                               short=f"Stop. {_cap(o.label)} ahead. The {side} side looks clearer.")
        room = open_side(report, o) if report.usable else None
        if room is not None:
            return Instruction(f"walk.obstacle:{o.key}:open_{room}",
                               f"{stop} There's {_a(o.label)} ahead. I can see more open space on your {room}, "
                               "but I can't confirm the way is clear. Stay stopped while we check.",
                               OBSTACLE_KIND, True,
                               {"action": "stop", "obstacle": o.label, "open_space": room,
                                "clear_way": "unconfirmed", "address": name},
                               short=f"Stop. {_cap(o.label)} ahead.")
        return Instruction(f"walk.obstacle:{o.key}",
                           f"{stop} There's an obstacle ahead: {_a(o.label)}. I can't confirm a clear way.",
                           OBSTACLE_KIND, True,
                           {"action": "stop", "obstacle": o.label, "clear_way": "unconfirmed", "address": name},
                           short=f"Stop. {_cap(o.label)} ahead.")
    if report.path == "blocked":
        return Instruction("walk.blocked", f"{stop} The way ahead looks blocked. I can't confirm a clear way.",
                           OBSTACLE_KIND, True,
                           {"action": "stop", "path": "blocked", "clear_way": "unconfirmed", "address": name},
                           short="Stop. The way looks blocked.")
    if not report.usable:
        return Instruction("walk.degraded", DEGRADED_TEXT, DEGRADED_KIND, True,
                           {"action": "stop", "reason": "view_unclear", "wait_for": "clear_view"})
    if report.target_visible:
        side, large = report.target_side or "ahead", report.target_size == "large"
        if side == "ahead" and large:
            return Instruction("walk.arrival_candidate",
                               f"The {target} appears to be just ahead. Reach forward carefully to confirm.",
                               ARRIVAL_KIND,
                               facts={"action": "reach_check", "target": target, "direction": "ahead",
                                      "arrived": "unconfirmed"})
        if side in ("left", "right"):
            close = ", and it looks close" if large else ""
            return Instruction(f"walk.{side}", f"The {target} is on your {side}{close}. Turn {side} a little.",
                               facts={"action": "turn", "direction": side, "target": target, "close": large})
        return Instruction("walk.ahead", f"The {target} is ahead. Keep going slowly.",
                           facts={"action": "go", "direction": "ahead", "slowly": True, "target": target})
    if search.lost_s >= NOT_FOUND_S:
        return Instruction("walk.not_found", f"I can't find the {target} yet. Try turning around slowly.",
                           SEARCH_KIND,
                           facts={"action": "search", "found": False, "target": target, "turn": "around slowly"})
    side = search.last_side or search.memory_side or "left"
    text = f"Turn slowly {side}. I'm looking for the {target}."
    if search.memory_place and not search.memory_said:
        search.memory_said = True
        return Instruction("walk.memory", f"The {target} was last seen {search.memory_place}. {text}", SEARCH_KIND,
                           facts={"action": "search", "target": target, "last_seen": search.memory_place,
                                  "turn": side})
    return Instruction(f"walk.looking_{side}", text, SEARCH_KIND,
                       facts={"action": "search", "target": target, "turn": side})


def resume_line(target: str, label: str | None) -> Instruction:
    """After a stop, two fresh usable reports without anything in the path: carry on toward the destination
    (never a "clear" or "safe" claim)."""
    what = f"The {label} is" if label else "The obstacle is"
    return Instruction("walk.resume", f"{what} no longer in your path in the current view. Walk on slowly toward "
                       f"the {target}.", DIRECTION_KIND,
                       facts={"action": "resume", "target": target, "slowly": True,
                              "obstacle_gone": label or "obstacle"})


# --- when a line is said --------------------------------------------------------------------------------------------
DEDUPE_S = 6.0           # the same non-urgent instruction again only after this long
MIN_GAP_S = 2.0          # never two guidance lines this close, unless the second is urgent
REASSURE_S = 10.0        # a repeated direction becomes "Keep going slowly." at most this often (quiet stretches)
ARRIVAL_REPEAT_S = 15.0  # the arrival-candidate line again only after this long


@dataclass
class Speech:
    """Pacing. Urgent lines (stops) are NEVER suppressed: said again soon after, they take their short form.
    Non-urgent: a new key goes at once (after the 2 s gap); the same key waits ``DEDUPE_S``; a repeated direction
    becomes "Keep going slowly." only when ``reassure`` allows."""
    dedupe_s: float = DEDUPE_S
    min_gap_s: float = MIN_GAP_S
    last_at: float = field(default=float("-inf"))      # any guidance line
    last_key: str | None = None
    said: dict[str, float] = field(default_factory=dict)

    def pick(self, ins: Instruction, now: float, reassure: bool = False) -> str | None:
        last = self.said.get(ins.key, float("-inf"))
        if ins.urgent:
            text = ins.short if (ins.short and now - last < self.dedupe_s) else ins.text
            self.mark(ins.key, now)
            return text
        wait = ARRIVAL_REPEAT_S if ins.kind == ARRIVAL_KIND else self.dedupe_s
        if now - last < wait or now - self.last_at < self.min_gap_s:
            return None
        text = ins.text
        if ins.kind == DIRECTION_KIND and ins.key == self.last_key:
            if not reassure:
                return None
            text = KEEP_GOING_TEXT
        self.mark(ins.key, now)
        return text

    def mark(self, key: str, now: float) -> None:
        self.said = {k: t for k, t in self.said.items() if now - t < max(self.dedupe_s, ARRIVAL_REPEAT_S)}
        self.said[key] = now
        self.last_key, self.last_at = key, now

    def reset(self) -> None:
        self.said.clear()
        self.last_key, self.last_at = None, float("-inf")


# --- freshness: how old an observation may be when its line is said ------------------------------------------------
# Age = seconds since the newest live frame was forwarded to Live before the check was sent, measured when the
# line is about to be said (so it includes the check's latency). Stops are always said (stopping on old news is
# safe); every other line needs age <= WALK_DIRECTION_MAX_AGE_S. Live gives no boxes, so there are no step counts
# (WALK_MAX_AGE_S, the old limit for a step count, is kept only as the "very fresh" mark in diagnostics).
WALK_MAX_AGE_S = 1.5
WALK_DIRECTION_MAX_AGE_S = 3.0


def _age(age_s: float | None) -> float:
    try:
        age = float(age_s)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("inf")
    return age if math.isfinite(age) and age >= 0 else float("inf")


def expired(age_s: float | None) -> bool:
    """True when an observation this old may say nothing but a stop."""
    return _age(age_s) > WALK_DIRECTION_MAX_AGE_S


def gate(ins: Instruction, age_s: float | None) -> Instruction | None:
    """``ins`` when it may be said from an observation ``age_s`` seconds old (None: unknown, treated as stale),
    else None. Urgent lines (stops) always pass."""
    if ins.urgent:
        return ins
    return None if expired(age_s) else ins


# --- the phone's own detector: instant stop -------------------------------------------------------------------------
CORRIDOR_X = (350, 650)      # the strip straight ahead of his feet (phone boxes, 0-1000)
CORRIDOR_Y = 550             # ... from here down to the bottom of the image (close to him)
FAST_CLOSE_BOTTOM = 750      # a phone box reaching this low ...
FAST_CLOSE_H = 0.30          # ... and this tall, in the corridor: close enough to stop at once
# COCO labels only (the phone detector's vocabulary): no "box"/"crate" (never produced) and no "refrigerator"
# (a walk to the fridge must not stop for the fridge; any other target is excluded by ``same_thing``).
FAST_LABELS = re.compile(r"\b(person|chair|bench|table|dining table|couch|suitcase|bicycle|motorcycle|dog|cat|"
                         r"potted plant)\b", re.I)


def in_corridor(box) -> bool:
    return box[1] < CORRIDOR_X[1] and box[3] > CORRIDOR_X[0] and box[2] > CORRIDOR_Y


def fast_stop_label(objects: Iterable[Any] | None, target: str = "") -> str | None:
    """From the phone's own detector boxes: the label to stop for, or None. A bump-guard "danger" box in the
    corridor always counts; otherwise a person or large object low and tall in the corridor. The walk's own
    target (``target``) never counts."""
    for o in objects or []:
        get = (lambda k, o=o: o.get(k)) if isinstance(o, dict) else (lambda k, o=o: getattr(o, k, None))
        box = parse_box(list(get("box") or []) or None)
        label = str(get("label") or "").strip()
        if box is None or not label or (target and same_thing(label, target)):
            continue
        if get("severity") == "danger" and in_corridor(box):
            return label
        if (FAST_LABELS.search(label) and in_corridor(box) and box[2] >= FAST_CLOSE_BOTTOM
                and (box[2] - box[0]) / 1000.0 >= FAST_CLOSE_H):
            return label
    return None
