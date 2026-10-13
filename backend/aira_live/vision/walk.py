""""Walk with me": one Robotics-ER look per walk frame, and the ONE short instruction it turns into.

``look`` asks ER 2 (one attempt, no Flash fallback: a skipped tick is safer than a weaker guess) for the target
(point + box), the obstacles in the walking path and a short trajectory from the owner's feet (the bottom centre
of the chest camera's image) to the target. ``decide`` turns that into one instruction deterministically, in
code, never by the model. ``Speech`` decides whether the instruction is said now: only when it changes, or every
``REASSURE_S`` as a reassurance; safety lines go at once.

Coordinates stay normalised 0-1000: points [y, x], boxes [ymin, xmin, ymax, xmax]. Step counts are estimates
("about 3 steps") from a small calibrated table, capped at ``MAX_STEPS``; when unsure there is no number at all.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

from aira_live import monitor
from aira_live.diag import diag, trunc
from aira_live.frames import Frame
from aira_live.models import model_for
from aira_live.vision import gemini
from aira_live.vision.gemini import VisionError, image_part
from aira_live.vision.point import parse_box

# --- geometry ------------------------------------------------------------------------------------------------------
CORRIDOR_X = (350, 650)      # the strip straight ahead of his feet
CORRIDOR_Y = 550             # ... from here down to the bottom of the image (close to him)
LEFT_X, RIGHT_X = 400, 600   # target x below / above: "on your left / right"
ARRIVED_H = 0.45             # target box taller than this fraction of the frame ...
ARRIVED_BOTTOM = 900         # ... or reaching this low, and centred: he is there
ARRIVED_X = (300, 700)
MAX_STEPS = 8
# box height fraction -> about how many steps away (chest camera, ~1024 px, fridge/shelf-sized targets);
# a rough calibration, always spoken as "about N steps"
STEPS_TABLE: tuple[tuple[float, int], ...] = ((0.35, 1), (0.25, 2), (0.18, 3), (0.12, 4), (0.08, 5), (0.05, 6))
MIN_STEP_BOX_H = 0.02        # a box smaller than this is too unsure for any number
REASSURE_S = 4.0             # same instruction: repeat ("keep going") after this long
HAZARD = re.compile(r"\b(steps?|stairs?|staircase|stair|ledge|drop|wet|spill|slippery|water|oil|"
                    r"person|people|man|woman|child|kid|boy|girl|customer)\b", re.I)

WALK_PROMPT = (
    "This is the chest camera of a blind man walking slowly in his small shop. The bottom centre of the image "
    "is at his feet; x 0 is his left and 1000 his right. 1) Find the {target}. If it is visible, give its point "
    "[y, x] and box_2d [ymin, xmin, ymax, xmax]; otherwise set target_visible to false. 2) List the obstacles in "
    "his walking path (straight ahead and between him and the target): chairs, tables, crates, boxes, bags, "
    "people, steps, stairs, wet floor, anything on the floor he could trip on. Give each a short English label "
    "and box_2d. Do not list the target itself, walls, the floor or the ceiling. At most 5 obstacles, nearest "
    "first. Never identify or describe people beyond the word \"person\". 3) If the target is visible, give a "
    "trajectory of 3 to 5 points [y, x] from his feet (about [1000, 500]) to the target that avoids the "
    "obstacles; otherwise an empty trajectory. All coordinates are integers normalised to 0-1000."
)
_PT = {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}
_BOX = {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}
WALK_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "target_visible": {"type": "boolean"},
        "target": {"type": "object", "properties": {"label": {"type": "string"}, "point": _PT, "box_2d": _BOX}},
        "obstacles": {"type": "array", "items": {"type": "object",
                                                 "properties": {"label": {"type": "string"}, "box_2d": _BOX},
                                                 "required": ["label", "box_2d"]}},
        "trajectory": {"type": "array", "items": _PT},
    },
    "required": ["target_visible", "obstacles", "trajectory"],
}
LOOK_TIMEOUT_S = 6.0
LOOK_MAX_OUTPUT_TOKENS = 2048

Box = tuple[int, int, int, int]
Point = tuple[int, int]


@dataclass(frozen=True)
class Obstacle:
    label: str
    box: Box

    @property
    def hazard(self) -> bool:
        return HAZARD.search(self.label) is not None


@dataclass(frozen=True)
class WalkView:
    target_point: Point | None = None
    target_box: Box | None = None
    obstacles: tuple[Obstacle, ...] = ()
    trajectory: tuple[Point, ...] = ()

    @property
    def has_target(self) -> bool:
        return self.target_point is not None


def _point(raw: Any) -> Point | None:
    if (isinstance(raw, (list, tuple)) and len(raw) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1000 for v in raw)):
        return (round(raw[0]), round(raw[1]))
    return None


def _overlap(a: Box, b: Box) -> float:
    """Intersection over the smaller box's area."""
    ih = min(a[2], b[2]) - max(a[0], b[0])
    iw = min(a[3], b[3]) - max(a[1], b[1])
    if ih <= 0 or iw <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ih * iw / small if small > 0 else 0.0


def parse_walk(data: Any) -> WalkView:
    """A raw ER reply -> ``WalkView``. Malformed parts are dropped, never invented."""
    if not isinstance(data, dict):
        return WalkView()
    tgt = data.get("target") if isinstance(data.get("target"), dict) else {}
    point = _point(tgt.get("point")) if data.get("target_visible") is not False else None
    box = parse_box(tgt.get("box_2d")) if point is not None else None
    if box is not None and not (box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]):
        box = None
    if point is None and box is not None:
        point = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    obstacles: list[Obstacle] = []
    for raw in data.get("obstacles") or []:
        if not isinstance(raw, dict):
            continue
        label = raw.get("label")
        ob = parse_box(raw.get("box_2d") if raw.get("box_2d") is not None else raw.get("box"))
        if not isinstance(label, str) or not label.strip() or ob is None:
            continue
        if box is not None and _overlap(ob, box) > 0.6:
            continue  # the target listed again as an obstacle
        obstacles.append(Obstacle(re.sub(r"\s+", " ", label).strip()[:30], ob))
    traj = tuple(p for p in (_point(r) for r in (data.get("trajectory") or [])[:5]) if p is not None)
    return WalkView(point, box, tuple(obstacles[:5]), traj if point is not None else ())


async def look(frame: Frame, target: str, timeout_s: float = LOOK_TIMEOUT_S) -> WalkView:
    """One ER call (thinking low, schema-constrained, no retry, no fallback). Raises VisionError on failure."""
    model = model_for("point")
    cfg = gemini._config("low", max_output_tokens=LOOK_MAX_OUTPUT_TOKENS, json_schema=WALK_JSON_SCHEMA)
    contents = [image_part(frame.jpeg_b64), WALK_PROMPT.format(target=target)]
    started = time.perf_counter()
    try:
        resp = await asyncio.wait_for(
            gemini.client().aio.models.generate_content(model=model, contents=contents, config=cfg),
            timeout=timeout_s)
        data = gemini.extract_json(resp.text)
    except Exception as e:  # timeout, API error or a reply that is not JSON: this tick is skipped
        diag("vision_error", severity="WARNING", role="walk", model=model,
             ms=round((time.perf_counter() - started) * 1000), error=type(e).__name__, message=trunc(str(e), 200))
        raise VisionError(f"walk: {e!r}") from e
    ms = round((time.perf_counter() - started) * 1000)
    monitor.count_vision(model, ms)
    return parse_walk(data)


# --- the instruction ------------------------------------------------------------------------------------------------


# The English meaning of each instruction (``tools.walk`` sends facts plus this; Live says it in the owner's
# language). The key is the fact; these words are only its hint.
WALK_EN: dict[str, str] = {
    "walk.hazard": "Stop. {label} in front.",
    "walk.arrived": "Stop. The {name} is right in front of you. Reach forward.",
    "walk.obstacle_left": "Stop. {label} in front. Move a little left.",
    "walk.obstacle_right": "Stop. {label} in front. Move a little right.",
    "walk.left": "{name} on your left. Turn left a little.",
    "walk.right": "{name} on your right. Turn right a little.",
    "walk.ahead": "{name} ahead, about {n} steps.",
    "walk.ahead_slow": "{name} ahead. Keep going slowly.",
    "walk.not_found": "I can't find the {name}. Try turning around slowly.",
    "walk.looking_left": "Turn slowly left. I'm looking.",
    "walk.looking_right": "Turn slowly right. I'm looking.",
    "walk.memory": "The {name} was last seen {place}.",
    "walk.keep_going": "Keep going.",
}


def _en(key: str, **kw) -> str:
    return WALK_EN[key].format(**kw)


@dataclass(frozen=True)
class Instruction:
    key: str                     # the instruction (a fact), also the diag "instruction key"
    text: str
    urgent: bool = False         # safety: said at once, never held back
    arrived: bool = False        # the walk ends after this line
    steps: int | None = None


def in_corridor(box: Box) -> bool:
    return box[1] < CORRIDOR_X[1] and box[3] > CORRIDOR_X[0] and box[2] > CORRIDOR_Y


def steps_for(box: Box | None) -> int | None:
    """About how many steps to a target with this box; None when too unsure to say a number."""
    if box is None:
        return None
    h = (box[2] - box[0]) / 1000.0
    if h < MIN_STEP_BOX_H:
        return None
    for frac, n in STEPS_TABLE:
        if h >= frac:
            return n
    return MAX_STEPS


def side_with_room(ob: Obstacle, others: tuple[Obstacle, ...]) -> str:
    """"left" or "right": the side of ``ob`` with more free floor before the next obstacle or the image edge."""
    left_edge, right_edge = 0, 1000
    for o in others:
        if o is ob or o.box[2] <= CORRIDOR_Y - 150:
            continue  # far away: not in the way of a small sidestep
        if o.box[3] <= ob.box[1]:
            left_edge = max(left_edge, o.box[3])
        elif o.box[1] >= ob.box[3]:
            right_edge = min(right_edge, o.box[1])
    return "left" if ob.box[1] - left_edge > right_edge - ob.box[3] else "right"


def arrived(view: WalkView) -> bool:
    if view.target_point is None or view.target_box is None:
        return False
    b = view.target_box
    tall = (b[2] - b[0]) / 1000.0 > ARRIVED_H or b[2] > ARRIVED_BOTTOM
    return tall and ARRIVED_X[0] <= view.target_point[1] <= ARRIVED_X[1]


@dataclass
class Search:
    """What the loop knows while the target is out of view."""
    lost_s: float = 0.0               # seconds since it was last seen (or since the walk began)
    last_x: int | None = None         # where it was last seen in the image
    memory_place: str | None = None   # the saved "last seen" place, spoken once
    memory_side: str | None = None    # "left" / "right" from that place, if any
    memory_said: bool = False


NOT_FOUND_S = 8.0


def decide(view: WalkView, name: str, lang: str = "en", search: Search | None = None) -> Instruction:
    """The ONE instruction for this look. Safety first, then arrival, obstacle, direction, search. ``text`` is
    the English meaning only (``lang`` is ignored: Live says it in the owner's language)."""
    search = search or Search()
    near = sorted((o for o in view.obstacles if in_corridor(o.box)), key=lambda o: -o.box[2])
    hazards = [o for o in near if o.hazard]
    if hazards:
        return Instruction("walk.hazard", _en("walk.hazard", label=hazards[0].label), urgent=True)
    if arrived(view):
        return Instruction("walk.arrived", _en("walk.arrived", name=name), urgent=True, arrived=True)
    if near:
        side = side_with_room(near[0], view.obstacles)
        key = f"walk.obstacle_{side}"
        return Instruction(key, _en(key, label=near[0].label), urgent=True)
    if view.target_point is not None:
        x = view.target_point[1]
        if x < LEFT_X:
            return Instruction("walk.left", _en("walk.left", name=name))
        if x > RIGHT_X:
            return Instruction("walk.right", _en("walk.right", name=name))
        n = steps_for(view.target_box)
        if n is None:
            return Instruction("walk.ahead_slow", _en("walk.ahead_slow", name=name))
        return Instruction("walk.ahead", _en("walk.ahead", name=name, n=n), steps=n)
    if search.lost_s >= NOT_FOUND_S:
        return Instruction("walk.not_found", _en("walk.not_found", name=name))
    if search.last_x is not None:
        side = "left" if search.last_x < 500 else "right"
    else:
        side = search.memory_side or "left"
    key = f"walk.looking_{side}"
    text = _en(key, name=name)
    if search.memory_place and not search.memory_said:
        search.memory_said = True
        text = f"{_en('walk.memory', name=name, place=search.memory_place)} {text}"
        return Instruction("walk.memory", text)
    return Instruction(key, text)


@dataclass
class Speech:
    """Change-only speaking: a new instruction goes at once, the same one again after ``reassure_s`` (direction
    lines become "keep going"). Safety lines are never held back. ``lang`` is unused (English meanings)."""
    lang: str = "en"
    reassure_s: float = REASSURE_S
    last_key: str | None = None
    last_steps: int | None = None
    last_at: float = field(default=float("-inf"))

    def pick(self, ins: Instruction, now: float) -> str | None:
        changed = ins.key != self.last_key or (
            ins.steps is not None and self.last_steps is not None and abs(ins.steps - self.last_steps) >= 2)
        if changed:
            text = ins.text
        elif now - self.last_at >= self.reassure_s:
            text = _en("walk.keep_going") if ins.key in _DIRECTION else ins.text
        else:
            return None
        self.last_key, self.last_at = ins.key, now
        if changed:
            self.last_steps = ins.steps
        return text


_DIRECTION = frozenset({"walk.left", "walk.right", "walk.ahead", "walk.ahead_slow"})


def overlay_objects(view: WalkView, name: str) -> list[dict]:
    """The look as monitor objects: the target, the obstacles (in the corridor: "danger", drawn red) and the
    trajectory as numbered path points."""
    out: list[dict] = []
    if view.target_point is not None:
        out.append({"label": name, "point": list(view.target_point),
                    "box": list(view.target_box) if view.target_box else None})
    for o in view.obstacles:
        b = o.box
        obj = {"label": o.label, "box": list(b), "point": [(b[0] + b[2]) // 2, (b[1] + b[3]) // 2]}
        if in_corridor(b) or o.hazard:
            obj["severity"] = "danger"
        out.append(obj)
    for i, p in enumerate(view.trajectory, 1):
        out.append({"label": f"path {i}", "point": list(p), "box": None})
    return out


FAST_CLOSE_BOTTOM = 750     # a phone box reaching this low ...
FAST_CLOSE_H = 0.30         # ... and this tall, in the corridor: close enough to stop at once
FAST_LABELS = re.compile(r"\b(person|chair|bench|table|dining table|couch|suitcase|bicycle|motorcycle|dog|cat|"
                         r"potted plant|refrigerator|box|crate)\b", re.I)


def fast_stop_label(objects) -> str | None:
    """From the phone's own detector boxes: the label to stop for, or None. A bump-guard "danger" box always
    counts; otherwise a person or large object low and tall in the corridor."""
    for o in objects or []:
        get = (lambda k, o=o: o.get(k)) if isinstance(o, dict) else (lambda k, o=o: getattr(o, k, None))
        box = parse_box(list(get("box") or []) or None)
        label = str(get("label") or "").strip()
        if box is None or not label:
            continue
        if get("severity") == "danger" and in_corridor(box):
            return label
        if (FAST_LABELS.search(label) and in_corridor(box) and box[2] >= FAST_CLOSE_BOTTOM
                and (box[2] - box[0]) / 1000.0 >= FAST_CLOSE_H):
            return label
    return None
