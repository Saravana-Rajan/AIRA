"""Switch board: structured detections in, a pure safety decision out (vision/switches.py). No network."""
import json

import pytest

from aira_live.vision import switches as sw
from aira_live.vision.switches import BoardDetection, SwitchItem, choose_switch, parse_detection

BOARD = (300, 400, 500, 700)
SW1 = SwitchItem("switch", (320, 420, 380, 480))
SW2 = SwitchItem("switch", (320, 500, 380, 560))
SW3 = SwitchItem("switch", (320, 580, 380, 640), label="fan")
SOCKET_FAR = SwitchItem("socket", (420, 420, 490, 520))     # well below the switches
SOCKET_NEXT = SwitchItem("socket", (320, 560, 380, 620))    # touching SW2's right edge


def det(*items, conf=0.9, board=BOARD) -> BoardDetection:
    return BoardDetection(board=board, items=tuple(items), confidence=conf)


# ---------- parsing: the model's reply becomes structured data, nothing is guessed ----------
def test_parse_detection_keeps_kinds_boxes_labels_and_confidence():
    d = parse_detection({"board": {"box": [300, 400, 500, 700]}, "confidence": 0.8, "items": [
        {"kind": "switch", "box": [320, 420, 380, 480], "state": "off"},
        {"kind": "SOCKET", "box_2d": [420, 420, 490, 520], "label": "plug"},
    ]})
    assert d.board == BOARD and d.confidence == 0.8
    assert [i.kind for i in d.items] == ["switch", "socket"]
    assert d.items[0].state == "off" and d.items[1].label == "plug" and d.items[1].state == "unknown"
    assert d.switches == [d.items[0]] and d.sockets == [d.items[1]]


def test_parse_detection_turns_malformed_items_into_unknown_never_drops_them_silently():
    d = parse_detection({"board": [300, 400, 500, 700], "confidence": "90", "items": [
        {"kind": "lever", "box": [320, 420, 380, 480]},     # an unknown kind
        {"kind": "switch", "box": [1, 2, 3]},               # a bad box: becomes unknown over the board
        "garbage",
    ]})
    assert d.confidence == 0.9
    assert [i.kind for i in d.items] == ["unknown", "unknown"]
    assert d.items[1].box == BOARD
    assert parse_detection(None).items == () and parse_detection([]).confidence == 0.0
    assert parse_detection({"board": None, "items": [], "confidence": 1.2}).board is None


# ---------- the decision ----------
def test_guides_to_the_first_switch_from_the_left_with_its_ordinal_name():
    d = choose_switch(det(SW2, SW1, SW3))
    assert d.kind == "guide" and d.point == SW1.centre and d.box == SW1.box
    assert d.name == "the first switch from the left of 3" and d.index == 0 and d.total == 3
    second = choose_switch(det(SW1, SW2, SW3), index=1)
    assert second.point == SW2.centre and second.name == "the second switch from the left of 3"
    assert choose_switch(det(SW1)).name == "the first switch from the left"


def test_a_map_label_picks_that_switch_and_keeps_its_name():
    d = choose_switch(det(SW1, SW2, SW3), label="Fan")
    assert d.kind == "guide" and d.point == SW3.centre and d.name == "fan" and d.index == 2
    # an unknown label falls back to the index
    assert choose_switch(det(SW1, SW2, SW3), index=1, label="tube").point == SW2.centre


def test_never_toward_a_socket_a_switch_next_to_a_socket_is_unsure():
    # a socket far below the switches does not block them
    ok = choose_switch(det(SW1, SW2, SOCKET_FAR))
    assert ok.kind == "guide" and ok.sockets == 1
    # the second switch touches a socket: unsure, no target (the first one is still fine)
    assert choose_switch(det(SW1, SW2, SOCKET_NEXT)).kind == "guide"
    bad = choose_switch(det(SW1, SW2, SOCKET_NEXT), index=1)
    assert bad.kind == "unsure" and bad.reason == "near_socket" and bad.point is None
    # a switch whose centre lies inside the grown socket box (within SOCKET_MARGIN) is unsure too
    near = SwitchItem("switch", (320, 420, 380, 480))
    sock = SwitchItem("socket", (390, 400, 460, 500))   # 10 units below the switch's box
    assert choose_switch(det(near, sock)).kind == "unsure"
    assert choose_switch(det(near, sock), socket_margin=0).kind == "guide"


def test_unsure_when_the_model_could_not_tell_switches_from_sockets():
    unknown = SwitchItem("unknown", (320, 500, 380, 560))
    d = choose_switch(det(SW1, unknown))
    assert d.kind == "unsure" and d.reason == "unknown_item"
    low = choose_switch(det(SW1, conf=0.4))
    assert low.kind == "unsure" and low.reason == "low_confidence"
    assert sw.MIN_CONF == 0.5


def test_none_when_there_is_no_board_no_switch_or_no_switch_left_to_try():
    assert choose_switch(BoardDetection(None, (), 0.9)).reason == "no_board"
    only = choose_switch(det(SOCKET_FAR))
    assert only.kind == "none" and only.reason == "only_sockets" and only.sockets == 1
    assert choose_switch(det(conf=0.9)).reason == "no_switch"
    gone = choose_switch(det(SW1, SW2), index=2)
    assert gone.kind == "none" and gone.reason == "exhausted" and gone.total == 2


# ---------- the model call: the point role, structured JSON only ----------
async def test_detect_switchboard_parses_the_point_models_json(fake_genai, frame):
    client = fake_genai(json.dumps({"board": {"box": [300, 400, 500, 700]}, "confidence": 0.85, "items": [
        {"kind": "switch", "box": [320, 420, 380, 480]}, {"kind": "socket", "box": [420, 420, 490, 520]}]}))
    d = await sw.detect_switchboard(frame)
    assert d.confidence == 0.85 and len(d.switches) == 1 and len(d.sockets) == 1
    call = client.aio.models.calls[0]
    assert call["model"] == "gemini-robotics-er-2-preview"
    text = " ".join(str(p) for p in call["contents"])
    assert "Never call a socket a switch" in text and "unknown" in text


async def test_a_dark_frame_the_model_is_unsure_about_never_guides(fake_genai, frame):
    fake_genai(json.dumps({"board": {"box": [300, 400, 500, 700]}, "confidence": 0.2,
                           "items": [{"kind": "switch", "box": [320, 420, 380, 480]}]}))
    d = await sw.detect_switchboard(frame)
    assert choose_switch(d).kind == "unsure"


async def test_every_attempt_failing_raises_vision_error(fake_genai, frame):
    from aira_live.vision.gemini import VisionError

    fake_genai(RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom"))
    with pytest.raises(VisionError):
        await sw.detect_switchboard(frame)
