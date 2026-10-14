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
    walk._ENDED_BY_DISCONNECT.clear()


@pytest.fixture
def no_memory(monkeypatch):
    async def none(session, target):
        return None, None

    monkeypatch.setattr(walk, "remembered_place", none)


def make(s, clock, bridge, target="fridge", **kw) -> walk.WalkLoop:
    kw.setdefault("nav_factory", lambda lp: None)        # no navigation session unless a test gives one
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


async def test_main_live_frame_feeds_the_nav_session_even_when_the_conversation_drops_the_frame(session):
    """main.py passes ``sent``: a frame the conversation session throttled still reaches the walk's own nav
    session; with no nav session it does not count as a new picture."""
    from live_fakes import FakeBackend

    from aira_live import main
    from aira_live.frames import Frame
    from aira_live.live_bridge import LiveBridge

    class Nav:
        alive = ready = True
        checks = 0

        def __init__(self):
            self.frames: list[bytes] = []

        async def send_frame(self, jpeg):
            self.frames.append(jpeg)
            return True

    s, rec = session
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    await bridge.start()
    clock = Clock()
    loop = walk.WalkLoop(s, "fridge", clock=clock, bridge=lambda: bridge)
    walk._ACTIVE[s.session_id] = loop
    jpeg = "/9j/4AAQSkZJRg=="
    frame = Frame(id="f1", jpeg_b64=jpeg, w=768, h=432, ts=1.0, purpose="live")
    assert await main.live_frame(s, bridge, frame, video=True)                # the conversation took this one
    first = loop.frame_at
    clock.t += 0.3                                                            # within its 1 fps gap: dropped
    assert not await main.live_frame(s, bridge, frame, video=True)
    assert loop.frame_at == first                                             # no nav session: not a new picture
    loop.nav = Nav()
    clock.t += 0.3
    assert not await main.live_frame(s, bridge, frame, video=True)
    assert len(loop.nav.frames) == 1 and loop.frame_at == clock.t            # the nav session saw it
    await bridge.close()


async def test_a_fallback_check_never_uses_a_frame_only_the_dead_nav_session_saw(session):
    """Regression: the nav session took a frame the conversation session dropped, then died. The fallback check
    through the conversation session must be timed (and gated) on the conversation session's own newest frame."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)

    class Nav:
        alive = ready = True

    clock.t += 2.0
    loop.on_live_frame()                                        # both sessions have this picture
    both = clock.t
    cid = await check(loop, clock, frame=False)
    await answer(loop, clock, cid, **CLEAR)
    loop.nav = Nav()
    clock.t += 1.0
    loop.on_live_frame(to_bridge=False)                         # only the nav session got this one
    loop.nav.alive = False                                      # ... and then it died
    clock.t += 2.0
    sent = len(fb.checks)
    await loop.tick()
    assert len(fb.checks) == sent                               # no new picture for the conversation session
    clock.t += walk.CHECK_INTERVAL_S
    loop.on_live_frame()                                        # the conversation session gets a fresh one
    await loop.tick()
    assert len(fb.checks) == sent + 1
    assert loop.pending[2] == clock.t and loop.pending[2] > both  # timed on its own newest frame


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
    from aira_live import live_bridge as lb
    bridge.last_speech_at = lb._monotonic()                    # words just heard / an answer just ended
    assert await bridge.request_nav_check("[NAV CHECK 3] look") is False
    bridge.last_speech_at = lb._monotonic() - lb.NAV_QUIET_S - 0.1   # a quiet moment: the check goes
    assert await bridge.request_nav_check("[NAV CHECK 3] look") is True
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


async def test_a_busy_minute_without_observations_stops_him_honestly_not_silently(session):
    """Review finding: with the conversation busy for a minute (no navigation session) the walk stayed silently
    NAVIGATING on evidence a minute old. Now the observation watchdog degrades it after OBSERVATION_GAP_S and
    says the honest line once; while he waits stopped he hears "Stay stopped" every 15 s, never "Still
    guiding" (he was told to stop)."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    loop.state = walk.NAVIGATING
    fb.answering = True
    degraded_at = None
    for _ in range(30):                     # 61 s of ticks, Live busy (no checks, no failures)
        clock.t += 2.05
        loop.on_live_frame()
        await loop.tick()
        if loop.state == walk.DEGRADED and degraded_at is None:
            degraded_at = clock.t
    assert loop.fails == 0 and fb.checks == []
    assert degraded_at is not None and degraded_at - 100.0 <= walk.OBSERVATION_GAP_S + 2.1
    assert fb.meanings().count(vwalk.DEGRADED_TEXT) == 1 and fb.notices[0][1] is True
    assert fb.meanings().count(walk.HOLD_TEXT) >= 2
    assert "Still guiding you to the fridge." not in fb.meanings()


async def test_still_guiding_once_a_minute_while_moving_with_nothing_to_say(session):
    """Observations keep coming but none is worth a line (three different small objects taking turns: each is
    seen in only 1 of the last 3 reports, so every instruction is a hold): the owner hears once a minute that
    the walk goes on. Never while stopped (HAZARD / DEGRADED say "Stay stopped" instead)."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await look(loop, clock, **CLEAR)
    await look(loop, clock, **CLEAR)
    assert loop.state == walk.NAVIGATING and len(fb.notices) == 1
    labels = ("chair", "bucket", "bag")
    for i in range(32):                     # ~90 s of held-back sightings, no direction and no stop
        ob = [{"label": labels[i % 3], "side": "ahead", "in_path": True, "size": "small"}]
        await look(loop, clock, obstacles=ob, **CLEAR)
    assert loop.state == walk.NAVIGATING and loop.fails == 0
    assert fb.meanings().count("Still guiding you to the fridge.") == 1
    assert not any(urgent for _, urgent in fb.notices)


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


async def test_stop_uses_configured_owner_name_when_session_has_none(session, monkeypatch):
    """AIRA_OWNER_NAME addresses the owner by name ("Sam, stop."); unset, the stop is just "Stop."."""
    from aira_live import config

    s, _ = session
    s.owner_name = None
    monkeypatch.setenv("AIRA_OWNER_NAME", "Sam")
    assert walk.WalkLoop(s, "fridge").owner_name == "Sam"
    monkeypatch.delenv("AIRA_OWNER_NAME")
    assert config.load().owner_name is None
    assert walk.WalkLoop(s, "fridge").owner_name is None


async def test_an_empty_report_is_a_failed_look_not_an_uncertain_view(session):
    """Real Live once answered a first check with check_id only: no evidence, so no degraded line at once."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await look(loop, clock)                              # nav_report(check_id=n) and nothing else
    assert fb.notices == [] and loop.fails == 1 and loop.pending is None
    await look(loop, clock, **CLEAR)                     # a real report resets the count
    assert loop.fails == 0
    for _ in range(3):
        await look(loop, clock)
    assert loop.state == walk.DEGRADED
    assert fb.meanings() == ["I can't see the path clearly right now. Please stop for a moment."]


async def test_a_tick_and_a_changed_frame_never_send_two_checks(session):
    """Review finding: the tick and a changed frame raced through the await and sent the same check twice."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    gate = asyncio.Event()

    class SlowBridge(FakeBridge):
        async def request_nav_check(self, text):
            await gate.wait()
            return await super().request_nav_check(text)

    fb = SlowBridge()
    loop = make(s, clock, fb)
    clock.t += 2.0
    loop.on_live_frame()
    a = asyncio.create_task(loop.maybe_check("tick"))
    await asyncio.sleep(0)
    b = asyncio.create_task(loop.maybe_check("changed"))
    await asyncio.sleep(0)
    gate.set()
    assert sorted([await a, await b]) == [False, True]
    assert len(fb.checks) == 1 and loop.pending[0] == loop.check_seq == 1


async def test_slow_clear_reports_never_leave_him_stopped_in_silence(session):
    """Review finding: stale clear reports reset the resume streak; now they are skipped, and while stopped he
    hears "Stay stopped" every 15 s of silence instead of nothing until the walk times out."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await look(loop, clock, target_visible=True, target_side="ahead", path="blocked", obstacles=CHAIR)
    await look(loop, clock, target_visible=True, target_side="ahead", path="blocked", obstacles=CHAIR)
    assert loop.state == walk.HAZARD
    n = len(fb.notices)
    await look(loop, clock, **CLEAR)                                     # fresh clear: 1 of 2
    cid = await check(loop, clock)
    await answer(loop, clock, cid, latency=3.5, **CLEAR)                # stale clear: skipped, streak kept
    assert loop.state == walk.HAZARD and loop.resume_streak == 1
    await look(loop, clock, **CLEAR)                                     # fresh clear: 2 of 2 -> resume
    assert loop.state == walk.NAVIGATING and len(fb.notices) == n + 1

    loop2_clock, fb2 = Clock(), FakeBridge()
    walk._ACTIVE.clear()
    loop2 = make(s, loop2_clock, fb2)
    loop2._to(walk.HAZARD, "test")
    for _ in range(40):                                                  # 20 s, Live never answers usefully
        loop2_clock.t += 0.5
        await loop2.tick()
    assert walk.HOLD_TEXT in fb2.meanings()


# ---------- conversation during a walk ----------
# Real Live (2026-10-14): a check sent while AIRA answered cut her answer; one in flight while the owner spoke could
# swallow his question. So checks wait for a quiet voice channel, the newest frame is checked the moment it is
# quiet, and the phone's on-device detector covers the gap.

@pytest.fixture
def checks_log(monkeypatch):
    lines: list[dict] = []
    real = walk.diag

    def capture(kind, **kw):
        if kind == "walk_check":
            lines.append(kw)
        return real(kind, **kw)

    monkeypatch.setattr(walk, "diag", capture)
    return lines


async def busy_for(loop, clock, fb, seconds, *, speaking=False, answering=False):
    """The voice channel is busy for ``seconds``: a live frame reaches Live every second, the loop ticks."""
    fb.user_speaking, fb.answering = speaking, answering
    for i in range(int(seconds / 0.5)):
        clock.t += 0.5
        if i % 2:
            loop.on_live_frame()
        await loop.tick()
    fb.user_speaking = fb.answering = False


async def test_no_check_while_sam_speaks_then_one_check_on_the_newest_frame(session, checks_log):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await busy_for(loop, clock, fb, 8, speaking=True)
    assert fb.checks == [] and loop.pending is None and loop.fails == 0       # waiting is not failing
    assert [c.get("skipped") for c in checks_log] == ["busy"]                 # one line per busy stretch
    newest = loop.frame_at
    clock.t += 0.5
    await loop.tick()                                                         # quiet again: checks at once
    assert len(fb.checks) == 1 and loop.pending[2] == newest
    assert checks_log[-1]["sent"] is True and checks_log[-1]["deferred_ms"] >= 7000


async def test_no_check_while_aira_answers_then_degraded_then_the_first_look_stops_him(session):
    """AIRA answers for 12 s (no navigation session): no check goes and none fails; after OBSERVATION_GAP_S the
    watchdog stops him honestly; the first look after the talk still stops him for a box, and two fresh clear
    looks let him walk on."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await look(loop, clock, **CLEAR)
    await look(loop, clock, **CLEAR)
    assert loop.state == walk.NAVIGATING
    sent = len(fb.checks)
    await busy_for(loop, clock, fb, 8, answering=True)
    assert len(fb.checks) == sent and loop.fails == 0 and loop.state == walk.NAVIGATING   # under the gap
    await busy_for(loop, clock, fb, 4, answering=True)
    assert len(fb.checks) == sent and loop.fails == 0 and loop.state == walk.DEGRADED
    assert fb.meanings()[-1] == vwalk.DEGRADED_TEXT and fb.notices[-1][1] is True
    await look(loop, clock, target_visible=True, target_side="ahead", path="blocked",
               obstacles=[{"label": "box", "side": "ahead", "in_path": True, "size": "large"}])
    assert fb.notices[-1][1] is True and loop.state == walk.HAZARD           # the first look after it stops him
    await look(loop, clock, **CLEAR)
    await look(loop, clock, **CLEAR)
    assert loop.state == walk.NAVIGATING and fb.meanings()[-1].startswith("The box is no longer in your path")


async def test_urgent_obstacle_while_sam_speaks_is_said_at_once(session):
    """The phone's fast stop never waits for the conversation, and no check is queued in front of it."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    fb.user_speaking = fb.answering = True
    clock.t += 1.0
    assert await loop.on_phone_boxes([{"label": "person", "box": [500, 400, 950, 600]}])
    assert fb.notices[-1][1] is True and fb.meanings()[-1].startswith("Stop")
    assert fb.checks == [] and loop.state == walk.HAZARD


async def test_a_check_overtaken_by_speech_is_cancelled_not_failed(session, checks_log):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    cid = await check(loop, clock)
    assert cid is not None and loop.pending is not None
    fb.user_speaking = True                                                   # Sam starts talking mid-check
    clock.t += 0.5
    await loop.tick()
    assert loop.pending is None and loop.fails == 0 and loop.preempted == 1
    assert checks_log[-1]["skipped"] == "preempted"
    fb.user_speaking = False
    late = vwalk.parse_report("fridge", check_id=cid, target_visible=True, target_side="ahead")
    assert await loop.on_report(late) == "stale"                              # its late answer is ignored
    for _ in range(2):                                                        # overtaken again: never a failure
        await check(loop, clock)
        fb.user_speaking = True
        clock.t += 0.5
        await loop.tick()
        fb.user_speaking = False
    assert loop.state != walk.DEGRADED and loop.fails == 0 and fb.notices == []
    while clock.t - 100.0 <= walk.OBSERVATION_GAP_S:                          # ...until the observation gap
        await check(loop, clock)
        fb.user_speaking = True
        clock.t += 0.5
        await loop.tick()
        fb.user_speaking = False
    assert loop.state == walk.DEGRADED and loop.fails == 0                   # the watchdog, not a failed look
    assert fb.meanings() == [vwalk.DEGRADED_TEXT]


async def test_a_waiting_check_follows_a_new_target_and_dies_with_the_walk(session):
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await busy_for(loop, clock, fb, 4, speaking=True)
    loop.retarget("counter")
    clock.t += 0.5
    loop.on_live_frame()
    await loop.tick()
    assert len(fb.checks) == 1 and "counter" in fb.checks[0] and "fridge" not in fb.checks[0]
    await answer(loop, clock, **CLEAR)
    await busy_for(loop, clock, fb, 4, answering=True)
    await walk.stop_walk(s)
    for _ in range(6):
        clock.t += 0.5
        loop.on_live_frame()
        await loop.tick()
    assert len(fb.checks) == 1                                                # nothing goes after the stop


async def test_a_long_conversation_costs_no_checks_and_leaves_no_backlog(session):
    """A minute of talk sends zero checks; afterwards exactly one goes (no queue, no burst, bounded cost)."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await busy_for(loop, clock, fb, 30, speaking=True)
    await busy_for(loop, clock, fb, 30, answering=True)
    assert fb.checks == []
    for _ in range(3):
        clock.t += 0.25
        await loop.tick()
    assert len(fb.checks) == 1


# ---------- scene change, Live gone, stop / restart, disconnect / resume, mock, wording ----------
async def test_a_scene_change_during_a_pending_check_makes_its_report_stale_and_rechecks(session, checks_log):
    """The phone marked the scene changed after a check went: that check's report describes the older view. It
    may still stop him (a stop on old news is safe) but never direct him, and the new view is checked at once."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await look(loop, clock, **CLEAR)                                           # 1 of 2 for "ahead"
    cid = await check(loop, clock)
    clock.t += 0.3
    await walk.on_live_frame(s, changed=True)                                 # the view changed mid-check
    assert loop.pending is not None and loop.pending[0] == cid and loop.scene_changed_at == clock.t
    await answer(loop, clock, cid, **CLEAR)                                   # would have confirmed "ahead"
    assert fb.notices == []                                                   # no direction from the old view
    assert loop.pending is not None and loop.pending[0] == cid + 1            # the new view: checked at once
    assert checks_log[-1]["reason"] == "scene_changed"
    await answer(loop, clock, **CLEAR)                                        # fresh confirmation: a direction
    assert fb.meanings() == ["The fridge is ahead. Keep going slowly."]
    cid = await check(loop, clock)
    clock.t += 0.3
    await walk.on_live_frame(s, changed=True)
    await answer(loop, clock, cid, obstacles=STAIRS, path="blocked")          # old view, but a hazard: said
    assert fb.notices[-1][1] is True and loop.state == walk.HAZARD
    cid2 = loop.pending[0]                                                    # the prompt re-check
    await answer(loop, clock, cid2, **CLEAR)
    assert loop.scene_changed_at is None and loop.resume_streak == 1          # counted as fresh


async def test_live_gone_for_good_ends_the_walk_with_a_stop_line(session):
    """The conversation bridge ran out of reconnects (LiveBridge.closed): there is no voice to guide with, so
    the walk ends at once with a final stop line instead of ticking in silence until MAX_S."""
    s, rec = session
    clock, fb = Clock(), FakeBridge()
    fb.closed = False
    loop = make(s, clock, fb)
    await look(loop, clock, **CLEAR)
    fb.closed = True
    assert await check(loop, clock) is None
    assert not loop.active and loop.ended == "no_live" and loop.state == walk.STOPPED and walk.active(s) is None
    assert fb.meanings()[-1].startswith("I stopped guiding: I lost the voice connection")
    assert any(isinstance(m, RequestFrameMsg) and m.purpose == "walk" and m.stop for m in rec.msgs)


async def test_live_bridge_exposes_closed(mk_session):
    from live_fakes import FakeBackend

    from aira_live.live_bridge import LiveBridge

    s, _ = mk_session()
    bridge = LiveBridge(s, lambda: FakeBackend(), "prompt", [])
    await bridge.start()
    assert bridge.closed is False
    await bridge.close()
    assert bridge.closed is True


async def test_stop_then_restart_gives_a_fresh_walk(session, no_memory, monkeypatch):
    """walk_stop then walk_to again: a new loop with fresh state and its own check ids; the old one's task is
    gone and its late reports are ignored."""
    from collections import deque

    s, rec = session
    clock, fb = Clock(), FakeBridge()
    monkeypatch.setattr(walk, "_bridge_of", lambda sess: fb)

    class LoopWithClock(walk.WalkLoop):
        def __init__(self, *a, **k):
            super().__init__(*a, clock=clock, nav_factory=lambda lp: None, **k)

    monkeypatch.setattr(walk, "WalkLoop", LoopWithClock)
    await walk.walk_to("fridge")
    first = walk.active(s)
    first.task.cancel()
    await look(first, clock, **CLEAR)
    await look(first, clock, obstacles=STAIRS, path="blocked")
    assert first.state == walk.HAZARD and len(fb.checks) == 2
    assert (await walk.walk_stop())["was_active"]
    await asyncio.sleep(0)
    assert first.task.done() and walk.active(s) is None
    out = await walk.walk_to("fridge")
    second = walk.active(s)
    assert out["active"] and not out.get("already") and second is not first and second.active
    assert second.state == walk.ACQUIRING and second.recent_keys == deque(maxlen=3) and second.hazard_label is None
    second.task.cancel()
    cid = await check(second, clock)
    assert cid == 1 and len(fb.checks) == 3                                   # check ids start again
    await answer(second, clock, cid, obstacles=STAIRS, path="blocked")
    assert first.state == walk.STOPPED and second.state == walk.HAZARD
    starts = [m for m in rec.msgs if isinstance(m, RequestFrameMsg) and m.purpose == "walk" and m.continuous]
    assert len(starts) == 2
    await walk.walk_stop()


async def test_a_disconnect_ends_the_walk_and_a_resume_tells_live_once(session):
    """The phone socket drops mid-walk: the walk ends (nobody sees him move). When the same session resumes
    within the window, Live is told the walk ended so it tells him and only restarts on his word. A long-press
    stop (the session stays registered) leaves no such note."""
    from aira_live import sessions

    s, rec = session
    clock, fb = Clock(), FakeBridge()
    loop = make(s, clock, fb)
    await look(loop, clock, **CLEAR)
    sessions.unregister(s)                                                    # _end_session does this first
    await walk.stop_session(s)
    assert not loop.active and walk._ENDED_BY_DISCONNECT[s.session_id][0] == "fridge"

    class Bridge(FakeBridge):
        def __init__(self):
            super().__init__()
            self.texts = []

        async def send_text(self, text, **kw):
            self.texts.append(text)

    nb = Bridge()
    assert await walk.on_session_resumed(s, nb) is True
    assert len(nb.texts) == 1 and "walk to the fridge ended" in nb.texts[0] and "walk_to" in nb.texts[0]
    assert await walk.on_session_resumed(s, nb) is False                      # told once
    # a long-press: the session is still registered, so nothing is remembered
    loop = make(s, clock, fb)
    sessions.register(s)
    try:
        await walk.stop_session(s)
    finally:
        sessions.unregister(s)
    assert s.session_id not in walk._ENDED_BY_DISCONNECT
    assert await walk.on_session_resumed(s, nb) is False
    # a new walk forgets an old note
    walk._ENDED_BY_DISCONNECT[s.session_id] = ("door", 0.0)
    nl = await walk.start(s, "fridge", run=False, memory_place=None, memory_side=None, bridge=lambda: fb,
                          nav_factory=lambda lp: None)
    assert s.session_id not in walk._ENDED_BY_DISCONNECT
    await nl.stop()


async def test_mock_mode_sends_no_checks_and_opens_no_nav_session(session, monkeypatch):
    """AIRA_MOCK=1 (or no key): no bridge, no navigation session; the walk never calls out, and after three
    looks with nothing to look through it says the honest line and waits."""
    monkeypatch.setenv("AIRA_MOCK", "1")
    monkeypatch.setenv("AIRA_WALK_NAV_SESSION", "1")
    s, rec = session
    clock = Clock()
    loop = walk.WalkLoop(s, "fridge", clock=clock, bridge=lambda: None)
    walk._ACTIVE[s.session_id] = loop
    assert walk.default_nav_factory(loop) is None
    await loop.open_nav()
    assert loop.nav is None and loop.nav_opens == walk.NAV_MAX_OPENS            # no retries either
    for _ in range(3):
        clock.t += 2.0
        loop.on_live_frame()
        await loop.tick()
    assert loop.checks_sent == 0 and loop.state == walk.DEGRADED
    assert _spoken(rec).count(vwalk.DEGRADED_TEXT) == 1


def test_production_flags_defaults(monkeypatch):
    """AIRA_WALK off by default; AIRA_WALK_NAV_SESSION on unless "0"; mock when the key is missing."""
    from aira_live import config

    for name in ("AIRA_WALK", "AIRA_WALK_NAV_SESSION", "AIRA_MOCK", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    s = config.load()
    assert s.walk is False and s.walk_nav_session is True and s.mock is True
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("AIRA_WALK", "1")
    monkeypatch.setenv("AIRA_WALK_NAV_SESSION", "0")
    s = config.load()
    assert s.walk is True and s.walk_nav_session is False and s.mock is False


def test_walk_speech_never_claims_distance_depth_or_a_safe_clear_way():
    """Every spoken walk line is evidence-based: no metres / steps / numbers, no "safe", no "clear path" or
    "the way is clear", no depth or drop-off claims; arrival only as a candidate ("appears")."""
    import inspect
    import re

    from aira_live.tools import walk as twalk

    lines: list[str] = []
    for args in (dict(obstacles=STAIRS, path="blocked"), dict(obstacles=CHAIR, path="blocked"),
                 dict(obstacles=[{"label": "person", "size": "large"}]), dict(path="blocked"),
                 dict(view_ok=False), dict(**CLEAR), dict(target_visible=True, target_side="left", target_size="large"),
                 dict(target_visible=True, target_side="ahead", target_size="large", path="clear_in_view"),
                 dict(path="clear_in_view")):
        rep = vwalk.parse_report("fridge", check_id=1, **args)
        for recent in ([rep], [rep, rep, rep]):
            for search in (vwalk.Search(), vwalk.Search(lost_s=9.0), vwalk.Search(memory_place="near the door")):
                ins = vwalk.decide(rep, "fridge", recent, search, name="Sam")
                lines += [ins.text, ins.short or ""]
    lines += [vwalk.resume_line("fridge", "chair").text, vwalk.resume_line("fridge", None).text,
              vwalk.DEGRADED_TEXT, vwalk.KEEP_GOING_TEXT, twalk.HOLD_TEXT,
              "Still guiding you to the fridge.", "I can see the path again. Walk on slowly toward the fridge."]
    src = inspect.getsource(twalk)
    lines += re.findall(r'"(I stopped guiding[^"]*)"', src)
    bad = re.compile(r"\b(\d+|metres?|meters?|feet|foot|steps? away|safe|safely|the way is clear|path is clear|"
                     r"clear path|all clear|drop-?offs?|depth|you have arrived|you are at|you're at)\b", re.I)
    for text in lines:
        assert not bad.search(text), text
    assert lines and all("appears" in t for t in lines if "just ahead" in t)
