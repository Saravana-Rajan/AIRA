"""Switch board: find the switches (never the sockets) so the hand can be guided to one.

The model returns structured detections only: a board box and a list of items, each ``switch``, ``socket`` or
``unknown`` with a box in 0-1000. **Code decides** which item, if any, the fingertip may be guided to
(``choose_switch``): a target is never a socket, never near a socket, and when the model could not tell switches
from sockets (any ``unknown`` item, or a low confidence) the decision is "unsure" and guidance stops. Nothing
here speaks; the caller (``tools.light``) turns the decision into facts for Live.

Model IDs come only from ``aira_live.models`` (role ``point``: Robotics-ER 2 with the Flash fallback).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from aira_live.frames import Frame
from aira_live.vision.gemini import generate_json, image_part
from aira_live.vision.point import parse_box

ItemKind = Literal["switch", "socket", "unknown"]
DecisionKind = Literal["guide", "unsure", "none"]

# Below this detection confidence the board is not trusted at all (every item counts as unknown).
MIN_CONF = 0.5
# A switch whose centre lies inside a socket box grown by this many 0-1000 units is never a target: a fingertip
# guided there could land on the socket opening.
SOCKET_MARGIN = 40

SWITCH_PROMPT = (
    "This is the chest camera of a blind shopkeeper looking for the light switch board in a small shop. Find the "
    "switch board: a plate on the wall with light switches and sometimes power sockets. List every item on it "
    "separately, left to right: kind \"switch\" for a rocker, toggle or push light switch, kind \"socket\" for a "
    "power outlet (holes or pins for a plug), kind \"unknown\" when you cannot tell. Never call a socket a switch. "
    "Boxes are [ymin, xmin, ymax, xmax] normalized 0-1000. If no switch board is visible, return board null and "
    "an empty items list. confidence is how sure you are of the whole reading, 0 to 1."
)
SWITCH_SCHEMA = (
    '{"board": {"box": [ymin, xmin, ymax, xmax]} or null, "items": [{"kind": "switch|socket|unknown", '
    '"box": [ymin, xmin, ymax, xmax], "label": "<optional, e.g. fan>", "state": "on|off|unknown"}], '
    '"confidence": 0.0}'
)
_ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth")


@dataclass(frozen=True)
class SwitchItem:
    kind: ItemKind
    box: tuple[int, int, int, int]
    label: str = ""
    state: str = "unknown"

    @property
    def centre(self) -> tuple[int, int]:
        ymin, xmin, ymax, xmax = self.box
        return (round((ymin + ymax) / 2), round((xmin + xmax) / 2))


@dataclass(frozen=True)
class BoardDetection:
    board: tuple[int, int, int, int] | None
    items: tuple[SwitchItem, ...]
    confidence: float

    @property
    def switches(self) -> list[SwitchItem]:
        return sorted((i for i in self.items if i.kind == "switch"), key=lambda i: i.box[1])

    @property
    def sockets(self) -> list[SwitchItem]:
        return [i for i in self.items if i.kind == "socket"]


@dataclass(frozen=True)
class Decision:
    """What the fingertip may be guided to. ``guide``: ``point``/``box``/``name`` are set. ``unsure``: switches
    and sockets could not be told apart (or a switch sits by a socket): say "not sure" and STOP guiding.
    ``none``: no board or no switch to try (``reason`` says which)."""

    kind: DecisionKind
    reason: str
    point: tuple[int, int] | None = None
    box: tuple[int, int, int, int] | None = None
    name: str = ""
    index: int = 0
    total: int = 0
    sockets: int = 0
    extra: dict = field(default_factory=dict)


def _conf(v: Any) -> float:
    if v is None or isinstance(v, bool):
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    if f > 1.0:
        f = f / 100.0 if f <= 100.0 else 0.0
    return min(max(f, 0.0), 1.0)


def parse_detection(data: Any) -> BoardDetection:
    """One raw model reply -> a detection. A malformed item becomes ``unknown`` (never silently dropped: an
    item the model saw but could not describe must block guidance, not vanish). A malformed board box is None."""
    if not isinstance(data, dict):
        return BoardDetection(board=None, items=(), confidence=0.0)
    board_raw = data.get("board")
    board = parse_box(board_raw.get("box")) if isinstance(board_raw, dict) else parse_box(board_raw)
    items: list[SwitchItem] = []
    raw_items = data.get("items")
    for raw in (raw_items if isinstance(raw_items, list) else []):
        if not isinstance(raw, dict):
            continue
        box = parse_box(raw.get("box") if raw.get("box") is not None else raw.get("box_2d"))
        kind = str(raw.get("kind") or "").strip().lower()
        if box is None:
            if board is not None:
                items.append(SwitchItem(kind="unknown", box=board, label=str(raw.get("label") or "")))
            continue
        if kind not in ("switch", "socket"):
            kind = "unknown"
        state = str(raw.get("state") or "unknown").strip().lower()
        items.append(SwitchItem(kind=kind, box=box, label=str(raw.get("label") or "").strip(),  # type: ignore[arg-type]
                                state=state if state in ("on", "off") else "unknown"))
    return BoardDetection(board=board, items=tuple(items), confidence=_conf(data.get("confidence")))


async def detect_switchboard(frame: Frame) -> BoardDetection:
    """Ask the ``point`` model for the switch board's items. Raises VisionError when every attempt fails."""
    data = await generate_json("point", [image_part(frame.jpeg_b64), SWITCH_PROMPT], SWITCH_SCHEMA,
                               thinking="low", timeout_s=6.0)
    return parse_detection(data)


def _grown(box: tuple[int, int, int, int], margin: int) -> tuple[int, int, int, int]:
    ymin, xmin, ymax, xmax = box
    return (max(0, ymin - margin), max(0, xmin - margin), min(1000, ymax + margin), min(1000, xmax + margin))


def _inside(p: tuple[int, int], box: tuple[int, int, int, int]) -> bool:
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def ordinal_name(index: int, total: int) -> str:
    word = _ORDINALS[index] if index < len(_ORDINALS) else f"number {index + 1}"
    return f"the {word} switch from the left" + (f" of {total}" if total > 1 else "")


def choose_switch(det: BoardDetection, index: int = 0, label: str | None = None,
                  socket_margin: int = SOCKET_MARGIN) -> Decision:
    """Pure safety decision over structured detections.

    - No board or no items: ``none`` (``no_board``).
    - Confidence under ``MIN_CONF`` or any ``unknown`` item: ``unsure``. The model could not tell switches from
      sockets, so nothing is guided.
    - No switch (only sockets): ``none`` (``only_sockets``). ``index`` past the last switch: ``none`` (``exhausted``).
    - The chosen switch (by ``label`` when one is given and found, else the ``index``-th from the left) whose
      centre lies in a socket box grown by ``socket_margin``, or whose box overlaps a socket: ``unsure``
      (``near_socket``). A fingertip is never sent toward a socket opening.
    """
    if det.board is None and not det.items:
        return Decision("none", "no_board")
    if det.confidence < MIN_CONF:
        return Decision("unsure", "low_confidence")
    if any(i.kind == "unknown" for i in det.items):
        return Decision("unsure", "unknown_item")
    switches, sockets = det.switches, det.sockets
    if not switches:
        return Decision("none", "only_sockets" if sockets else "no_switch", sockets=len(sockets))
    chosen: SwitchItem | None = None
    pick = index
    if label:
        want = label.strip().casefold()
        for i, sw in enumerate(switches):
            if sw.label and (sw.label.casefold() == want or want in sw.label.casefold()):
                chosen, pick = sw, i
                break
    if chosen is None:
        if index >= len(switches):
            return Decision("none", "exhausted", index=index, total=len(switches), sockets=len(sockets))
        chosen = switches[index]
    point = chosen.centre
    for sock in sockets:
        if _inside(point, _grown(sock.box, socket_margin)) or _overlap(chosen.box, sock.box):
            return Decision("unsure", "near_socket", index=pick, total=len(switches), sockets=len(sockets))
    name = chosen.label if (label and chosen.label) else ordinal_name(pick, len(switches))
    return Decision("guide", "switch", point=point, box=chosen.box, name=name, index=pick, total=len(switches),
                    sockets=len(sockets), extra={"state": chosen.state})
