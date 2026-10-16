"""Step-by-step routes inside the walk loop (tools/walk.py + vision/route.py), AIRA_ROUTES on and off.

The same fakes as test_walk.py: a fake bridge records the checks and the notices, the tests answer each check
through the real ``nav_report`` tool, the clock is fake, nothing calls a model.
"""
from pathlib import Path

import pytest

from aira_live import shop_map
from aira_live.contracts import TranscriptMsg
from aira_live.session import current_session
from aira_live.tools import walk
from aira_live.vision import route as vroute
from aira_live.vision import walk as vwalk

ROOT = Path(__file__).resolve().parents[3]
DEMO_MAP = ROOT / "shared" / "catalog" / "demo_cricket_map.json"


class Clock:
    def __init__(self, t0: float = 100.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


class FakeBridge:
    def __init__(self):
        self.checks: list[str] = []
        self.notices: list[tuple[str, bool]] = []
        self._notices: dict = {}
        self.answering = self.user_speaking = self.stopped = False

    async def request_nav_check(self, text: str) -> bool:
        if self.answering or self.user_speaking or self.stopped:
            return False
        self.checks.append(text)
        return True

    async def speak_notice(self, text: str, urgent: bool = False) -> None:
        self.notices.append((text, urgent))

    def meanings(self) -> list[str]:
        return [n.split(" (meaning: ", 1)[1][:-1] if " (meaning: " in n else n for n, _ in self.notices]


@pytest.fixture
def session(mk_session):
    s, rec = mk_session(lang="en")
    token = current_session.set(s)
    yield s, rec
    current_session.reset(token)
    walk._ACTIVE.clear()
    walk._ENDED_BY_DISCONNECT.clear()


@pytest.fixture
def no_memory(monkeypatch):
    async def none(session, target):
        return None, None

    monkeypatch.setattr(walk, "remembered_place", none)


@pytest.fixture
def routes_on(monkeypatch):
    monkeypatch.setenv("AIRA_WALK", "1")
    monkeypatch.setenv("AIRA_ROUTES", "1")
    monkeypatch.setenv("AIRA_ROUTES_MAP", str(DEMO_MAP))
    shop_map.clear_cache()
    yield
    shop_map.clear_cache()


def make(s, clock, bridge, target="counter", route=True, **kw) -> walk.WalkLoop:
    kw.setdefault("nav_factory", lambda lp: None)
    if route is True:
        m = shop_map.load_file(DEMO_MAP)
        route = vroute.RouteGuide(m, m.resolve(target), name="Sam")
    loop = walk.WalkLoop(s, target, clock=clock, bridge=lambda: bridge, route=route or None, **kw)
    walk._ACTIVE[s.session_id] = loop
    return loop


async def check(loop, clock, dt=2.0) -> int | None:
    clock.t += dt
    loop.on_live_frame()
    before = loop.check_seq
    await loop.tick()
    return loop.check_seq if loop.check_seq != before else None


async def answer(loop, clock, cid=None, latency=0.5, **report) -> None:
    clock.t += latency
    out = await walk.nav_report(check_id=loop.check_seq if cid is None else cid, **report)
    assert out == {"status": "ok", "retryable": False, "message": ""}


async def look(loop, clock, **report) -> None:
    cid = await check(loop, clock)
    assert cid is not None, "a check should have been sent"
    await answer(loop, clock, cid, **report)


async def steps(s, total, available=True) -> None:
    await walk.on_client_event(s, "steps", {"total": total, "available": available})


CLEAR = dict(path="clear_in_view", view_ok=True)
AT_ENTRANCE = dict(current_zone="entrance", facing="front", confidence="high", **CLEAR)
AT_COUNTER = dict(current_zone="counter", facing="front", confidence="high", **CLEAR)
CHAIR = [{"label": "chair", "side": "ahead", "in_path": True, "size": "large"}]


# ---------- flag off: unchanged ----------
async def test_flag_off_no_route_and_position_fields_are_ignored(session, no_memory, monkeypatch):
    monkeypatch.delenv("AIRA_ROUTES", raising=False)
    s, rec = session
    assert walk.route_for(s, "counter") is None
    out = await walk.walk_to("counter")
    assert out["route"] is False and walk.active(s).route is None
    await walk.walk_stop()
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb, route=False)
    cid = await check(loop, clock)
    assert "current_zone" not in fb.checks[-1] and "ZONES" not in fb.checks[-1]
    await answer(loop, clock, cid, current_zone="counter", facing="front", confidence="high",
                 target_visible=True, target_side="ahead", target_size="medium", **CLEAR)
    assert loop.recent[-1].zone == "unknown" and loop.state == walk.ACQUIRING   # no map: no zone is trusted


async def test_a_report_with_only_position_fields_is_still_empty(session):
    s, _ = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb, route=False)
    cid = await check(loop, clock)
    await answer(loop, clock, cid, current_zone="counter", facing="left", confidence="high", reflection=False)
    assert loop.fails == 1                                      # no observation of the view at all
    cid = await check(loop, clock)
    await answer(loop, clock, cid, path="clear_in_view")
    assert loop.fails == 0


def test_nav_report_declaration_has_no_empty_enum_and_lists_the_new_fields():
    from google.adk.tools import FunctionTool

    schema = FunctionTool(walk.nav_report)._get_declaration().parameters_json_schema
    props = schema["properties"]
    assert props["facing"]["enum"] == ["front", "left", "right", "back", "unknown"]
    assert props["confidence"]["enum"] == ["low", "medium", "high"]
    assert props["current_zone"]["type"] == "string" and "enum" not in props["current_zone"]
    assert props["reflection"]["type"] == "boolean"
    for p in props.values():
        assert all(str(v).strip() for v in p.get("enum", []))


# ---------- the flag, the map and the target ----------
async def test_walk_to_resolves_the_target_through_the_map_in_any_language(session, no_memory, routes_on):
    s, rec = session
    out = await walk.walk_to("கவுண்டர்")
    assert out["route"] is True
    loop = walk.active(s)
    assert loop.route is not None and loop.route.target_zone == "counter"
    out = await walk.walk_to("the bat wall")
    assert out["changed"] and out["route"] is True and loop.route.target_zone == "bat_wall"
    out = await walk.walk_to("fridge")                           # not on the map: today's guidance
    assert out["route"] is False and loop.route is None and loop.target == "fridge"
    await walk.walk_stop()


async def test_a_missing_or_bad_map_turns_routes_off_quietly(session, no_memory, routes_on, monkeypatch, tmp_path):
    s, _ = session
    monkeypatch.setenv("AIRA_ROUTES_MAP", str(tmp_path / "missing_map.json"))
    shop_map.clear_cache()
    out = await walk.walk_to("counter")
    assert out["route"] is False and walk.active(s).route is None
    await walk.walk_stop()


async def test_checks_and_the_nav_session_prompt_carry_the_zones(session, routes_on, monkeypatch):
    from aira_live import nav_session

    s, _ = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await check(loop, clock)
    assert "current_zone (one of: entrance, counter, bat_wall, kit_wall, ball_shelf; or unknown)" in fb.checks[-1]
    assert "reflection true" in fb.checks[-1] and fb.checks[-1].endswith("Do not speak.")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("AIRA_MOCK", "0")
    nav = walk.default_nav_factory(loop)
    assert isinstance(nav, nav_session.NavSession) and "ZONES of this shop" in nav.system
    assert "MIRROR" in nav.system and "never speak" in nav.system
    assert len(nav.system) < 12_000                              # well inside the 16k-token sliding window
    plain = walk.default_nav_factory(make(s, clock, fb, route=False))
    assert plain.system is None


# ---------- the follower drives the lines ----------
async def test_localise_then_go_steps_then_more_steps_then_arrival_candidate(session, routes_on):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await steps(s, 0)                                            # the phone's baseline
    await look(loop, clock, **CLEAR)                             # nothing recognised yet
    assert fb.meanings() == [vroute.LOCALISE_TEXT]
    await look(loop, clock, **AT_ENTRANCE)
    assert len(fb.notices) == 1                                   # one report: not yet trusted
    await look(loop, clock, **AT_ENTRANCE)                        # 2 of 3: a plan from the entrance
    assert fb.meanings()[-1] == "Go ahead 8 steps." and loop.state == walk.NAVIGATING
    assert '"steps": 8' in fb.notices[-1][0]
    await steps(s, 3)
    clock.t += 2.5
    await steps(s, 6)
    assert fb.meanings()[-1] == "2 more steps."
    await steps(s, 7)
    clock.t += 2.5
    await loop.tick()
    assert fb.meanings()[-1] == "1 more step."
    await steps(s, 8)
    clock.t += 2.5
    await loop.tick()
    assert "appears to be just ahead" in fb.meanings()[-1] and "reach forward carefully" in fb.meanings()[-1].lower()
    assert loop.state == walk.ARRIVAL_CANDIDATE
    assert all(m.role == "aira" for m in rec.msgs if isinstance(m, TranscriptMsg))


async def test_a_confirmed_checkpoint_snaps_and_overshoot_relocalises(session, routes_on):
    s, _ = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb, target="ball shelf")               # entrance -> counter -> ball shelf
    await steps(s, 0)
    await look(loop, clock, **AT_ENTRANCE)
    await look(loop, clock, **AT_ENTRANCE)
    assert fb.meanings()[-1] == "Go ahead 8 steps."
    await look(loop, clock, **AT_COUNTER)
    await look(loop, clock, **AT_COUNTER)                         # at the counter early: snap
    assert fb.meanings()[-1] == "The counter is in front of you."
    clock.t += 2.5
    await loop.tick()                                            # the next leg's line follows on the next tick
    assert fb.meanings()[-1] == "Turn right. Stop when you face the new way."
    await look(loop, clock, current_zone="counter", facing="right", confidence="high", **CLEAR)
    await look(loop, clock, current_zone="counter", facing="right", confidence="high", **CLEAR)
    assert fb.meanings()[-1] == "Go ahead 3 steps."
    await steps(s, 10)
    clock.t += 2.5
    await steps(s, 13)                                           # leg done; next: turn left
    assert fb.meanings()[-1] == "Turn left. Stop when you face the new way."


async def test_overshoot_without_the_checkpoint_stops_him_urgently(session, routes_on):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb, target="ball shelf")
    await steps(s, 0)
    await look(loop, clock, **AT_ENTRANCE)
    await look(loop, clock, **AT_ENTRANCE)
    await steps(s, 8)
    await steps(s, 9)
    await steps(s, 10)                                           # 8 expected + 2, no counter seen
    text, urgent = fb.notices[-1]
    assert urgent and "Sam, stop. I expected the counter by now." in text and vroute.LOCALISE_TEXT in text
    assert loop.route.phase == vroute.RELOCALISE


# ---------- safety always wins ----------
async def test_hazard_mid_route_stops_him_and_step_lines_wait_until_clear(session, routes_on):
    s, _ = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await steps(s, 0)
    await look(loop, clock, **AT_ENTRANCE)
    await look(loop, clock, **AT_ENTRANCE)
    assert fb.meanings()[-1] == "Go ahead 8 steps."
    await look(loop, clock, obstacles=CHAIR, path="blocked", view_ok=True, current_zone="entrance",
               facing="front", confidence="high")
    text, urgent = fb.notices[-1]
    assert urgent and text.startswith('{"action": "stop"') and "chair" in text
    assert loop.state == walk.HAZARD
    await steps(s, 6)                                            # "2 more steps" is not said while stopped
    assert fb.notices[-1] == (text, urgent)
    await look(loop, clock, **AT_ENTRANCE)
    assert loop.state == walk.HAZARD
    await look(loop, clock, **AT_ENTRANCE)                       # two fresh clear reports: resume, then the step line
    assert "no longer in your path" in fb.meanings()[-1]
    clock.t += 2.5
    await loop.tick()
    assert fb.meanings()[-1] == "2 more steps."


async def test_phone_fast_stop_overrides_the_route(session, routes_on):
    s, _ = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await steps(s, 0)
    await look(loop, clock, **AT_ENTRANCE)
    await look(loop, clock, **AT_ENTRANCE)
    assert await loop.on_phone_boxes([{"label": "person", "box": [300, 400, 900, 600], "severity": "danger"}])
    assert loop.state == walk.HAZARD and fb.notices[-1][1] and "Person in front" in fb.notices[-1][0]
    await steps(s, 8)                                            # the leg is done, but he is stopped: silence
    assert "Person in front" in fb.notices[-1][0]


async def test_unusable_view_degrades_before_any_route_line(session, routes_on):
    s, _ = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await look(loop, clock, view_ok=False, path="uncertain", current_zone="entrance", facing="front",
               confidence="high")
    assert fb.meanings() == [vwalk.DEGRADED_TEXT] and loop.state == walk.DEGRADED


# ---------- mirrors and refused motion ----------
async def test_a_reflection_never_places_him(session, routes_on):
    s, _ = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    for _ in range(3):
        await look(loop, clock, current_zone="counter", facing="front", confidence="high", reflection=True,
                   **CLEAR)
    assert loop.route.phase == vroute.LOCALISE and loop.route.localizer.zone is None
    for _ in range(3):                                           # the mirror shows the counter: medium is not enough
        await look(loop, clock, current_zone="counter", facing="front", confidence="medium", **CLEAR)
    assert loop.route.phase == vroute.LOCALISE
    assert all(m == vroute.LOCALISE_TEXT for m in fb.meanings())


async def test_motion_refused_falls_back_to_landmark_guidance_once(session, routes_on):
    s, _ = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await steps(s, 0, available=False)
    assert fb.meanings() == [vroute.NO_MOTION_TEXT]
    await steps(s, 0, available=False)
    assert len(fb.notices) == 1
    await look(loop, clock, **AT_ENTRANCE)
    await look(loop, clock, **AT_ENTRANCE)
    assert fb.meanings()[-1] == "Go ahead slowly toward the counter."
    await look(loop, clock, **AT_COUNTER)
    await look(loop, clock, **AT_COUNTER)
    assert "appears to be just ahead" in fb.meanings()[-1] and loop.state == walk.ARRIVAL_CANDIDATE


async def test_steps_events_are_ignored_without_a_route_and_after_the_walk(session):
    s, _ = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb, route=False)
    await steps(s, 5)
    assert fb.notices == []
    await walk.stop_walk(s)
    await steps(s, 9)
    assert fb.notices == []
    await walk.on_client_event(s, "talk", {})                    # other events: nothing


async def test_main_routes_steps_events_to_the_walk(session, routes_on):
    from aira_live import main

    s, _ = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await look(loop, clock, **AT_ENTRANCE)
    await look(loop, clock, **AT_ENTRANCE)
    await main._lane_hook("aira_live.tools.walk", "on_client_event", s, "steps", {"total": 0, "available": True})
    clock.t += 2.5
    await main._lane_hook("aira_live.tools.walk", "on_client_event", s, "steps", {"total": 6, "available": True})
    assert fb.meanings()[-1] == "2 more steps."
    src = (ROOT / "backend" / "aira_live" / "main.py").read_text(encoding="utf-8")
    assert '"on_client_event", session, msg.name, msg.data' in src
