"""The walk's navigation-only Live session (aira_live.nav_session) and how the walk uses it (tools/walk.py).

A fake Live connection stands in for google-genai: it records what is sent and plays back tool calls. Nothing
calls a model.
"""
import asyncio
from types import SimpleNamespace

import pytest

from aira_live import nav_session
from aira_live.nav_session import NavSession
from aira_live.session import current_session
from aira_live.tools import walk


class FakeLive:
    def __init__(self):
        self.video: list[bytes] = []
        self.texts: list[str] = []
        self.responses: list = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.fail_send = False

    async def send_realtime_input(self, *, video=None, text=None):
        if self.fail_send:
            raise ConnectionError("socket closed")
        if video is not None:
            self.video.append(video.data)
        if text is not None:
            self.texts.append(text)

    async def send_tool_response(self, *, function_responses):
        self.responses.extend(function_responses)

    def call(self, **args):
        fc = SimpleNamespace(id=f"c{len(self.texts)}", name="nav_report", args=args)
        self.inbox.put_nowait(SimpleNamespace(tool_call=SimpleNamespace(function_calls=[fc])))

    def drop(self):
        self.inbox.put_nowait(ConnectionError("1011 internal"))

    async def receive(self):   # one turn per call, like google-genai
        msg = await self.inbox.get()
        if isinstance(msg, Exception):
            raise msg
        yield msg


class Stack:
    closed = 0

    async def aclose(self):
        Stack.closed += 1


def connector(live):
    async def connect():
        return live, Stack()
    return connect


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_frames_checks_and_silent_reports():
    live, got = FakeLive(), []

    async def on_report(args):
        got.append(args)

    nav = NavSession("s1", on_report, connect=connector(live))
    assert await nav.start() and nav.alive
    assert await nav.send_frame(b"jpeg") and live.video == [b"jpeg"]
    assert await nav.request("[NAV CHECK 1] look") and live.texts == ["[NAV CHECK 1] look"]
    live.call(check_id=1, path="clear_in_view")
    await settle()
    assert got == [{"check_id": 1, "path": "clear_in_view"}]
    from google.genai import types
    assert live.responses[0].scheduling == types.FunctionResponseScheduling.SILENT   # never spoken
    await nav.close()
    assert not nav.alive and not await nav.request("[NAV CHECK 2] look")


async def test_open_failure_and_drop_leave_it_not_alive():
    async def broken():
        raise TimeoutError

    nav = NavSession("s1", lambda a: None, connect=broken)
    assert await nav.start() is False and not nav.alive
    live = FakeLive()
    nav = NavSession("s1", lambda a: None, connect=connector(live))
    await nav.start()
    live.drop()
    await settle()
    assert not nav.alive                                         # the walk falls back to the conversation
    live2 = FakeLive()
    nav2 = NavSession("s2", lambda a: None, connect=connector(live2))
    await nav2.start()
    live2.fail_send = True
    assert await nav2.request("[NAV CHECK 1]") is False and not nav2.alive
    await nav.close()
    await nav2.close()


async def test_a_bad_report_never_ends_the_session():
    live = FakeLive()

    async def on_report(args):
        raise ValueError("bad")

    nav = NavSession("s1", on_report, connect=connector(live))
    await nav.start()
    live.call(check_id=1)
    await settle()
    assert nav.alive and len(live.responses) == 1
    await nav.close()


def test_the_nav_session_is_live_only_and_silent():
    from google.genai import types
    decl = nav_session._nav_declaration(types)
    cfg = nav_session._config(types, decl)
    assert decl.name == "nav_report" and decl.behavior == types.Behavior.NON_BLOCKING
    assert cfg.context_window_compression is not None              # video sessions outlive 2 minutes
    assert "never speak" in nav_session.NAV_SYSTEM


# ---------- the walk with its own navigation session ----------

class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class BusyBridge:
    """The conversation session: always busy (the owner talks, AIRA answers)."""

    def __init__(self):
        self.checks, self.notices = [], []
        self.answering = self.user_speaking = True
        self.stopped = False

    async def request_nav_check(self, text):
        return False

    async def speak_notice(self, text, urgent=False):
        self.notices.append((text, urgent))


@pytest.fixture
def session(mk_session):
    s, rec = mk_session(lang="en")
    token = current_session.set(s)
    yield s, rec
    current_session.reset(token)
    walk._ACTIVE.clear()


async def test_the_walk_keeps_checking_through_conversation_with_its_nav_session(session):
    s, rec = session
    clock, bridge, live = Clock(), BusyBridge(), FakeLive()
    made = []

    def factory(loop):
        nav = NavSession(s.session_id, loop.on_nav_report, connect=connector(live))
        made.append(nav)
        return nav

    loop = walk.WalkLoop(s, "fridge", clock=clock, bridge=lambda: bridge, nav_factory=factory)
    walk._ACTIVE[s.session_id] = loop
    await loop.open_nav()
    assert loop.nav_alive()
    clock.t += 2.0
    await walk.on_live_frame(s, False, "aGVsbG8=")
    await loop.tick()
    assert live.texts == []                                       # no check on the session's first frame
    for n in range(1, 4):                                         # Sam talks the whole time
        clock.t += 2.0
        await walk.on_live_frame(s, False, "aGVsbG8=")               # the same frame reaches the nav session
        await loop.tick()
        assert len(live.texts) == n and f"[NAV CHECK {n}]" in live.texts[-1]
        clock.t += 0.8
        live.call(check_id=n, target_visible=True, target_side="ahead", target_size="medium",
                  path="clear_in_view", view_ok=True)
        await settle()
        assert loop.pending is None
    assert live.video == [b"hello"] * 4 and loop.fails <= 1 and loop.preempted == 0
    assert loop.state == walk.NAVIGATING                          # guidance continued during the talk
    clock.t += 2.0
    await walk.on_live_frame(s, False, "aGVsbG8=")
    await loop.tick()
    assert loop.pending is not None
    clock.t += 0.8
    live.call(check_id=loop.check_seq, target_visible=True, target_side="ahead", path="blocked",
              obstacles=[{"label": "box", "side": "ahead", "in_path": True, "size": "large"}])
    await settle()
    assert bridge.notices[-1][1] is True and loop.state == walk.HAZARD   # a stop while he is still talking
    await walk.stop_walk(s)
    assert not made[0].alive                                       # the nav session closes with the walk


async def test_a_dropped_nav_session_falls_back_to_quiet_moment_checks(session):
    s, rec = session
    clock, bridge, live = Clock(), BusyBridge(), FakeLive()
    loop = walk.WalkLoop(s, "fridge", clock=clock, bridge=lambda: bridge,
                         nav_factory=lambda lp: NavSession(s.session_id, lp.on_nav_report, connect=connector(live)))
    walk._ACTIVE[s.session_id] = loop
    await loop.open_nav()
    live.drop()
    await settle()
    assert not loop.nav_alive()
    for _ in range(6):                                            # conversation busy: wait, never fail
        clock.t += 0.5
        loop.on_live_frame()
        await loop.tick()
    assert live.texts == [] and loop.fails == 0
    await walk.stop_walk(s)


async def test_no_nav_session_without_the_flag_or_in_mock(session, monkeypatch):
    s, rec = session
    loop = walk.WalkLoop(s, "fridge", bridge=lambda: None)
    monkeypatch.setenv("AIRA_WALK_NAV_SESSION", "0")
    assert walk.default_nav_factory(loop) is None
    monkeypatch.setenv("AIRA_WALK_NAV_SESSION", "1")
    monkeypatch.setenv("AIRA_MOCK", "1")
    assert walk.default_nav_factory(loop) is None


async def test_a_new_nav_sessions_first_empty_answers_are_warm_up_then_count(session):
    """Real Live: the first answers of a fresh navigation session came back empty. No false "can't see" stop for
    those; empties after the warm-up still lead to the honest degraded stop."""
    s, rec = session
    clock, bridge, live = Clock(), BusyBridge(), FakeLive()
    loop = walk.WalkLoop(s, "fridge", clock=clock, bridge=lambda: bridge,
                         nav_factory=lambda lp: NavSession(s.session_id, lp.on_nav_report, connect=connector(live)))
    walk._ACTIVE[s.session_id] = loop
    await loop.open_nav()
    await walk.on_live_frame(s, False, "aGVsbG8=")
    for _ in range(walk.NAV_WARMUP_CHECKS + walk.MAX_FAILS):
        clock.t += 2.0
        await walk.on_live_frame(s, False, "aGVsbG8=")
        await loop.tick()
        clock.t += 0.8
        live.call(check_id=loop.check_seq)                           # check_id only: no observation
        await settle()
        if loop.nav.checks <= walk.NAV_WARMUP_CHECKS:
            assert loop.fails == 0 and bridge.notices == []
    assert loop.state == walk.DEGRADED and loop.fails == walk.MAX_FAILS
    assert bridge.notices[-1][1] is True                              # then the honest stop
    await walk.stop_walk(s)
