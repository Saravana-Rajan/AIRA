"""Walk with me: the loop driving Live nav checks, the state machine, speaking, and the Live tools (tools/walk.py).

Perception is simulated: a fake bridge records the [NAV CHECK n] notes and the notices, and the tests answer each
check through the real ``nav_report`` tool. The clock is fake; nothing calls a model.
"""
import asyncio
import json

import pytest

from aira_live.contracts import CueMsg, RequestFrameMsg, TranscriptMsg
from aira_live.session import current_session
from aira_live.tools import walk
from aira_live.vision import walk as vwalk


class Clock:
    def __init__(self, t0: float = 100.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


class FakeBridge:
    """The bits of LiveBridge the walk uses: request_nav_check (idle only) and speak_notice."""

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


@pytest.fixture
def no_memory(monkeypatch):
    async def none(session, target):
        return None, None

    monkeypatch.setattr(walk, "remembered_place", none)


def make(s, clock, bridge, target="fridge", **kw) -> walk.WalkLoop:
    loop = walk.WalkLoop(s, target, clock=clock, bridge=lambda: bridge, **kw)
    walk._ACTIVE[s.session_id] = loop
    return loop


async def check(loop, clock, dt=2.0, frame=True) -> int | None:
    """Time passes, a live frame reaches Live, the loop ticks: the id of the check it sent (or None)."""
    clock.t += dt
    if frame:
        loop.on_live_frame()
    before = loop.check_seq
    await loop.tick()
    return loop.check_seq if loop.check_seq != before else None


async def answer(loop, clock, cid=None, latency=0.8, **report) -> None:
    """Live answers the check through the real nav_report tool."""
    clock.t += latency
    out = await walk.nav_report(check_id=loop.check_seq if cid is None else cid, **report)
    assert out == {"status": "ok", "retryable": False, "message": ""}


async def look(loop, clock, **report) -> None:
    cid = await check(loop, clock)
    assert cid is not None, "a check should have been sent"
    await answer(loop, clock, cid, **report)


CHAIR = [{"label": "chair", "side": "ahead", "in_path": True, "size": "medium"}]
STAIRS = [{"label": "stairs", "side": "ahead", "in_path": True, "size": "small"}]
CLEAR = dict(target_visible=True, target_side="ahead", target_size="medium", path="clear_in_view")


def _spoken(rec) -> list[str]:
    return [m.text for m in rec.msgs if isinstance(m, TranscriptMsg)]


# ---------- availability ----------
@pytest.mark.parametrize("er2,walk_on", [(True, True), (True, False), (False, True), (False, False)])
def test_walk_tools_follow_aira_walk_independently_of_er2(er2, walk_on):
    from aira_live.tools import ER_TOOLS, WALK_TOOLS, active_tools

    names = {f.__name__ for f in active_tools(er2, walk_on)}
    assert (WALK_TOOLS <= names) is walk_on and not (WALK_TOOLS & names and not walk_on)
    assert (ER_TOOLS <= names) is er2 and not (ER_TOOLS & names and not er2)
    assert "walk_to" not in ER_TOOLS and "walk_stop" not in ER_TOOLS
    assert {"price_query", "memory_where", "web_search"} <= names                 # everything else unchanged


def test_aira_walk_is_off_by_default(monkeypatch):
    from aira_live import config

    monkeypatch.delenv("AIRA_WALK", raising=False)
    assert config.load().walk is False
    monkeypatch.setenv("AIRA_WALK", "1")
    monkeypatch.setenv("AIRA_ER2", "0")
    s = config.load()
    assert s.walk is True and s.er2 is False


def test_no_live_tool_declares_an_empty_enum_value():
    """Live rejects "" in a function-declaration enum at setup (1007)."""
    from google.adk.tools import FunctionTool

    from aira_live.tools import TOOL_FUNCS

    def enums(node):
        if isinstance(node, dict):
            if "enum" in node:
                yield from node["enum"]
            for v in node.values():
                yield from enums(v)
        elif isinstance(node, list):
            for v in node:
                yield from enums(v)

    for fn in TOOL_FUNCS:
        decl = FunctionTool(fn)._get_declaration()
        schema = decl.parameters_json_schema or (decl.parameters.model_dump(exclude_none=True)
                                                 if decl.parameters else {})
        assert all(str(v).strip() for v in enums(schema)), fn.__name__
    decl = FunctionTool(walk.nav_report)._get_declaration().parameters_json_schema
    assert decl["properties"]["target_side"]["enum"] == ["left", "ahead", "right", "none"]


def test_nav_report_is_non_blocking_and_silent(mk_session):
    from google.adk.tools import FunctionTool
    from google.genai import types

    from aira_live.tool_runtime import live_tools
    from aira_live.tools import NON_BLOCKING_TOOLS, SILENT_TOOLS, active_tools

    s, _ = mk_session()
    tools = live_tools(active_tools(False, True), s, NON_BLOCKING_TOOLS, SILENT_TOOLS)
    (nav,) = [t for t in tools if isinstance(t, FunctionTool) and t.name == "nav_report"]
    assert nav.behavior == types.Behavior.NON_BLOCKING
    assert nav.response_scheduling == types.FunctionResponseScheduling.SILENT
    out = asyncio.run(nav.func(check_id=1))
    assert isinstance(out, types.FunctionResponse) and out.scheduling == types.FunctionResponseScheduling.SILENT
    assert out.response == {"status": "ok", "retryable": False, "message": ""}


@pytest.mark.parametrize("er2,walk_on", [(True, True), (True, False), (False, True), (False, False)])
def test_prompt_rule_18_follows_aira_walk_with_the_same_numbering(er2, walk_on):
    import re

    from aira_live.prompt import render_prompt, rules_for

    rules = rules_for(er2, walk_on)
    assert [int(re.match(r"(\d+)\. ", r).group(1)) for r in rules] == list(range(1, 21))
    low = render_prompt({"name": "Sunrise Dairy"}, [], [], "en", "short", er2=er2, walk=walk_on).lower()
    assert "18. when the owner wants to go somewhere or reach something" in low
    for phrase in ("call walk_to once", "call walk_stop immediately", "[nav check n]", "call nav_report once",
                   "never call nav_report unless", "answer it normally: the walk goes on",
                   "call walk_to with the new target", "addressing him by name"):
        assert (phrase in low) is walk_on, phrase
    assert "never say the way is clear or safe" in low
    if er2:
        assert "camera_ask" in low
    else:
        assert "call camera_ask" not in low


async def test_main_live_frame_feeds_the_walk(session):
    from live_fakes import FakeBackend

    from aira_live import main
    from aira_live.frames import Frame
    from aira_live.live_bridge import LiveBridge

    s, rec = session
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    await bridge.start()
    clock = Clock()
    loop = walk.WalkLoop(s, "fridge", clock=clock, bridge=lambda: bridge)
    walk._ACTIVE[s.session_id] = loop
    jpeg = "/9j/4AAQSkZJRg=="
    await main.live_frame(s, bridge, Frame(id="f1", jpeg_b64=jpeg, w=768, h=432, ts=1.0, purpose="live"),
                          video=True)
    assert loop.frame_at == clock.t and b.sent_video
    bridge.video.last_sent_at = float("-inf")
    clock.t += 1.0
    await main.live_frame(s, bridge, Frame(id="f2", jpeg_b64=jpeg, w=768, h=432, ts=2.0, purpose="live"),
                          video=True, changed=True)
    checks = [t for t in b.sent_text if t.startswith("[NAV CHECK 1]")]
    assert len(checks) == 1 and loop.pending is not None                      # a changed frame: checked at once
    assert not [t for t in b.sent_text if t.startswith("[SYSTEM NOTE] Camera")]   # no nudge while walking
    await bridge.close()


# ---------- the bridge's check sender ----------
async def test_request_nav_check_sends_one_text_only_while_idle(mk_session):
    from live_fakes import FakeBackend

    from aira_live.live_bridge import LiveBridge

    s, _ = mk_session()
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    await bridge.start()
    assert await bridge.request_nav_check("[NAV CHECK 1] look") is True
    assert b.sent_text == ["[NAV CHECK 1] look"]
    bridge._answering = True                                   # AIRA mid-answer: client text would cut it
    assert await bridge.request_nav_check("[NAV CHECK 2] look") is False
    bridge._answering = False
    bridge._in_partial = "how much is"                         # the owner is speaking
    assert await bridge.request_nav_check("[NAV CHECK 3] look") is False
    bridge._in_partial = ""
    await bridge.hard_stop()                                   # long-press
    sent = len(b.sent_text)
    assert await bridge.request_nav_check("[NAV CHECK 4] look") is False
    assert bridge.stopped and len(b.sent_text) == sent         # a check never lifts the stop
    await bridge.close()


# ---------- start / stop and the phone signal ----------
async def test_walk_to_starts_once_and_signals_the_phone(session, no_memory):
    s, rec = session
    out = await walk.walk_to("  fridge ")
    assert out["active"] and out["target"] == "fridge"
    assert out["say_en"] == "I'll guide you to the fridge and tell you about anything in the way. Walk slowly."
    loop = walk.active(s)
    assert loop is not None and loop.task is not None and loop.state == walk.ACQUIRING
    starts = [m for m in rec.msgs if isinstance(m, RequestFrameMsg) and m.purpose == "walk" and m.continuous]
    assert len(starts) == 1
    out = await walk.walk_stop()
    assert out["was_active"] and walk.active(s) is None and not loop.active and loop.state == walk.STOPPED
    assert any(isinstance(m, RequestFrameMsg) and m.purpose == "walk" and m.stop for m in rec.msgs)
    assert (await walk.walk_stop())["was_active"] is False
    assert (await walk.walk_to(""))["active"] is False


async def test_walk_to_a_new_target_replaces_it_in_the_same_loop(session, no_memory):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    cid = await check(loop, clock)
    out = await walk.walk_to("door")
    assert out["changed"] and walk.active(s) is loop and loop.target == "door" and loop.state == walk.ACQUIRING
    await answer(loop, clock, cid, **CLEAR)                    # a report for the fridge check: ignored
    assert fb.notices == [] and len(loop.recent) == 0
    assert (await walk.walk_to("door"))["already"] and walk.active(s) is loop
    assert await check(loop, clock) is not None and "walk to the door" in fb.checks[-1]


# ---------- proactive guidance ----------
async def test_a_silent_owner_keeps_getting_checks_and_a_new_obstacle_is_warned(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    for _ in range(3):
        await look(loop, clock, **CLEAR)
    assert len(fb.checks) == 3 and all(c.startswith("[NAV CHECK") for c in fb.checks)
    assert "check_id=3" in fb.checks[-1] and "Do not speak" in fb.checks[-1]
    assert fb.meanings() == ["The fridge is ahead. Keep going slowly."]          # confirmed on the 2nd report
    assert loop.state == walk.NAVIGATING
    await look(loop, clock, obstacles=CHAIR, **CLEAR)                           # first sighting: wait
    assert len(fb.notices) == 1
    await look(loop, clock, obstacles=CHAIR, **CLEAR)                           # confirmed: stop, no question
    text, urgent = fb.notices[-1]
    assert urgent and "chair" in text and loop.state == walk.HAZARD
    assert any(isinstance(m, CueMsg) and m.cue == "warn" for m in rec.msgs)
    facts = json.loads(text.split(" (meaning: ")[0])
    assert facts["action"] == "stop" and facts["obstacle"] == "chair"


async def test_urgent_hazard_preempts_on_one_report_and_repeats_shorter(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await look(loop, clock, obstacles=STAIRS, path="blocked", target_visible=True, target_side="ahead")
    assert fb.notices == [('{"action": "stop", "hazard": "stairs"} (meaning: Stop. Stairs ahead.)', True)]
    await look(loop, clock, obstacles=STAIRS, path="blocked")
    assert fb.meanings()[-1] == "Stop. Stairs." and fb.notices[-1][1]       # never suppressed near a hazard


async def test_obstacle_clears_after_two_fresh_reports_then_resume_line(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    big = [{"label": "table", "side": "ahead", "in_path": True, "size": "large"}]
    await look(loop, clock, obstacles=big, path="blocked")
    assert loop.state == walk.HAZARD
    await look(loop, clock, **CLEAR)
    assert loop.state == walk.HAZARD and len(fb.notices) == 1                  # one clear report is not enough
    await look(loop, clock, **CLEAR)
    assert loop.state == walk.NAVIGATING
    assert fb.meanings()[-1] == "The table is no longer in your path in the current view. Walk on slowly toward " \
                                "the fridge."


async def test_duplicate_direction_is_not_repeated_within_six_seconds(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    left = dict(target_visible=True, target_side="left", target_size="medium", path="clear_in_view")
    for _ in range(4):                                                          # 8 s of the same view
        await look(loop, clock, **left)
    assert fb.meanings() == ["The fridge is on your left. Turn left a little."]


async def test_stale_and_out_of_order_reports_are_discarded(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    cid = await check(loop, clock)
    await answer(loop, clock, cid + 5, obstacles=STAIRS, path="blocked")       # no such check
    await answer(loop, clock, cid - 1, obstacles=STAIRS, path="blocked")       # an older one
    assert fb.notices == [] and loop.pending is not None
    await answer(loop, clock, cid, **CLEAR)
    await answer(loop, clock, cid, obstacles=STAIRS, path="blocked")           # answered twice: the 2nd ignored
    assert fb.notices == [] and loop.pending is None


async def test_an_old_observation_says_stops_but_no_directions(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb, check_timeout_s=10.0)
    for _ in range(2):
        cid = await check(loop, clock)
        await answer(loop, clock, cid, latency=3.5, **CLEAR)                    # 3.5 s old: too old to direct
    assert fb.notices == []
    cid = await check(loop, clock)
    await answer(loop, clock, cid, latency=3.5, obstacles=STAIRS, path="blocked")
    assert fb.meanings() == ["Stop. Stairs ahead."]


async def test_ambiguous_view_degrades_once_and_gives_no_direction(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    bad = dict(target_visible=True, target_side="left", path="uncertain")
    for _ in range(3):
        await look(loop, clock, **bad)
    assert fb.meanings() == ["I can't see the path clearly right now. Please stop for a moment."]
    assert loop.state == walk.DEGRADED and fb.notices[0][1] is True
    await look(loop, clock, target_visible=True, target_side="left", path="clear_in_view", view_ok=False)
    assert len(fb.notices) == 1


async def test_failed_checks_degrade_and_never_pretend(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    for _ in range(3):                                   # Live never answers: each check times out
        await check(loop, clock)
        clock.t += walk.CHECK_TIMEOUT_S + 0.1
        await loop.tick()
    assert loop.state == walk.DEGRADED and loop.fails >= walk.MAX_FAILS
    assert fb.meanings().count("I can't see the path clearly right now. Please stop for a moment.") == 1
    assert loop.active                                    # the walk waits for a clear view, it does not end


async def test_no_frames_reaching_live_degrade(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    for _ in range(3):
        assert await check(loop, clock, frame=False) is None
    assert loop.state == walk.DEGRADED and len(fb.checks) == 0 and len(fb.notices) == 1


async def test_checks_wait_while_live_answers_or_the_owner_speaks(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    fb.answering = True
    assert await check(loop, clock) is None
    fb.answering, fb.user_speaking = False, True
    assert await check(loop, clock) is None
    assert loop.fails == 0 and loop.state == walk.ACQUIRING                    # busy is not a failure
    fb.user_speaking = False
    assert await check(loop, clock, dt=0.5) is not None                        # goes on the next tick


async def test_at_most_one_check_in_flight(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    assert await check(loop, clock) is not None
    assert await check(loop, clock) is None                                    # still waiting for the report
    await loop.on_changed_frame()
    assert len(fb.checks) == 1


async def test_a_changed_frame_checks_at_once(session):
    from aira_live import main

    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    clock.t += 1.0
    await walk.on_live_frame(s, changed=False)
    assert loop.frame_at == clock.t and fb.checks == []
    clock.t += 0.2
    await walk.on_live_frame(s, changed=True)
    assert len(fb.checks) == 1
    assert hasattr(main, "live_frame")


async def test_arrival_is_a_candidate_and_the_walk_keeps_watching(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    near = dict(target_visible=True, target_side="ahead", target_size="large", path="clear_in_view")
    await look(loop, clock, **near)
    await look(loop, clock, **near)
    assert loop.state == walk.ARRIVAL_CANDIDATE and loop.active
    assert fb.meanings() == ["The fridge appears to be just ahead. Reach forward carefully to confirm."]
    assert all("arrived" not in m.lower() for m in fb.meanings())
    assert await check(loop, clock) is not None                                # still watching


async def test_the_target_never_fast_stops(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    close = [500, 400, 950, 600]
    assert not await loop.on_phone_boxes([{"label": "refrigerator", "box": close, "severity": "danger"}])
    assert await loop.on_phone_boxes([{"label": "person", "box": close}])
    assert fb.notices[-1][1] and loop.state == walk.HAZARD
    assert not await loop.on_phone_boxes([{"label": "person", "box": close}])   # 5 boxes a second: rate limit
    assert await loop.on_phone_boxes([{"label": "chair", "box": close}])        # a new danger goes at once
    said = len(fb.notices)
    for _ in range(2):        # Live lists the fridge itself as in the path: never a stop for it
        await look(loop, clock, obstacles=[{"label": "refrigerator", "in_path": True, "size": "large"}],
                   path="clear_in_view", target_visible=True, target_side="ahead", target_size="medium")
    assert not any(urgent for _, urgent in fb.notices[said:])


async def test_stop_and_disconnect_end_checks_and_ignore_late_reports(session):
    s, rec = session
    for end in ("walk_stop", "disconnect", "long_press"):
        clock, fb = Clock(), FakeBridge()
        loop = make(s, clock, fb)
        cid = await check(loop, clock)
        if end == "walk_stop":
            await walk.walk_stop()
        else:
            await walk.stop_session(s)
        assert loop.state == walk.STOPPED and not loop.active and walk.active(s) is None
        await answer(loop, clock, cid, obstacles=STAIRS, path="blocked")       # a late report
        assert await loop.on_report(vwalk.parse_report("fridge", check_id=cid)) == "inactive"
        await loop.tick()
        await loop.on_phone_boxes([{"label": "person", "box": [500, 400, 950, 600]}])
        assert len(fb.checks) == 1 and fb.notices == [], end


async def test_still_guiding_once_a_minute_only_when_silent(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    loop.state = walk.NAVIGATING
    fb.answering = True
    for _ in range(30):                     # 61 s of ticks, Live busy (no checks, no failures)
        clock.t += 2.05
        loop.on_live_frame()
        await loop.tick()
    assert loop.fails == 0 and fb.checks == []
    assert fb.meanings().count("Still guiding you to the fridge.") == 1


async def test_times_out_after_max_s(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()

    async def sleep(dt):
        clock.t += dt
        await asyncio.sleep(0)

    loop = make(s, clock, fb, sleep=sleep)
    assert walk.MAX_S == 300.0
    await asyncio.wait_for(loop.run(), 5)
    assert loop.ended == "timeout" and loop.state == walk.STOPPED and walk.active(s) is None
    assert fb.meanings()[-1] == "I stopped guiding. Ask me again when you are ready."


async def test_one_loop_per_session(session, no_memory):
    s, rec = session
    first = await walk.start(s, "fridge", run=False)
    second = await walk.start(s, "door", run=False)
    assert not first.active and walk.active(s) is second
    await walk.stop_walk(s)


# ---------- no Robotics-ER anywhere in walking ----------
@pytest.fixture
def no_models(monkeypatch):
    """Any model call in the walk path fails the test."""
    from aira_live import models
    from aira_live.vision import gemini

    def boom(*a, **k):
        raise AssertionError("a model was called during a walk")

    monkeypatch.setattr(models, "model_for", boom)
    monkeypatch.setattr(gemini, "client", boom)
    for name in ("generate_json", "generate", "ask"):
        if hasattr(gemini, name):
            monkeypatch.setattr(gemini, name, boom)


async def test_full_journey_to_the_fridge(session, no_models, monkeypatch):
    """Sam asks once; the walk guides him proactively from Live's own reports to an arrival candidate."""
    from aira_live.memory import items

    s, rec = session
    s.owner_name = "Sam"
    clock, fb = Clock(), FakeBridge()

    async def no_settings(shop):
        return {"items": []}

    async def never_seen(shop, name):
        return None

    monkeypatch.setattr(items, "settings", no_settings)
    monkeypatch.setattr(items, "last_seen", never_seen)
    monkeypatch.setattr(walk, "_bridge_of", lambda sess: fb)
    class LoopWithClock(walk.WalkLoop):
        def __init__(self, *a, **k):
            super().__init__(*a, clock=clock, **k)

    monkeypatch.setattr(walk, "WalkLoop", LoopWithClock)
    # 1. Sam: "take me to the fridge" -> Live calls walk_to once; the acknowledgement is its result
    out = await walk.walk_to("fridge")
    assert out["say_en"] == "I'll guide you to the fridge and tell you about anything in the way. Walk slowly."
    loop = walk.active(s)
    loop.task.cancel()                                   # the test drives the ticks itself
    # 2. Sam says nothing; checks keep firing and the walk speaks by itself
    for _ in range(4):
        await look(loop, clock, **CLEAR)
    assert len(fb.checks) == 4 and loop.state == walk.NAVIGATING
    assert fb.meanings() == ["The fridge is ahead. Keep going slowly."]
    # 3. a chair walks into the path: a stop line without any owner turn
    await look(loop, clock, obstacles=CHAIR, **CLEAR)
    await look(loop, clock, obstacles=CHAIR, **CLEAR)
    stop_line, urgent = fb.notices[-1]
    assert urgent and "Sam, stop." in stop_line and "chair" in stop_line and loop.state == walk.HAZARD
    # 4. the chair is gone for two fresh reports: a resume line, toward the same fridge
    await look(loop, clock, **CLEAR)
    await look(loop, clock, **CLEAR)
    assert fb.meanings()[-1].startswith("The chair is no longer in your path") and loop.state == walk.NAVIGATING
    # 5. Sam asks a question: Live answers it itself (no walk tool); the walk and its destination stay
    fb.answering = True
    assert await check(loop, clock) is None
    fb.answering = False
    assert walk.active(s) is loop and loop.target == "fridge" and loop.active
    # 6. the camera is covered: an honest degraded line, no directions
    said = len(fb.notices)
    for _ in range(2):
        await look(loop, clock, target_visible=True, target_side="left", path="uncertain", view_ok=False)
    assert fb.meanings()[said:] == ["I can't see the path clearly right now. Please stop for a moment."]
    assert loop.state == walk.DEGRADED
    # 7. the view recovers
    await look(loop, clock, **CLEAR)
    await look(loop, clock, **CLEAR)
    assert loop.state == walk.NAVIGATING and fb.meanings()[-1].startswith("I can see the path again")
    # 8. the fridge is large and straight ahead: a candidate, never "arrived"
    near = dict(target_visible=True, target_side="ahead", target_size="large", path="clear_in_view")
    await look(loop, clock, **near)
    await look(loop, clock, **near)
    assert loop.state == walk.ARRIVAL_CANDIDATE
    assert fb.meanings()[-1] == "The fridge appears to be just ahead. Reach forward carefully to confirm."
    assert not any("arrived" in m.lower() or "you are at" in m.lower() for m in fb.meanings())
    # 9. Sam: "I'm here" -> walk_stop; nothing more is checked or said, a late report is ignored
    cid = await check(loop, clock)
    checks, notices = len(fb.checks), len(fb.notices)
    assert (await walk.walk_stop())["was_active"]
    await answer(loop, clock, cid, obstacles=STAIRS, path="blocked")
    for _ in range(3):
        await check(loop, clock)
    assert len(fb.checks) == checks and len(fb.notices) == notices and loop.state == walk.STOPPED
    assert any(isinstance(m, RequestFrameMsg) and m.purpose == "walk" and m.stop for m in rec.msgs)
