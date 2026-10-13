"""camera_ask: open camera questions answered as facts for a blind owner, never as a generic description.

The Live model decides what is asked (``kind``); the code routes on it and never parses the owner's words.
Results are English facts (``facts``, ``say_en``); Live says them in the owner's language."""
import pytest

from aira_live import prompt
from aira_live.frames import Frame
from aira_live.session import current_session
from aira_live.tools import TOOLS_BY_NAME, camera

TINY = "/9j/4AAQSkZJRg=="


def _frame(ts: float = 1.0) -> Frame:
    return Frame(id=f"f{ts}", jpeg_b64=TINY, w=640, h=480, ts=ts, purpose="ask")


@pytest.fixture
def session(mk_session, monkeypatch):
    s, rec = mk_session(lang="en")
    calls: list[dict] = []

    async def fake_frames(purpose, count=1, interval_ms=0, timeout_s=4.0):
        calls.append({"purpose": purpose, "count": count})
        return [_frame(float(i + 1)) for i in range(count)]

    monkeypatch.setattr(s, "request_frames", fake_frames)
    token = current_session.set(s)
    s.frame_calls = calls
    yield s, rec
    current_session.reset(token)


def _script(monkeypatch, *replies):
    """generate_json returns the given dicts in order; records every call (role, parts, schema)."""
    seen: list[dict] = []
    items = list(replies)

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0, **_kw):
        seen.append({"role": role, "parts": parts, "schema": schema_hint})
        item = items.pop(0)
        if isinstance(item, Exception):
            raise item
        return dict(item)

    monkeypatch.setattr(camera, "generate_json", fake)
    return seen


# --- registry and prompt -------------------------------------------------------------------------------------


def test_registered_as_a_live_tool():
    assert TOOLS_BY_NAME["camera_ask"] is camera.camera_ask
    assert "question" in camera.camera_ask.__doc__ or "camera" in camera.camera_ask.__doc__.lower()


def test_prompt_rule_sends_vision_questions_to_camera_ask():
    text = prompt.render_prompt({"name": "Sunrise Dairy"}, [], [], "en", "short")
    assert "camera_ask" in text
    assert "never answer from imagination" in text.lower() and "live video" in text
    assert "product_find" in text


# --- guidance phrasing from points ---------------------------------------------------------------------------


async def test_point_on_the_left_and_near_is_spoken_as_guidance(session, monkeypatch):
    s, _ = session
    seen = _script(monkeypatch, {
        "answer": "A milk crate.", "guidance": ["Reach down with your left hand."],
        "objects": [{"label": "milk crate", "point": [700, 150], "distance": "near"}],
        "confidence": 0.9,
    })
    out = await camera.camera_ask("what's in front of me?", kind="look")
    assert s.frame_calls == [{"purpose": "ask", "count": 1}]
    assert seen[0]["role"] == "point"
    assert out["confidence"] == 0.9 and out["objects"][0]["point"] == [700, 150]
    assert "left" in out["facts"] and "arm" in out["facts"]
    assert "Reach down with your left hand." in out["facts"]
    assert out["facts"].count(".") <= 3
    assert "safe" not in out["facts"].lower()
    assert out["seen"] == [{"label": "milk crate", "side": "far_left", "distance": "near"}]
    assert out["sure"] is True and out["say_en"] == out["facts"] and "say" not in out


async def test_point_on_the_right_and_far(session, monkeypatch):
    _script(monkeypatch, {
        "answer": "The door.", "guidance": [],
        "objects": [{"label": "door", "point": [500, 900], "distance": "far"}], "confidence": 0.8,
    })
    out = await camera.camera_ask("where is the door?")
    assert "right" in out["facts"] and "steps" in out["facts"]


async def test_point_in_the_centre_uses_centre_word(session, monkeypatch):
    _script(monkeypatch, {
        "answer": "", "guidance": [],
        "objects": [{"label": "curd shelf", "point": [500, 500], "distance": "arm"}], "confidence": 0.8,
    })
    out = await camera.camera_ask("which shelf has curd?")
    assert "ahead" in out["facts"] and "curd shelf" in out["facts"]


# --- uncertainty -----------------------------------------------------------------------------------------------


async def test_low_confidence_says_not_sure_and_gives_the_aim_hint(session, monkeypatch):
    _script(monkeypatch, {"answer": "Maybe a shelf.", "guidance": ["Go forward."], "objects": [],
                          "confidence": 0.2, "aim_hint": "vision.aim.hold_closer"})
    out = await camera.camera_ask("what's this?")
    assert out["sure"] is False and out["hint"] == "hold_closer" and out["reason"] == "low_confidence"
    assert out["say_en"] == "Not sure. Hold it closer to the camera."
    assert "Maybe" not in out["facts"] and out["confidence"] == 0.2


async def test_a_confident_answer_with_a_tip_is_still_spoken(session, monkeypatch):
    """Seen live on a laptop: confidence 0.9, an answer, and a "show me the items" tip. The answer was thrown away."""
    _script(monkeypatch, {"answer": "A person is in front of you.", "guidance": ["Hold up the item you want me to see."],
                          "objects": [], "confidence": 0.9, "aim_hint": "vision.aim.show_items"})
    out = await camera.camera_ask("what is in front of the camera?")
    assert out["facts"] == "A person is in front of you. Hold up the item you want me to see."


async def test_a_confident_reply_with_nothing_to_say_is_still_not_sure(session, monkeypatch):
    _script(monkeypatch, {"answer": "", "guidance": [], "objects": [], "confidence": 0.9,
                          "aim_hint": "vision.aim.hold_closer"})
    out = await camera.camera_ask("what's this?")
    assert out["sure"] is False and out["hint"] == "hold_closer"


async def test_unknown_aim_hint_falls_back_to_show_items(session, monkeypatch):
    _script(monkeypatch, {"answer": "", "guidance": [], "objects": [], "confidence": 0.1, "aim_hint": "bogus"})
    out = await camera.camera_ask("what's around?")
    assert out["hint"] == "show_items" and out["aim_hint"] == "vision.aim.show_items"


async def test_no_frame_says_camera_none(session, monkeypatch):
    s, _ = session

    async def none(*a, **k):
        return []

    monkeypatch.setattr(s, "request_frames", none)
    out = await camera.camera_ask("what's in front?")
    assert out["sure"] is False and out["reason"] == "no_camera_frame" and out["confidence"] == 0.0


async def test_vision_failure_is_spoken_not_raised(session, monkeypatch):
    from aira_live.vision.gemini import VisionError
    _script(monkeypatch, VisionError("down"))
    out = await camera.camera_ask("what's in front?")
    assert out["sure"] is False and out["reason"] == "vision_unavailable"


# --- reading text ----------------------------------------------------------------------------------------------


async def test_read_text_twice_and_return_verbatim_when_they_agree(session, monkeypatch):
    seen = _script(monkeypatch,
                   {"text": "Aavin Milk 500 ml  MRP 28", "confidence": 0.9},
                   {"text": "AAVIN MILK 500 ML MRP 28", "confidence": 0.8})
    out = await camera.camera_ask("read this label", kind="read")
    assert [c["role"] for c in seen] == ["read", "verify"]
    assert out["text"] == "Aavin Milk 500 ml  MRP 28" and out["verbatim"] is True
    assert out["answer"] == "Aavin Milk 500 ml  MRP 28" and out["kind"] == "read"


async def test_read_text_disagreement_says_not_sure(session, monkeypatch):
    _script(monkeypatch, {"text": "MRP 28", "confidence": 0.9}, {"text": "MRP 26", "confidence": 0.9})
    out = await camera.camera_ask("what does this say?", kind="read")
    assert out["sure"] is False and out["reason"] == "reads_disagree" and out["hint"] == "show_label"
    assert "28" not in str(out) and "26" not in str(out)


# --- safety: path questions ------------------------------------------------------------------------------------


async def test_path_question_names_the_obstacle_and_its_direction_never_safe(session, monkeypatch):
    s, _ = session
    _script(monkeypatch, {
        "answer": "Yes, your path is safe and clear.", "guidance": ["Walk ahead."],
        "objects": [{"label": "crate", "point": [800, 200], "distance": "near"}], "confidence": 0.9,
    })
    out = await camera.camera_ask("is my path clear?", kind="path")
    assert s.frame_calls[0]["count"] >= 2                      # a short burst: motion matters on a path
    assert "crate" in out["facts"] and "left" in out["facts"]
    assert out["obstacle"] == {"label": "crate", "side": "left", "distance": "near"}
    assert "safe" not in str(out).lower()
    assert out["kind"] == "path"


async def test_path_question_with_nothing_seen_still_never_says_safe(session, monkeypatch):
    _script(monkeypatch, {"answer": "All clear, safe to go.", "guidance": [], "objects": [], "confidence": 0.85})
    out = await camera.camera_ask("can I walk forward?", kind="path")
    assert "safe" not in str(out).lower() and "clear" not in str(out).lower()
    assert out["obstacle"] is None and out["facts"].startswith("Nothing seen in the way")


# --- language and routing --------------------------------------------------------------------------------------


async def test_facts_are_english_whatever_the_session_language(mk_session, monkeypatch):
    """Live says the facts in Sam's language: the vision prompt asks for plain English, never a fixed sentence."""
    s, _ = mk_session(lang="ta")

    async def fake_frames(purpose, count=1, interval_ms=0, timeout_s=4.0):
        return [_frame()]

    monkeypatch.setattr(s, "request_frames", fake_frames)
    token = current_session.set(s)
    try:
        seen = _script(monkeypatch, {"answer": "", "guidance": [], "objects": [], "confidence": 0.1})
        out = await camera.camera_ask("என் முன்னால் என்ன இருக்கு?", kind="look")
        assert out["sure"] is False and out["say_en"].startswith("Not sure.")
        prompt_text = " ".join(p for p in seen[0]["parts"] if isinstance(p, str))
        assert "plain English" in prompt_text and "Tamil" not in prompt_text
    finally:
        current_session.reset(token)


@pytest.mark.parametrize("question", ["read this label", "how many packets?", "is my path clear?",
                                      "what's in my fridge?", "எத்தனை பாக்கெட்?"])
async def test_routing_follows_kind_never_the_words(session, monkeypatch, question):
    """Without a kind (or with an unknown one) every question is a plain look, whatever its words say."""
    seen = _script(monkeypatch, {"answer": "A shelf.", "guidance": [], "objects": [], "confidence": 0.9},
                   {"answer": "A shelf.", "guidance": [], "objects": [], "confidence": 0.9})
    out = await camera.camera_ask(question)
    assert out["kind"] == "look" and [c["role"] for c in seen] == ["point"]
    out = await camera.camera_ask(question, kind="bogus")
    assert out["kind"] == "look"


async def test_kind_read_reads_even_for_a_question_without_read_words(session, monkeypatch):
    seen = _script(monkeypatch, {"text": "MRP 28", "confidence": 0.9}, {"text": "MRP 28", "confidence": 0.9})
    out = await camera.camera_ask("இது என்ன?", kind="read")
    assert [c["role"] for c in seen] == ["read", "verify"] and out["text"] == "MRP 28"


def test_no_word_list_classifier_is_left():
    for name in ("classify", "READ_WORDS", "PATH_WORDS", "COUNT_WORDS", "COUNT_EN"):
        assert not hasattr(camera, name), name


def test_kind_is_an_enum_for_live():
    import inspect
    import typing

    sig = inspect.signature(camera.camera_ask)
    assert list(sig.parameters) == ["question", "kind", "item", "product"]
    kind = typing.get_type_hints(camera.camera_ask)["kind"]
    assert set(typing.get_args(kind)) == {"look", "read", "count", "path", "list"}
    assert sig.parameters["kind"].default == "look"
    doc = " ".join(camera.camera_ask.__doc__.split())
    assert "in the language he is speaking" in doc and "exactly" in doc and "about" in doc


# --- "what's in front of me": describe up to three objects with position, nearest first ------------------------


async def test_look_describes_up_to_three_objects_nearest_first(session, monkeypatch):
    _script(monkeypatch, {
        "answer": "A shop aisle.", "guidance": ["Walk slowly."],
        "objects": [{"label": "fridge", "point": [500, 850], "distance": "arm"},
                    {"label": "table", "point": [600, 500], "distance": "near"},
                    {"label": "milk crates", "point": [800, 150], "distance": "far"},
                    {"label": "door", "point": [400, 900], "distance": "far"}],
        "confidence": 0.9,
    })
    out = await camera.camera_ask("what's in front of me?")
    say = out["facts"]
    assert say.startswith("The table is straight ahead, within arm's reach.")
    assert say.index("table") < say.index("fridge") < say.index("milk crates")
    assert "door" not in say                                   # at most three objects
    assert "A shop aisle" not in say                           # the positions say more than the summary
    assert say.endswith("Walk slowly.")
    assert camera.MAX_OBJECTS == 3


async def test_distance_falls_back_to_the_box_size_and_people_are_dropped(session, monkeypatch):
    _script(monkeypatch, {
        "answer": "", "guidance": [],
        "objects": [{"label": "table", "point": [600, 500], "box": [200, 100, 900, 900]},        # big: near
                    {"label": "shelf", "point": [400, 300], "box": [300, 200, 600, 500]},        # a quarter: arm
                    {"label": "door", "point": [400, 900], "box": [400, 850, 500, 950]},         # small: far
                    {"label": "a man", "point": [500, 500], "distance": "near"}],
        "confidence": 0.9,
    })
    out = await camera.camera_ask("what's around?")
    assert [(o["label"], o["distance"]) for o in out["objects"]] == [("table", "near"), ("shelf", "arm"), ("door", "far")]
    assert out["objects"][0]["box"] == [200, 100, 900, 900]
    assert "man" not in out["facts"]


def test_parse_objects_drops_bad_points_boxes_and_safe_labels():
    objs = camera.parse_objects([
        {"label": "crate", "point": [100, 1200], "distance": "near"},                 # out of range
        {"label": "", "point": [100, 100]},                                           # no label
        {"label": "safe path", "point": [100, 100], "distance": "near"},              # never "safe"
        {"label": "crate", "point": [100, 100], "box": [500, 500, 100, 100]},         # inverted box: dropped box
        "junk",
    ])
    assert objs == [{"label": "crate", "point": [100, 100], "distance": "arm"}]
    assert camera.distance_from_box(None) is None


async def test_every_camera_ask_result_reaches_the_vision_observers(session, monkeypatch):
    from aira_live.vision import observers
    got = []

    async def cb(session_, objects, context):
        got.append((session_, objects, context))

    monkeypatch.setattr(observers, "OBSERVERS", [cb])
    s, _ = session
    _script(monkeypatch, {"answer": "", "guidance": [],
                          "objects": [{"label": "keys", "point": [700, 150], "distance": "near"}], "confidence": 0.3})
    await camera.camera_ask("what's on the counter?")
    assert got[0][0] is s and got[0][1] == [{"label": "keys", "point": [700, 150], "distance": "near"}]
    assert got[0][2]["source"] == "camera_ask" and got[0][2]["kind"] == "look"
    assert got[0][2]["confidence"] == 0.3 and got[0][2]["frame_id"] == "f1.0"
