"""Step-by-step routes (vision/route.py): localisation (2 of 3, mirrors) and the route follower. Pure code."""
from pathlib import Path

import pytest

from aira_live import shop_map
from aira_live.vision import route as vroute
from aira_live.vision.route import ARRIVED, FACING, LOCALISE, RELOCALISE, TURN, WALK, Localizer, RouteGuide
from aira_live.vision.walk import ARRIVAL_KIND, parse_report

ROOT = Path(__file__).resolve().parents[3]
DEMO = shop_map.load_file(ROOT / "shared" / "catalog" / "demo_cricket_map.json")
ZONES = tuple(DEMO.zones)


def rep(zone="unknown", facing="unknown", confidence="high", reflection=False, cid=1, **kw):
    kw.setdefault("path", "clear_in_view")
    return parse_report("counter", check_id=cid, current_zone=zone, facing=facing, confidence=confidence,
                        reflection=reflection, zone_ids=ZONES, **kw)


# ---------- parsing the new fields ----------
def test_parse_report_validates_zone_facing_confidence_and_reflection():
    r = parse_report("counter", current_zone="Counter", facing="LEFT", confidence="High")
    assert r.zone == "counter" and r.facing == "left" and r.confidence == "high" and r.localised
    r = parse_report("counter", current_zone="roof", facing="up", confidence="sure", zone_ids=ZONES)
    assert r.zone == "unknown" and r.facing == "unknown" and r.confidence == "low" and not r.localised
    r = parse_report("counter", current_zone="counter", confidence="high", reflection=True)
    assert r.reflection and r.zone == "unknown" and not r.localised
    r = parse_report("counter")
    assert r.zone == "unknown" and r.facing == "unknown" and r.confidence == "low" and not r.reflection
    assert parse_report("counter", current_zone="bat wall", confidence="medium", zone_ids=ZONES).zone == "bat_wall"


# ---------- the localiser ----------
def test_zone_needs_the_same_zone_in_two_of_the_last_three_reports():
    loc = Localizer(DEMO)
    loc.observe(rep("bat_wall", "front"))
    assert loc.zone is None
    loc.observe(rep("ball_shelf", "front"))
    assert loc.zone is None
    loc.observe(rep("bat_wall", "front"))
    assert loc.zone == "bat_wall" and loc.facing_in("bat_wall") == "front"
    loc.observe(rep("kit_wall"))
    loc.observe(rep("kit_wall"))
    assert loc.zone == "kit_wall"


def test_unknown_low_confidence_and_reflections_are_never_evidence():
    loc = Localizer(DEMO)
    loc.observe(rep("bat_wall", confidence="low"))
    loc.observe(rep("bat_wall", confidence="low"))
    loc.observe(rep("bat_wall", confidence="low"))
    assert loc.zone is None
    loc.observe(rep("bat_wall", reflection=True))
    loc.observe(rep("bat_wall", reflection=True))
    assert loc.zone is None
    loc.observe(rep("unknown"))
    assert loc.zone is None


def test_a_zone_a_mirror_can_show_needs_high_confidence():
    """The tall mirror on the bat wall shows the counter: a medium-confidence "counter" is a reflection risk."""
    loc = Localizer(DEMO)
    loc.observe(rep("counter", confidence="medium"))
    loc.observe(rep("counter", confidence="medium"))
    loc.observe(rep("counter", confidence="medium"))
    assert loc.zone is None and DEMO.mirror_reflects("counter")
    loc.observe(rep("counter", confidence="high"))
    loc.observe(rep("counter", confidence="high"))
    assert loc.zone == "counter"
    loc2 = Localizer(DEMO)                    # the bat wall itself is not reflected: medium is enough
    loc2.observe(rep("bat_wall", confidence="medium"))
    loc2.observe(rep("bat_wall", confidence="medium"))
    assert loc2.zone == "bat_wall"


def test_facing_counts_only_within_one_zone():
    loc = Localizer(DEMO)
    loc.observe(rep("entrance", "left"))
    loc.observe(rep("counter", "left"))
    loc.observe(rep("entrance", "front"))
    assert loc.zone == "entrance" and loc.facing_in("entrance") is None
    loc.observe(rep("entrance", "front"))
    assert loc.facing_in("entrance") == "front"


def test_turned():
    assert vroute.turned("front", "left") == "left" and vroute.turned("left", "left") == "back"
    assert vroute.turned("front", "right") == "right" and vroute.turned("back", "right") == "left"
    assert vroute.turned("front", "around") == "back" and vroute.turned("unknown", "left") == "unknown"


# ---------- the follower ----------
def guide(target="counter", name=None) -> RouteGuide:
    return RouteGuide(DEMO, target, name=name)


def localise(g: RouteGuide, zone: str, facing: str = "front", n: int = 2) -> None:
    for _ in range(n):
        g.observe(rep(zone, facing))


def lines(g: RouteGuide, now: float) -> list[str]:
    """Every line due now (marking each as said), in order."""
    out = []
    while (ins := g.instruction(now)) is not None:
        g.mark(ins.key, now)
        out.append(ins.text)
        if ins.kind in (ARRIVAL_KIND, vroute.LOCALISE_KIND):
            break
    return out


def test_unknown_position_asks_him_to_turn_slowly_every_ten_seconds():
    g = guide()
    assert g.phase == LOCALISE
    assert lines(g, 0.0) == [vroute.LOCALISE_TEXT]
    assert lines(g, 5.0) == []
    assert lines(g, 10.0) == [vroute.LOCALISE_TEXT]
    g.observe(rep("entrance", "front", confidence="low"))      # still nothing to trust
    assert g.phase == LOCALISE


def test_walk_leg_counts_steps_says_two_more_then_arrival_candidate():
    g = guide()
    localise(g, "entrance", "front")
    assert g.phase == WALK and g.here == "entrance" and [l.type for l in g.legs] == ["walk", "checkpoint"]
    assert lines(g, 0.0) == ["Go ahead 8 steps."]
    assert lines(g, 1.0) == []                                   # said once
    g.on_steps(10)                                                # baseline: the phone's count when the leg began
    g.on_steps(13)
    assert lines(g, 2.0) == []
    g.on_steps(16)                                                # 6 done, 2 left
    assert lines(g, 3.0) == ["2 more steps."]
    g.on_steps(17)
    assert lines(g, 4.0) == ["1 more step."]
    g.on_steps(18)
    assert g.phase == ARRIVED
    arrival = g.instruction(5.0)
    assert arrival.kind == ARRIVAL_KIND and "appears to be just ahead" in arrival.text
    assert "reach forward carefully" in arrival.text.lower()


def test_first_walk_leg_needs_the_start_facing_confirmed():
    g = guide()
    localise(g, "entrance", "left")                               # the route assumes he faces front
    assert g.phase == FACING
    (text,) = lines(g, 0.0)
    assert text.startswith("First turn until you face this: the counter straight ahead")
    g.on_steps(5)                                                 # steps before the leg starts do not count
    localise(g, "entrance", "front")
    assert g.phase == WALK
    assert lines(g, 1.0) == ["Go ahead 8 steps."]
    g.on_steps(5)
    g.on_steps(13)
    assert g.phase == ARRIVED


def test_turn_leg_waits_for_the_new_facing_before_the_next_walk_leg():
    g = guide("bat_wall")                                          # entrance: walk 3, turn right, walk 4
    localise(g, "entrance", "front")
    assert lines(g, 0.0) == ["Go ahead 3 steps."]
    g.on_steps(0)
    g.on_steps(3)
    assert g.phase == TURN and g.expected_facing == "right"
    assert lines(g, 1.0) == ["Turn right. Stop when you face the new way."]
    g.on_steps(5)                                                 # steps during the turn: no progress
    assert g.phase == TURN and lines(g, 2.0) == []
    g.observe(rep("entrance", "right"))
    g.observe(rep("entrance", "right"))
    assert g.phase == WALK and lines(g, 3.0) == ["Go ahead 4 steps."]
    g.on_steps(6)                                                 # the new leg's baseline is the next count
    g.on_steps(10)
    assert g.phase == ARRIVED


def test_landmark_checkpoint_is_said_when_its_steps_are_done():
    g = guide("kit_wall")                                          # walk 5, landmark, turn left, walk 4, kit wall
    localise(g, "entrance", "front")
    assert lines(g, 0.0) == ["Go ahead 5 steps."]
    g.on_steps(0)
    g.on_steps(5)
    assert g.phase == TURN
    assert lines(g, 1.0) == ["The big table is behind you now.", "Turn left. Stop when you face the new way."]


def test_confirmed_checkpoint_snaps_progress():
    g = guide("ball_shelf")                                        # entrance -> counter -> ball shelf (chained)
    localise(g, "entrance", "front")
    assert lines(g, 0.0) == ["Go ahead 8 steps."]
    g.on_steps(0)
    g.on_steps(2)
    localise(g, "counter", "front")                                # he is at the counter already: snap
    assert g.here == "counter" and g.phase == TURN
    assert lines(g, 1.0) == ["The counter is in front of you.", "Turn right. Stop when you face the new way."]
    assert g.expected_facing == "right"                            # the counter route starts facing front
    g.observe(rep("counter", "right"))
    g.observe(rep("counter", "right"))
    assert g.phase == WALK and lines(g, 2.0) == ["Go ahead 3 steps."]


def test_mid_route_zone_checkpoint_waits_for_the_zone_and_overshoot_relocalises():
    g = guide("ball_shelf", name="Sam")
    localise(g, "entrance", "front")
    lines(g, 0.0)
    g.on_steps(0)
    g.on_steps(8)                                                 # steps done, counter not seen yet
    assert g.phase == WALK and lines(g, 1.0) == []
    g.on_steps(9)
    assert g.phase == WALK
    g.on_steps(10)                                                # expected + 2 without the counter
    assert g.phase == RELOCALISE
    (stop,) = lines(g, 2.0)
    assert stop == "Sam, stop. I expected the counter by now. Turn slowly until I recognise where you are."
    assert lines(g, 5.0) == []                                    # the reminder waits its 10 s
    localise(g, "bat_wall", "right")                              # found somewhere else: a new plan from there
    assert g.phase == WALK and g.here == "bat_wall" and g.gen == 2
    assert lines(g, 6.0) == ["Go ahead 5 steps."]


def test_arrival_said_from_steps_then_more_steps_without_the_zone_relocalise():
    g = guide()
    localise(g, "entrance", "front")
    lines(g, 0.0)
    g.on_steps(0)
    g.on_steps(8)
    assert g.phase == ARRIVED
    g.on_steps(9)
    assert g.phase == ARRIVED
    g.on_steps(10)
    assert g.phase == RELOCALISE


def test_arrival_by_zone_confirmation_even_before_the_steps():
    g = guide()
    localise(g, "entrance", "front")
    lines(g, 0.0)
    g.on_steps(0)
    g.on_steps(3)
    localise(g, "counter", "front")
    assert g.phase == ARRIVED
    g.on_steps(20)                                                # at the target: no overshoot
    assert g.phase == ARRIVED


def test_no_motion_falls_back_to_landmark_only_guidance_once():
    g = guide()
    assert g.no_motion().text == vroute.NO_MOTION_TEXT
    assert g.no_motion() is None
    localise(g, "entrance", "front")
    assert lines(g, 0.0) == ["Go ahead slowly toward the counter."]
    g.on_steps(50)                                                # a late count changes nothing
    assert g.phase == WALK
    localise(g, "counter", "front")
    assert g.phase == ARRIVED


def test_no_route_from_where_he_stands():
    g = guide("entrance")
    localise(g, "ball_shelf", "front")                             # nothing leaves the ball shelf
    (text,) = lines(g, 0.0)
    assert text.startswith("I have no route from the ball shelf to the entrance.")
    assert g.phase == LOCALISE
    localise(g, "counter", "back")
    assert g.phase == WALK and lines(g, 1.0) == ["Go ahead 8 steps."]


def test_somewhere_else_than_the_plan_replans():
    g = guide()
    localise(g, "entrance", "front")
    lines(g, 0.0)
    g.on_steps(0)
    localise(g, "kit_wall", "left")                                # not on the route: a new plan from the kit wall
    assert g.here == "kit_wall" and g.gen == 2 and g.phase == WALK
    assert lines(g, 1.0) == ["Go ahead 5 steps."]


def test_route_lines_carry_facts_with_a_meaning_and_steps_in_facts_only_when_counted():
    g = guide()
    localise(g, "entrance", "front")
    ins = g.instruction(0.0)
    assert ins.facts == {"action": "go", "steps": 8, "target": "counter"} and ins.kind == vroute.ROUTE_KIND
