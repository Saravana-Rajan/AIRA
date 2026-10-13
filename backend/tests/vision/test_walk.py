"""Walk with me: the ER reply parser, the instruction builder and change-only speaking (vision/walk.py)."""
import pytest

from aira_live.vision import walk as w
from aira_live.vision.gemini import VisionError
from aira_live.vision.walk import Obstacle, Search, Speech, WalkView, decide, steps_for


def view(point=None, box=None, obstacles=(), traj=()):
    return WalkView(point, box, tuple(obstacles), tuple(traj))


# --- parse ----------------------------------------------------------------------------------------------------------

def test_parse_full_reply():
    v = w.parse_walk({"target_visible": True, "target": {"point": [400, 500], "box_2d": [300, 420, 500, 580]},
                      "obstacles": [{"label": "chair", "box_2d": [600, 400, 900, 600]}, {"label": "x"},
                                    {"label": "fridge again", "box_2d": [300, 420, 500, 580]}],
                      "trajectory": [[1000, 500], [800, 480], [500, 500], "bad"]})
    assert v.target_point == (400, 500) and v.target_box == (300, 420, 500, 580)
    assert [o.label for o in v.obstacles] == ["chair"]          # malformed and target-duplicate dropped
    assert v.trajectory == ((1000, 500), (800, 480), (500, 500))


def test_parse_not_visible_and_junk():
    v = w.parse_walk({"target_visible": False, "target": {"point": [1, 2]}, "obstacles": [], "trajectory": [[9, 9]]})
    assert not v.has_target and v.trajectory == ()
    assert w.parse_walk("nonsense") == WalkView()
    assert w.parse_walk({"target": {"point": [2000, 5]}}).target_point is None


# --- the instruction ------------------------------------------------------------------------------------------------

def test_obstacle_in_corridor_stops_and_picks_the_side_with_room():
    chair = Obstacle("chair", (600, 300, 900, 550))   # more room on its right
    ins = decide(view((300, 500), (250, 450, 350, 550), [chair]), "fridge", "en")
    assert ins.key == "walk.obstacle_right" and ins.urgent
    assert ins.text == "Stop. chair in front. Move a little right."
    chair2 = Obstacle("crate", (600, 450, 900, 750))
    assert decide(view(obstacles=[chair2]), "fridge", "en").key == "walk.obstacle_left"


def test_side_with_room_counts_other_obstacles():
    mid = Obstacle("chair", (600, 300, 900, 550))
    right = Obstacle("table", (500, 600, 900, 1000))       # the right side is blocked close by
    assert w.side_with_room(mid, (mid, right)) == "left"


def test_obstacle_outside_corridor_is_ignored():
    far = Obstacle("chair", (200, 450, 400, 550))          # ahead but far away
    side = Obstacle("crate", (700, 0, 1000, 200))          # close but to the side
    ins = decide(view((300, 500), (250, 450, 350, 550), [far, side]), "fridge", "en")
    assert ins.key == "walk.ahead"


def test_hazard_says_stop_first():
    step = Obstacle("step", (700, 300, 800, 700))
    ins = decide(view((300, 500), (100, 300, 950, 700), [step]), "fridge", "en")   # even when "arrived"
    assert ins.key == "walk.hazard" and ins.text.startswith("Stop.") and ins.urgent and not ins.arrived
    person = Obstacle("person", (560, 400, 1000, 600))
    assert decide(view(obstacles=[person]), "fridge", "en").key == "walk.hazard"


def test_target_left_right_ahead():
    assert decide(view((400, 200), (350, 150, 450, 250)), "fridge", "en").text == \
        "fridge on your left. Turn left a little."
    assert decide(view((400, 800), (350, 750, 450, 850)), "fridge", "en").key == "walk.right"
    ins = decide(view((400, 500), (300, 420, 500, 580)), "fridge", "en")
    assert ins.key == "walk.ahead" and ins.steps == 3 and ins.text == "fridge ahead, about 3 steps."


def test_steps_table_is_capped_and_unsure_without_box():
    assert steps_for((0, 0, 400, 10)) == 1
    assert steps_for((0, 0, 200, 10)) == 3
    assert steps_for((0, 0, 60, 10)) == 6
    assert steps_for((0, 0, 30, 10)) == w.MAX_STEPS == 8
    assert steps_for((0, 0, 10, 10)) is None
    assert steps_for(None) is None
    ins = decide(view((400, 500), None), "fridge", "en")
    assert ins.key == "walk.ahead_slow" and "Keep going slowly" in ins.text


def test_arrived_when_big_or_low_and_centred():
    ins = decide(view((500, 500), (200, 300, 700, 700)), "fridge", "en")
    assert ins.arrived and ins.text == "Stop. The fridge is right in front of you. Reach forward."
    assert decide(view((800, 520), (700, 400, 950, 650)), "fridge", "en").arrived      # bottom > 900
    assert not decide(view((500, 150), (200, 0, 700, 300)), "fridge", "en").arrived  # big but off to the side


def test_not_visible_uses_memory_once_then_looks():
    search = Search(memory_place="on your left, a few steps away", memory_side="left")
    first = decide(view(), "fridge", "en", search)
    assert first.key == "walk.memory"
    assert first.text == "The fridge was last seen on your left, a few steps away. Turn slowly left. I'm looking."
    again = decide(view(), "fridge", "en", search)
    assert again.key == "walk.looking_left"
    search.last_x = 800                                     # it was last seen on the right of the image
    assert decide(view(), "fridge", "en", search).key == "walk.looking_right"


def test_not_found_after_8_seconds():
    assert decide(view(), "fridge", "en", Search(lost_s=8.5)).key == "walk.not_found"
    assert decide(view(), "fridge", "en", Search(lost_s=3)).key == "walk.looking_left"


def test_lines_are_english_meanings_whatever_the_language():
    """The key and step count are the facts; the text is only the English meaning Live says in his language."""
    en = decide(view((400, 500), (300, 420, 500, 580)), "fridge", "en")
    ta = decide(view((400, 500), (300, 420, 500, 580)), "fridge", "ta")
    assert ta == en and en.key == "walk.ahead" and en.steps == 3 and en.text == "fridge ahead, about 3 steps."


# --- change-only speaking -------------------------------------------------------------------------------------------

def test_speech_change_only_and_reassurance_every_4s():
    sp = Speech("en")
    ahead = decide(view((400, 500), (300, 420, 500, 580)), "fridge", "en")
    assert sp.pick(ahead, 0.0) == ahead.text
    assert sp.pick(ahead, 1.0) is None
    assert sp.pick(ahead, 3.9) is None
    assert sp.pick(ahead, 4.1) == "Keep going."
    assert sp.pick(ahead, 5.0) is None
    left = decide(view((400, 200), (350, 150, 450, 250)), "fridge", "en")
    assert sp.pick(left, 5.5) == left.text                 # changed: at once


def test_speech_small_step_jitter_is_not_a_change_but_two_steps_are():
    sp = Speech("en")
    three = decide(view((400, 500), (400, 450, 580, 550)), "fridge", "en")    # h 0.18 -> 3
    four = decide(view((400, 500), (400, 450, 530, 550)), "fridge", "en")     # h 0.13 -> 4
    one = decide(view((400, 500), (300, 400, 660, 600)), "fridge", "en")      # h 0.36 -> 1
    assert (three.steps, four.steps, one.steps) == (3, 4, 1)
    assert sp.pick(three, 0) is not None
    assert sp.pick(four, 1) is None
    assert sp.pick(one, 2) == one.text


def test_obstacle_overrides_at_once_and_repeats_after_4s():
    sp = Speech("en")
    ahead = decide(view((400, 500), (300, 420, 500, 580)), "fridge", "en")
    stop = decide(view(obstacles=[Obstacle("chair", (600, 400, 900, 600))]), "fridge", "en")
    sp.pick(ahead, 0)
    assert sp.pick(stop, 0.5) == stop.text
    assert sp.pick(stop, 1.5) is None
    assert sp.pick(stop, 4.6) == stop.text                 # a safety line repeats itself, never "keep going"


# --- overlay and the phone's fast stop ------------------------------------------------------------------------------

def test_overlay_objects_target_obstacles_and_path():
    objs = w.overlay_objects(view((400, 500), (300, 420, 500, 580),
                                  [Obstacle("chair", (600, 400, 900, 600)), Obstacle("bag", (100, 0, 200, 100))],
                                  [(1000, 500), (700, 500)]), "fridge")
    assert objs[0]["label"] == "fridge" and objs[0]["box"] == [300, 420, 500, 580]
    assert objs[1]["severity"] == "danger" and "severity" not in objs[2]
    assert [o["label"] for o in objs[3:]] == ["path 1", "path 2"] and objs[3]["box"] is None


def test_fast_stop_label():
    assert w.fast_stop_label([{"label": "person", "box": [500, 400, 950, 600]}]) == "person"
    assert w.fast_stop_label([{"label": "person", "box": [100, 400, 300, 600]}]) is None     # far
    assert w.fast_stop_label([{"label": "cup", "box": [500, 400, 950, 600]}]) is None        # not a big thing
    assert w.fast_stop_label([{"label": "box", "box": [600, 400, 700, 600], "severity": "danger"}]) == "box"
    assert w.fast_stop_label([{"label": "x", "box": "bad"}]) is None


# --- the ER call ----------------------------------------------------------------------------------------------------

async def test_look_is_one_er_call_with_the_schema(fake_genai, frame):
    client = fake_genai('{"target_visible": true, "target": {"point": [400, 500], "box_2d": [300, 420, 500, 580]}, '
                        '"obstacles": [], "trajectory": [[1000, 500], [400, 500]]}')
    v = await w.look(frame, "curd shelf")
    assert v.has_target and v.trajectory == ((1000, 500), (400, 500))
    call = client.aio.models.calls[0]
    assert call["model"] == "gemini-robotics-er-2-preview" and "curd shelf" in call["contents"][1]
    assert call["config"].response_json_schema == w.WALK_JSON_SCHEMA


async def test_look_failure_is_a_vision_error_without_fallback(fake_genai, frame):
    client = fake_genai(RuntimeError("down"), '{"target_visible": false}')
    with pytest.raises(VisionError):
        await w.look(frame, "fridge")
    assert len(client.aio.models.calls) == 1        # no retry, no Flash: the tick is skipped
    fake_genai(0.2)
    with pytest.raises(VisionError):
        await w.look(frame, "fridge", timeout_s=0.05)
