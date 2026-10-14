"""Walk with me: the navigation policy over Live's nav reports (vision/walk.py). Pure code, no model."""
from aira_live.vision import walk as vwalk
from aira_live.vision.walk import NavReport, Obstacle, Search, Speech, decide, gate, parse_report


def rep(*obstacles, cid=1, visible=False, side="", size="", path="clear_in_view", view_ok=True) -> NavReport:
    return NavReport(cid, visible, side, size, tuple(obstacles), path, view_ok)


CHAIR = Obstacle("chair", "ahead", True, "medium")
CHAIR_LEFT = Obstacle("chair", "left", True, "medium")
BOX_RIGHT = Obstacle("box", "right", False, "small")


# ---------- parsing ----------
def test_parse_normalises_and_reads_none_as_not_visible():
    r = parse_report("fridge", check_id="7", target_visible=True, target_side="LEFT", target_size="none",
                     obstacles=[{"label": "  chair ", "side": "ahead", "in_path": True, "size": "large"},
                                {"label": "crate", "side": "sideways", "in_path": "maybe", "size": "huge"},
                                {"label": ""}, "junk", {"side": "left"}],
                     path="blocked", view_ok=True)
    assert r.check_id == 7 and r.target_visible and r.target_side == "left" and r.target_size == ""
    # an unknown side / in_path / size reads cautiously: ahead, in the path, medium
    assert r.obstacles == (Obstacle("chair", "ahead", True, "large"), Obstacle("crate", "ahead", True, "medium"))
    assert r.path == "blocked" and r.usable


def test_parse_defaults_are_cautious():
    r = parse_report("fridge", check_id="x", path="looks fine", view_ok="?")
    assert r.check_id == -1 and r.path == "uncertain" and not r.view_ok and not r.usable
    assert parse_report("door", target_visible=False, target_side="left").target_side == ""


def test_the_target_is_never_an_obstacle():
    r = parse_report("fridge", obstacles=[{"label": "refrigerator", "side": "ahead", "in_path": True},
                                          {"label": "the fridge door", "in_path": True},
                                          {"label": "chair", "in_path": True}], path="blocked")
    assert [o.label for o in r.obstacles] == ["chair"]
    assert vwalk.same_thing("Refrigerators", "fridge") and vwalk.same_thing("curd shelf", "shelf")
    assert not vwalk.same_thing("chair", "fridge") and not vwalk.same_thing("", "fridge")


# ---------- the instruction ----------
def test_hazard_in_path_stops_on_one_report():
    stairs = Obstacle("stairs", "ahead", True, "small")
    ins = decide(rep(stairs, path="blocked"), "fridge")
    assert ins.urgent and ins.kind == vwalk.HAZARD_KIND and ins.text == "Stop. Stairs ahead."
    assert ins.facts["hazard"] == "stairs"
    assert decide(rep(Obstacle("wet floor", "left", False)), "fridge").kind != vwalk.HAZARD_KIND  # not in path


def test_a_close_person_in_the_path_stops_at_once_by_name():
    ins = decide(rep(Obstacle("man", "ahead", True, "large")), "fridge", name="Sam")
    assert ins.urgent and ins.text == "Sam, stop. Someone is right in front of you." and ins.facts["address"] == "Sam"


def test_a_small_obstacle_needs_two_of_three_reports():
    first = rep(CHAIR)
    ins = decide(first, "fridge", [first])
    assert ins.kind == vwalk.HOLD_KIND and not ins.text and not ins.urgent        # nothing said, no direction
    second = rep(CHAIR, cid=2)
    ins = decide(second, "fridge", [first, second])
    assert ins.urgent and ins.kind == vwalk.OBSTACLE_KIND and "chair" in ins.text
    big = rep(Obstacle("table", "ahead", True, "large"))
    assert decide(big, "fridge", [big]).urgent                                    # a large one: one report


def test_direction_only_with_evidence():
    a, b = rep(CHAIR_LEFT, path="blocked"), rep(CHAIR_LEFT, cid=2, path="blocked")
    ins = decide(b, "fridge", [a, b])
    assert ins.text == ("Stop. There's a chair ahead. The right side looks clearer in the current view. "
                        "Step that way slowly.")
    assert ins.facts["clearer_side"] == "right" and ins.urgent


def test_no_invented_direction_without_evidence():
    # path not "blocked": open space is mentioned but the way is not confirmed
    a, b = rep(CHAIR_LEFT, path="clear_in_view"), rep(CHAIR_LEFT, cid=2, path="clear_in_view")
    ins = decide(b, "fridge", [a, b])
    assert "clearer" not in ins.text and "can't confirm the way is clear" in ins.text
    assert "more open space on your right" in ins.text and "Stay stopped" in ins.text
    # the other side has an obstacle too: no side at all
    a = rep(CHAIR_LEFT, Obstacle("box", "right", False), path="blocked")
    b = rep(CHAIR_LEFT, Obstacle("box", "right", False), cid=2, path="blocked")
    ins = decide(b, "fridge", [a, b])
    assert ins.text == "Stop. There's an obstacle ahead: a chair. I can't confirm a clear way."
    # an uncertain view never gives a side
    a, b = rep(CHAIR_LEFT, path="uncertain"), rep(CHAIR_LEFT, cid=2, path="uncertain")
    ins = decide(b, "fridge", [a, b])
    assert ins.urgent and "side" not in ins.text and "open space" not in ins.text


def test_unusable_view_is_degraded_never_a_direction():
    for r in (rep(visible=True, side="left", view_ok=False), rep(visible=True, side="left", path="uncertain")):
        ins = decide(r, "fridge")
        assert ins.kind == vwalk.DEGRADED_KIND and ins.urgent
        assert ins.text == "I can't see the path clearly right now. Please stop for a moment."


def test_arrival_is_only_a_candidate():
    ins = decide(rep(visible=True, side="ahead", size="large"), "fridge")
    assert ins.kind == vwalk.ARRIVAL_KIND and not ins.urgent
    assert ins.text == "The fridge appears to be just ahead. Reach forward carefully to confirm."
    assert ins.facts["arrived"] == "unconfirmed"
    assert "arrived" not in ins.text.lower() and "you are" not in ins.text.lower()


def test_target_sides_without_distances():
    assert decide(rep(visible=True, side="left", size="medium"), "fridge").text == \
        "The fridge is on your left. Turn left a little."
    assert decide(rep(visible=True, side="right", size="large"), "fridge").text == \
        "The fridge is on your right, and it looks close. Turn right a little."
    ahead = decide(rep(visible=True, side="ahead", size="small"), "fridge")
    assert ahead.key == "walk.ahead" and ahead.text == "The fridge is ahead. Keep going slowly."
    for r in (rep(visible=True, side="left"), rep(visible=True, side="ahead", size="medium")):
        text = decide(r, "fridge").text.lower()
        assert "step" not in text and "metre" not in text and "meter" not in text


def test_search_memory_once_then_looking_then_not_found():
    s = Search(memory_place="on your right, near the counter", memory_side="right")
    first = decide(rep(), "keys", search=s)
    assert first.key == "walk.memory" and "last seen on your right, near the counter" in first.text
    assert decide(rep(), "keys", search=s).key == "walk.looking_right"
    s.lost_s = vwalk.NOT_FOUND_S
    assert decide(rep(), "keys", search=s).key == "walk.not_found"


# ---------- speaking ----------
def test_urgent_lines_are_never_suppressed_but_get_shorter():
    sp = Speech()
    a, b = rep(CHAIR), rep(CHAIR, cid=2)
    ins = decide(b, "fridge", [a, b])
    assert sp.pick(ins, 0.0) == ins.text
    assert sp.pick(ins, 1.0) == "Stop. Chair ahead."            # again within 6 s: the short form, not silence
    assert sp.pick(ins, 8.0) == ins.text


def test_non_urgent_dedupe_gap_and_new_keys():
    sp = Speech()
    left = decide(rep(visible=True, side="left"), "fridge")
    right = decide(rep(visible=True, side="right"), "fridge")
    assert sp.pick(left, 0.0) == left.text
    assert sp.pick(left, 3.0) is None                            # same line within 6 s
    assert sp.pick(right, 1.0) is None                           # within the 2 s gap
    assert sp.pick(right, 2.5) == right.text                     # a new instruction goes after the gap
    assert sp.pick(right, 9.0, reassure=False) is None           # a repeated direction: only as reassurance
    assert sp.pick(right, 13.0, reassure=True) == "Keep going slowly."


def test_freshness_gate():
    stop = decide(rep(Obstacle("stairs", "ahead", True)), "fridge")
    turn = decide(rep(visible=True, side="left"), "fridge")
    assert gate(stop, 30.0) is stop and gate(stop, None) is stop
    assert gate(turn, 2.9) is turn and gate(turn, 3.1) is None and gate(turn, None) is None
    assert gate(turn, float("nan")) is None and gate(turn, -1) is None
    assert vwalk.WALK_DIRECTION_MAX_AGE_S == 3.0 and vwalk.WALK_MAX_AGE_S == 1.5


# ---------- the phone's instant stop ----------
def test_fast_stop_labels_are_coco_and_never_the_target():
    close = [500, 400, 950, 600]
    assert vwalk.fast_stop_label([{"label": "person", "box": close}]) == "person"
    for label in ("refrigerator", "box", "crate"):
        assert vwalk.fast_stop_label([{"label": label, "box": close}]) is None, label
    assert vwalk.fast_stop_label([{"label": "chair", "box": close}], target="fridge") == "chair"
    assert vwalk.fast_stop_label([{"label": "chair", "box": close}], target="chair") is None
    danger = {"label": "refrigerator", "box": close, "severity": "danger"}
    assert vwalk.fast_stop_label([danger]) == "refrigerator"
    assert vwalk.fast_stop_label([danger], target="fridge") is None       # never stop for the target itself
    assert vwalk.fast_stop_label([{"label": "cup", "box": [100, 0, 200, 100]}]) is None


def test_the_policy_module_calls_no_model():
    for name in ("look", "WALK_PROMPT", "WALK_JSON_SCHEMA", "gemini", "model_for", "parse_walk"):
        assert not hasattr(vwalk, name), name
