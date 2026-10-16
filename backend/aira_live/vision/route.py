"""Step-by-step indoor routes (AIRA_ROUTES): localisation and the route follower, in code. No model call here.

Inputs are the shop map (``aira_live.shop_map``), Live's ``nav_report`` observations (with ``current_zone``,
``facing``, ``confidence`` and ``reflection``) and the phone's cumulative step count (client event ``steps``).

``Localizer``: a zone is trusted only when the same zone id appears in ``CONFIRM_OF`` of the last ``RECENT``
reports; a facing only among reports of one reference zone (facings are relative to a zone's views). A report
flagged as a reflection, a low-confidence one, an unknown zone, or a zone a mirror can show (``mirror_reflects``)
without high confidence, is never evidence: the tall mirror on the bat wall shows the counter, and a walk must
not believe it stands at the counter.

``RouteGuide`` walks the legs of a plan. A walk leg says "Go ahead N steps", then "2 more steps" near the end;
a turn leg says the turn and waits until the new facing is confirmed before the next walk leg; a zone checkpoint
snaps progress when its zone is confirmed; ``OVERSHOOT_STEPS`` past a walk leg's count without its checkpoint
stops him and asks him to turn slowly until he is recognised again (then a new plan from wherever he is). With
no step counting on the phone (``no_motion``) walk legs become "Go ahead slowly toward the <next zone>" and only
checkpoints advance the route; he hears that once. Arrival is the walk's usual candidate line: never certain.

Safety never lives here: the walk loop (``tools.walk``) puts every hazard, obstacle, unusable view and phone
fast stop before any route line.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from aira_live.shop_map import Leg, ShopMap
from aira_live.vision.walk import ARRIVAL_KIND, UNKNOWN, Instruction, NavReport, stop_word

ROUTE_KIND = "route"          # a route line: said once per leg (new key per leg and plan)
LOCALISE_KIND = "localise"    # "turn slowly until I recognise where you are": repeated every LOCALISE_REPEAT_S
CONFIRM_OF = 2                # the same zone / facing in this many of the last RECENT reports
RECENT = 3
OVERSHOOT_STEPS = 2           # steps past a walk leg's count without its checkpoint: stop and re-localise
NEAR_END_STEPS = 2            # "2 more steps" from this many left
LOCALISE_REPEAT_S = 10.0
LOCALISE_TEXT = "Turn slowly until I recognise where you are."
NO_MOTION_TEXT = "I can't count your steps on this phone. I'll guide you by landmarks only."

_LEFT = {"front": "left", "left": "back", "back": "right", "right": "front"}
_RIGHT = {v: k for k, v in _LEFT.items()}


def turned(facing: str, direction: str) -> str:
    """The facing after a turn: left / right a quarter turn, around a half. Unknown stays unknown."""
    if facing not in _LEFT:
        return UNKNOWN
    if direction == "left":
        return _LEFT[facing]
    if direction == "right":
        return _RIGHT[facing]
    return _LEFT[_LEFT[facing]]


# ---------- where he is ----------
@dataclass
class Localizer:
    """The trusted zone and facing from the last ``RECENT`` reports (see the module docstring)."""
    shop_map: ShopMap | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=RECENT))

    def observe(self, report: NavReport) -> None:
        if self.counts(report):
            self.recent.append((report.zone, report.facing))
        else:
            self.recent.append((UNKNOWN, UNKNOWN))

    def counts(self, report: NavReport) -> bool:
        """Whether this report is evidence of a position at all."""
        if not report.localised:
            return False
        if self.shop_map is not None:
            if report.zone not in self.shop_map.zones:
                return False
            if self.shop_map.mirror_reflects(report.zone) and report.confidence != "high":
                return False
        return True

    @property
    def zone(self) -> str | None:
        zones = [z for z, _ in self.recent if z != UNKNOWN]
        return next((z for z in zones if zones.count(z) >= CONFIRM_OF), None)

    def facing_in(self, zone: str) -> str | None:
        """The confirmed facing among the reports that placed him in ``zone``."""
        facings = [f for z, f in self.recent if z == zone and f != UNKNOWN]
        return next((f for f in facings if facings.count(f) >= CONFIRM_OF), None)

    def reset(self) -> None:
        self.recent.clear()


# ---------- the follower ----------
LOCALISE, FACING, WALK, TURN, RELOCALISE, ARRIVED = "LOCALISE", "FACING", "WALK", "TURN", "RELOCALISE", "ARRIVED"
MOVING_PHASES = (FACING, WALK, TURN)


@dataclass
class RouteGuide:
    """Follows a plan from the trusted zone to ``target_zone``. ``observe`` (a nav report), ``on_steps`` (the
    phone's cumulative count) and ``no_motion`` feed it; ``instruction(now)`` is the line due now (or None) and
    ``mark`` records that it was said. Keys are stable per plan and leg so the walk's Speech pacing dedupes
    them; an event line (steps, checkpoint, overshoot) is queued in ``pending`` and said next."""
    shop_map: ShopMap
    target_zone: str
    name: str | None = None
    localizer: Localizer = field(init=False)
    phase: str = LOCALISE
    legs: tuple[Leg, ...] = ()
    leg: int = 0
    gen: int = 0                    # plans made (keys change with it)
    here: str | None = None         # the reference zone: where the plan started or the last checkpoint passed
    expected_facing: str = UNKNOWN  # in ``here``'s views
    total: int | None = None        # the phone's latest cumulative count (None: no steps event yet)
    leg_start: int | None = None    # the count when the current walk leg began
    motion: bool | None = None      # None: unknown yet; False: the phone cannot count steps
    said: dict[str, float] = field(default_factory=dict)
    pending: list[Instruction] = field(default_factory=list)
    no_motion_said: bool = False

    def __post_init__(self) -> None:
        self.localizer = Localizer(self.shop_map)

    # ----- observations -----
    def observe(self, report: NavReport) -> None:
        self.localizer.observe(report)
        zone = self.localizer.zone
        if self.phase in (LOCALISE, RELOCALISE):
            if zone is not None:
                self._plan(zone)
            return
        if self.phase == ARRIVED:
            return
        if zone is not None and zone != self.here:
            if self._snap(zone):
                return
            self._plan(zone)        # somewhere else than the plan expects: a new plan from there
            return
        if self.here and self.phase in (FACING, TURN) and self.localizer.facing_in(self.here) == self.expected_facing:
            if self.phase == TURN:
                self.leg += 1
            self._enter_leg()

    def on_steps(self, total: int) -> None:
        if self.motion is None:
            self.motion = True
        self.total = total
        leg = self.active_leg
        if leg is None or leg.type != "walk" or not self.motion:
            return
        if self.phase == ARRIVED:
            # arrival said from the last leg's count, and he keeps walking without the zone showing up
            if self.leg_start is not None and self.localizer.zone != self.target_zone \
                    and total - self.leg_start >= leg.steps + OVERSHOOT_STEPS:
                self._overshoot()
            return
        if self.phase != WALK:
            return
        if self.leg_start is None:
            self.leg_start = total          # the first count since the leg began is its baseline
        done = max(0, total - self.leg_start)
        left = leg.steps - done
        if left > 0:
            if left <= NEAR_END_STEPS:
                self._queue(Instruction(f"route.more:{self.gen}:{self.leg}:{left}",
                                        f"{left} more step{'s' if left > 1 else ''}.", ROUTE_KIND,
                                        facts={"action": "go", "steps_left": left}))
            return
        self._leg_done()
        if self.phase == WALK and done >= leg.steps + OVERSHOOT_STEPS:
            self._overshoot()

    def no_motion(self) -> Instruction | None:
        """The phone cannot count steps: landmark-only guidance from now on; the line is given once."""
        self.motion = False
        if self.no_motion_said:
            return None
        self.no_motion_said = True
        return Instruction("route.no_motion", NO_MOTION_TEXT, ROUTE_KIND,
                           facts={"action": "info", "steps": "unavailable", "guidance": "landmarks_only"})

    # ----- the line due now -----
    def instruction(self, now: float) -> Instruction | None:
        if self.pending:
            return self.pending.pop(0)
        if self.phase in (LOCALISE, RELOCALISE):
            key = "route.localise" if self.phase == LOCALISE else "route.relocalise"
            if now - self.said.get(key, float("-inf")) < LOCALISE_REPEAT_S:
                return None
            return Instruction(key, LOCALISE_TEXT, LOCALISE_KIND,
                               facts={"action": "turn_slowly", "reason": "position_unknown", "target": self.target})
        if self.phase == ARRIVED:
            return Instruction("route.arrival",
                               f"The {self.target} appears to be just ahead. Reach forward carefully to confirm.",
                               ARRIVAL_KIND, facts={"action": "reach_check", "target": self.target,
                                                    "direction": "ahead", "arrived": "unconfirmed"})
        key = f"route.{self.phase.lower()}:{self.gen}:{self.leg}"
        if key in self.said:
            return None
        if self.phase == FACING:
            zone = self.shop_map.zones.get(self.here or "")
            view = (zone.views.get(self.expected_facing) if zone else None) or self.expected_facing
            return Instruction(key, f"First turn until you face this: {view}.", ROUTE_KIND,
                               facts={"action": "turn_to_face", "view": view, "target": self.target})
        if self.phase == TURN:
            d = self.legs[self.leg].dir
            text = "Turn around." if d == "around" else f"Turn {d}."
            return Instruction(key, f"{text} Stop when you face the new way.", ROUTE_KIND,
                               facts={"action": "turn", "direction": d, "target": self.target})
        leg = self.legs[self.leg]       # WALK
        if self.motion is False or leg.type != "walk":
            nxt = self._next_checkpoint() if leg.type == "walk" else leg
            what = f"the {self.shop_map.zones[nxt.zone].name}" if nxt and nxt.zone else "the next landmark"
            return Instruction(key, f"Go ahead slowly toward {what}.", ROUTE_KIND,
                               facts={"action": "go", "slowly": True, "toward": what, "target": self.target})
        return Instruction(key, f"Go ahead {leg.steps} steps.", ROUTE_KIND,
                           facts={"action": "go", "steps": leg.steps, "target": self.target})

    def mark(self, key: str, now: float) -> None:
        self.said[key] = now
        if key.startswith("route.overshoot"):     # it ends with the "turn slowly" line: the reminder waits
            self.said["route.relocalise"] = now
        elif key.startswith("route.no_route"):
            self.said["route.localise"] = now

    @property
    def target(self) -> str:
        return self.shop_map.zones[self.target_zone].name

    @property
    def active_leg(self) -> Leg | None:
        return self.legs[self.leg] if self.leg < len(self.legs) else None

    # ----- internals -----
    def _queue(self, ins: Instruction) -> None:
        if not any(p.key == ins.key for p in self.pending):
            self.pending.append(ins)

    def _plan(self, zone: str) -> None:
        legs = self.shop_map.plan(zone, self.target_zone)
        if legs is None:
            key = f"route.no_route:{zone}"
            if key not in self.said:
                self._queue(Instruction(key, f"I have no route from the {self.shop_map.zones[zone].name} to the "
                                        f"{self.target}. {LOCALISE_TEXT}", LOCALISE_KIND,
                                        facts={"action": "turn_slowly", "reason": "no_route", "from": zone,
                                               "target": self.target}))
            if self.phase not in (LOCALISE, RELOCALISE):
                self.phase = RELOCALISE
                self.localizer.reset()
            return
        self.gen += 1
        self.here, self.legs, self.leg = zone, legs, 0
        self.expected_facing = self.shop_map.start_facing(zone, self.target_zone)
        self.leg_start = None
        self.pending.clear()
        if self.active_leg is not None and self.active_leg.type == "walk" and self.expected_facing != UNKNOWN \
                and self.localizer.facing_in(zone) != self.expected_facing:
            self.phase = FACING
            return
        self._enter_leg()

    def _enter_leg(self) -> None:
        leg = self.active_leg
        if leg is None:
            self.phase = ARRIVED
            return
        if leg.type == "checkpoint":
            if self.leg == len(self.legs) - 1:
                # the arrival checkpoint, reached by the steps of the last walk leg or by its zone being confirmed
                self.phase = ARRIVED
                return
            if leg.zone and self.localizer.zone != leg.zone:
                self.phase = WALK            # wait here for the zone (a snap moves on; no steps count here)
                return
            self._queue(Instruction(f"route.checkpoint:{self.gen}:{self.leg}", leg.say, ROUTE_KIND,
                                    facts={"action": "checkpoint" if leg.zone else "landmark",
                                           "zone": leg.zone, "landmark": leg.landmark}))
            if leg.zone:
                self.here = leg.zone
                self.expected_facing = self._facing_at_checkpoint(self.leg)
            self.leg += 1
            self.leg_start = None
            self._enter_leg()
            return
        if leg.type == "turn":
            self.expected_facing = turned(self.expected_facing, leg.dir)
            self.phase = TURN
            return
        self.phase = WALK
        self.leg_start = self.total

    def _leg_done(self) -> None:
        """The walk leg's steps are done. A landmark checkpoint or a turn next: on to it. A zone checkpoint next:
        the arrival (candidate line) or, mid-route, wait for that zone to be confirmed."""
        nxt = self.legs[self.leg + 1] if self.leg + 1 < len(self.legs) else None
        if nxt is None or self.leg + 1 == len(self.legs) - 1:
            self.phase = ARRIVED        # the leg index and baseline stay: more steps without the zone = overshoot
            return
        if nxt.type == "checkpoint" and nxt.zone and self.localizer.zone != nxt.zone:
            return
        self.leg += 1
        self.leg_start = None
        self._enter_leg()

    def _snap(self, zone: str) -> bool:
        """A confirmed zone that is a checkpoint still ahead: progress jumps to it. False when it is not."""
        for i in range(self.leg, len(self.legs)):
            leg = self.legs[i]
            if leg.type == "checkpoint" and leg.zone == zone:
                self.leg, self.leg_start = i, None
                self._enter_leg()           # passes the checkpoint (its line queued) or arrives
                return True
        return False

    def _facing_at_checkpoint(self, i: int) -> str:
        """The facing the legs after a mid-route checkpoint assume: a chained route starts there with its own
        ``start_facing``; otherwise the facing carries on in the new zone's views as "front"."""
        zone = self.legs[i].zone or ""
        rest = self.legs[i + 1:]
        for r in self.shop_map.routes:
            if r.from_zone == zone and rest[:len(r.legs)] == r.legs:
                return r.start_facing
        return "front"

    def _overshoot(self) -> None:
        nxt = self._next_checkpoint()
        what = f"the {self.shop_map.zones[nxt.zone].name}" if nxt and nxt.zone else "the next landmark"
        self.phase = RELOCALISE
        self.localizer.reset()
        self.leg_start = None
        self.pending.clear()
        self._queue(Instruction(f"route.overshoot:{self.gen}:{self.leg}",
                                f"{stop_word(self.name)} I expected {what} by now. {LOCALISE_TEXT}",
                                LOCALISE_KIND, True,
                                facts={"action": "stop", "reason": "checkpoint_missed", "expected": what,
                                       "address": self.name},
                                short=f"Stop. {LOCALISE_TEXT}"))

    def _next_checkpoint(self) -> Leg | None:
        return next((l for l in self.legs[self.leg + 1:] if l.type == "checkpoint"), None)
