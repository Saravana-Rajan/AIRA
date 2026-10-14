"""Scene memory (aira_live.scene_memory, scene_watch, tools.memory.scene_report / memory_where).

Live's silent scene reports are simulated with the real ``scene_report`` tool; the clock is fake; nothing calls a
model or Firestore (memory_where's Firestore fallback is replaced where it is reached).
"""
import pytest

from aira_live import scene_watch
from aira_live.scene_memory import OLD_S, SceneMemory, norm
from aira_live.session import current_session
from aira_live.tools import memory as memtools
from aira_live.tools import walk


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


TABLE = [{"name": "glasses", "on": "table", "near": ["apples"], "side": "ahead"},
         {"name": "apples", "on": "table", "near": ["glasses"], "side": "ahead"},
         {"name": "table", "side": "ahead"}]
FLOOR = [{"name": "rug", "on": "floor", "side": "ahead"}]


def test_a_remembered_after_the_camera_moves():
    clock = Clock()
    m = SceneMemory(clock)
    m.observe(TABLE)
    clock.t += 20
    m.observe(FLOOR)                                          # the camera now faces the floor
    clock.t += 5
    r = m.recall("my glasses")
    assert r["found"] and not r["in_view_now"] and r["side"] is None
    assert r["place"] == "on the table beside the apples" and r["ago_s"] == 25
    assert r["say_en"].startswith("I last saw your glasses on the table beside the apples, a few seconds ago")
    assert "can't confirm they're still there" in r["say_en"]


def test_b_in_view_now_answers_from_the_current_scene():
    clock = Clock()
    m = SceneMemory(clock)
    m.observe(TABLE)
    clock.t += 2
    r = m.recall("glasses")
    assert r["in_view_now"] and r["side"] == "ahead"
    assert r["say_en"] == "Your glasses are in front of the camera now, on the table beside the apples, straight ahead."


def test_c_never_claims_to_see_what_left_the_view():
    clock = Clock()
    m = SceneMemory(clock)
    m.observe(TABLE)
    clock.t += 1
    m.observe(FLOOR)                                          # a newer full view without the glasses
    r = m.recall("glasses")
    assert not r["in_view_now"] and "can't see them now" in r["say_en"]
    clock.t += 30                                             # and a recent sighting goes out of view with time
    m2 = SceneMemory(clock)
    m2.observe(TABLE)
    clock.t += 10
    assert not m2.recall("glasses")["in_view_now"]


def test_d_two_look_alikes_are_flagged_not_guessed():
    m = SceneMemory(Clock())
    m.observe([{"name": "glasses", "on": "table"}, {"name": "glasses", "on": "table"}, {"name": "cup"}])
    r = m.recall("glasses")
    assert r["ambiguous"] and "more than one glasses" in r["say_en"] and "not sure which one is yours" in r["say_en"]
    assert not m.recall("cup")["ambiguous"]


def test_e_an_old_sighting_says_it_may_have_moved():
    clock = Clock()
    m = SceneMemory(clock)
    m.observe(TABLE)
    clock.t += OLD_S + 60
    r = m.recall("glasses")
    assert r["old"] and not r["in_view_now"] and "may have moved" in r["say_en"] and "about 16 minutes ago" in r["say_en"]


def test_f_new_evidence_replaces_the_old_place():
    clock = Clock()
    m = SceneMemory(clock)
    m.observe(TABLE)
    clock.t += 60
    m.observe([{"name": "glasses", "on": "kitchen shelf", "near": ["kettle"], "side": "left"}])   # moved / other room
    clock.t += 1
    r = m.recall("glasses")
    assert r["place"] == "on the kitchen shelf beside the kettle" and r["in_view_now"] and r["side"] == "left"
    assert m.nearby("apples") == ["glasses"]                   # the table sighting of the apples is still there


def test_unknown_object_asks_to_turn_the_camera():
    r = SceneMemory(Clock()).recall("phone")
    assert not r["found"] and "Slowly turn the camera" in r["say_en"]


@pytest.mark.parametrize("raw,key", [("My Glasses", "glasses"), ("apples", "apple"), ("the boxes", "box"),
                                     ("batteries", "battery"), ("keys", "key"), ("glass", "glass")])
def test_names_match_plurals_and_my(raw, key):
    assert norm(raw) == key


def test_reading_glasses_answer_glasses_and_the_store_is_bounded():
    m = SceneMemory(Clock())
    m.observe([{"name": "reading glasses", "on": "desk"}])
    assert m.recall("glasses")["found"]
    for i in range(80):
        m.observe([{"name": f"thing {i}"}], at=float(i))
    assert len(m._items) <= 60


# ---------- the watcher: when a scene check goes ----------

class Bridge:
    def __init__(self):
        self.busy = False
        self.sent: list[str] = []

    async def request_nav_check(self, text):
        if self.busy:
            return False
        self.sent.append(text)
        return True


async def test_h_checks_only_in_quiet_moments_one_at_a_time_and_stale_answers_are_dropped():
    clock = Clock()
    w = scene_watch.SceneWatcher("s1", clock)
    b = Bridge()
    b.busy = True                                             # Sam is talking: nothing goes
    assert not await w.on_frame(b) and b.sent == [] and w.due
    b.busy = False
    assert await w.on_frame(b) and "[SCENE CHECK 1]" in b.sent[0]
    clock.t += 1
    w.scene_changed()
    assert not await w.on_frame(b)                            # one in flight
    assert not w.accept(7)                                    # an answer to another check: dropped
    assert w.accept(1)
    assert not w.accept(1)                                    # a duplicate: dropped
    assert not await w.on_frame(b)                            # MIN_GAP_S
    clock.t += scene_watch.MIN_GAP_S
    assert await w.on_frame(b) and len(b.sent) == 2           # the change is looked at on the next quiet frame
    clock.t += scene_watch.TIMEOUT_S + 0.1
    assert not await w.on_frame(b, walking=True)              # timed out; no scene checks during a walk
    assert w.pending is None and w.dropped == 1
    clock.t += 1
    assert not await w.on_frame(b)                            # nothing new and no refresh due yet
    clock.t += scene_watch.REFRESH_S
    assert await w.on_frame(b)                                # the periodic refresh


@pytest.fixture
def session(mk_session):
    s, rec = mk_session(lang="en")
    token = current_session.set(s)
    yield s
    current_session.reset(token)
    scene_watch.forget(s.session_id)
    walk._ACTIVE.clear()


async def test_scene_report_records_only_the_check_in_flight_then_memory_where_answers(session, monkeypatch):
    s = session
    w = scene_watch.watcher(s.session_id)
    b = Bridge()
    await w.on_frame(b)                                       # [SCENE CHECK 1] in flight
    out = await memtools.scene_report(check_id=1, items=TABLE)
    assert out == {"status": "ok", "retryable": False, "message": ""}      # silent: nothing to say
    late = await memtools.scene_report(check_id=1, items=[{"name": "glasses", "on": "bed"}])
    assert late["status"] == "ok" and s.scene_memory.recall("glasses")["on"] == "table"   # duplicate ignored

    async def no_firestore(*a, **k):
        raise AssertionError("seen in this conversation: no Firestore lookup")

    monkeypatch.setattr(memtools.items, "settings", no_firestore)
    r = await memtools.memory_where("glasses")
    assert r["found"] and r["source"] == "scene_memory" and r["place"] == "on the table beside the apples"


async def test_memory_where_falls_back_to_the_saved_memory_when_not_seen_now(session, monkeypatch):
    async def settings(shop):
        return {"items": ["glasses"], "remembering": True}

    async def last_seen(shop, name, now=None):
        return None

    monkeypatch.setattr(memtools.items, "settings", settings)
    monkeypatch.setattr(memtools.items, "last_seen", last_seen)
    r = await memtools.memory_where("glasses")
    assert r["found"] is False and "not seen" in r["say_en"]


async def test_g_walk_obstacles_are_remembered_on_the_floor(session):
    s = session
    loop = walk.WalkLoop(s, "fridge", bridge=lambda: None, nav_factory=lambda lp: None)
    await walk._handle_report(loop, {"check_id": 0, "path": "blocked", "obstacles": [
        {"label": "box", "side": "left", "in_path": True, "size": "large"}]})
    r = s.scene_memory.recall("box")
    assert r["found"] and r["on"] == "the floor" and r["last_side"] == "left"


def test_the_prompt_note_and_tool_only_with_the_flag(monkeypatch):
    from aira_live.prompt import SCENE_NOTE, render_prompt
    from aira_live.tools import SILENT_TOOLS, active_tools

    on = render_prompt({"name": "Shop"}, [], [], "en", "short", er2=False, scene=True)
    off = render_prompt({"name": "Shop"}, [], [], "en", "short", er2=False)
    assert SCENE_NOTE in on and SCENE_NOTE not in off and "[SCENE CHECK" not in off
    assert "scene_report" in {f.__name__ for f in active_tools(False, scene=True)}
    assert "scene_report" not in {f.__name__ for f in active_tools(False)}
    assert "scene_report" in SILENT_TOOLS
    from aira_live import config
    monkeypatch.delenv("AIRA_SCENE_MEMORY", raising=False)
    assert config.load().scene_memory is False                # off unless AIRA_SCENE_MEMORY=1
