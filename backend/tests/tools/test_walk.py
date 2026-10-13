"""Walk with me: the walk loop's stop conditions, pipelining, speaking and the Live tools (tools/walk.py)."""
import asyncio

import pytest

from aira_live.contracts import RequestFrameMsg, TranscriptMsg
from aira_live.frames import Frame
from aira_live.session import current_session
from aira_live.tools import TOOLS_BY_NAME, NON_BLOCKING_TOOLS, walk
from aira_live.vision.gemini import VisionError
from aira_live.vision.walk import Obstacle, WalkView

TINY = "/9j/4AAQSkZJRg=="
FAR = WalkView((400, 500), (300, 420, 500, 580))            # ahead, about 3 steps
THERE = WalkView((500, 500), (200, 300, 700, 700))          # arrived
NOTHING = WalkView()


class Clock:
    def __init__(self, t0: float = 100.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def session(mk_session):
    s, rec = mk_session(lang="en")
    token = current_session.set(s)
    yield s, rec
    current_session.reset(token)
    walk._ACTIVE.clear()


def _frame(ts: float) -> Frame:
    return Frame(id=f"w{ts}", jpeg_b64=TINY, w=1024, h=768, ts=ts, purpose="walk")


def _spoken(rec) -> list[str]:
    return [m.text for m in rec.msgs if isinstance(m, TranscriptMsg)]


def make(s, replies, clock, *, frames=True, **kw):
    """A loop whose fake ER answers ``replies`` in order (the last one repeats) and whose sleep advances the
    fake clock by one second and (with ``frames``) brings the next walk frame."""
    calls = []

    async def look(frame, target):
        calls.append(frame.ts)
        item = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    async def sleep(dt):
        clock.t += dt
        if frames:
            s.frames.add(_frame(clock.t))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    if frames:
        s.frames.add(_frame(clock.t))
    loop = walk.WalkLoop(s, "fridge", look=look, clock=clock, sleep=sleep, frame_timeout_s=0.01, **kw)
    walk._ACTIVE[s.session_id] = loop
    loop.calls = calls
    return loop


async def _run(loop):
    await asyncio.wait_for(loop.run(), 5)
    await asyncio.sleep(0)


def _stopped_frames(rec) -> bool:
    return any(isinstance(m, RequestFrameMsg) and m.purpose == "walk" and m.stop for m in rec.msgs)


async def test_arrives_and_ends(session):
    s, rec = session
    loop = make(s, [FAR, FAR, THERE], Clock())
    await _run(loop)
    assert loop.ended == "arrived" and not loop.active and walk.active(s) is None
    said = _spoken(rec)
    assert said[0] == "fridge ahead, about 3 steps."
    assert said[-1] == "Stop. The fridge is right in front of you. Reach forward."
    assert _stopped_frames(rec)


async def test_no_transcript_keyword_stop(session, monkeypatch):
    """Stopping by voice is the Live model's walk_stop call, in any language: the walk never reads the transcript."""
    from aira_live import live_bridge

    s, rec = session

    async def no_memory(session, target):
        return None, None

    monkeypatch.setattr(walk, "remembered_place", no_memory)
    for name in ("is_stop", "_STOP"):
        assert not hasattr(walk, name), name
    loop = await walk.start(s, "fridge", run=False)
    assert not hasattr(loop, "on_utterance")
    assert live_bridge._listeners.get(s.session_id) in (None, [])
    await walk.stop_walk(s)


async def test_walk_stop_tool_ends_at_once_and_is_described_for_any_language(session):
    s, rec = session
    loop = make(s, [FAR], Clock())
    out = await walk.walk_stop()
    assert out["was_active"] and out["stopped"] and not loop.active and _stopped_frames(rec)
    assert (await walk.walk_stop())["was_active"] is False          # twice is harmless
    doc = " ".join(walk.walk_stop.__doc__.split())
    assert "ANY language" in doc and "in the language he is speaking" in doc


class _Notices(list):
    urgent: list[bool]


def _bridge(monkeypatch):
    notices = _Notices()

    class Bridge:
        _notices: dict = {}

        async def speak_notice(self, text, urgent=False):
            notices.append(text)
            urgents.append(urgent)

    urgents: list[bool] = []
    monkeypatch.setattr(walk, "_bridge_of", lambda s: Bridge())
    notices.urgent = urgents
    return notices


async def test_instructions_go_to_live_as_facts(session, monkeypatch):
    import json

    s, rec = session
    notices = _bridge(monkeypatch)
    chair = WalkView((400, 500), (300, 420, 500, 580), (Obstacle("chair", (600, 300, 900, 550)),))
    loop = make(s, [FAR, chair, THERE], Clock(), max_in_flight=1)
    await _run(loop)
    facts = [json.loads(n.split(" (meaning: ")[0]) for n in notices]
    assert facts[0] == {"action": "go", "direction": "ahead", "steps_about": 3, "target": "fridge"}
    assert facts[1] == {"action": "stop", "obstacle": "chair", "move": "right"}
    # Stop lines cut in at once for his safety; directions wait their turn.
    assert notices.urgent[1] is True and notices.urgent[0] is False
    assert facts[-1]["action"] == "arrived"
    assert notices[0].endswith("(meaning: fridge ahead, about 3 steps.)")


def test_walk_facts_for_every_instruction():
    from aira_live.vision import walk as vwalk

    view = WalkView((400, 500), None, (Obstacle("wet floor", (600, 400, 900, 600)),))
    ins = vwalk.Instruction("walk.hazard", "Stop.", urgent=True)
    assert walk.walk_facts(ins, view, "fridge") == {"action": "stop", "hazard": "wet floor"}
    assert walk.walk_facts(vwalk.Instruction("walk.left", ""), NOTHING, "door") == {
        "action": "turn", "direction": "left", "target": "door"}
    assert walk.walk_facts(vwalk.Instruction("walk.looking_right", ""), NOTHING, "door")["turn"] == "right"
    assert walk.walk_facts(vwalk.Instruction("walk.not_found", ""), NOTHING, "door")["found"] is False


async def test_auto_stop_after_90_seconds(session):
    s, rec = session
    loop = make(s, [FAR], Clock())
    await _run(loop)
    assert loop.ended == "timeout" and _spoken(rec)[-1] == "I stopped guiding. Ask me again when you are ready."
    assert 80 <= len(loop.calls) <= 91


async def test_three_vision_failures_in_a_row(session):
    s, rec = session
    loop = make(s, [FAR, VisionError("x"), VisionError("x"), VisionError("x")], Clock(), max_in_flight=1)
    await _run(loop)
    assert loop.ended == "blind" and _spoken(rec)[-1] == "I can't see well. Please stop and wait."
    assert len(loop.calls) == 4


async def test_a_good_look_resets_the_failures(session):
    s, rec = session
    loop = make(s, [VisionError("x"), VisionError("x"), FAR, VisionError("x"), VisionError("x"), THERE], Clock(),
                max_in_flight=1)
    await _run(loop)
    assert loop.ended == "arrived"


async def test_frames_stop_arriving(session):
    s, rec = session
    loop = make(s, [FAR], Clock(), frames=False)
    await _run(loop)
    assert loop.ended == "blind" and loop.calls == []


async def test_speaks_on_change_and_reassures_every_4_seconds(session):
    s, rec = session
    loop = make(s, [FAR] * 9 + [THERE], Clock(), max_in_flight=1)
    await _run(loop)
    said = _spoken(rec)
    assert said[0] == "fridge ahead, about 3 steps."
    assert said.count("Keep going.") == 2                  # 9 identical looks over ~9 s
    assert said[-1].startswith("Stop. The fridge")


async def test_obstacle_overrides_at_once(session):
    s, rec = session
    chair = WalkView((400, 500), (300, 420, 500, 580), (Obstacle("chair", (600, 300, 900, 550)),))
    loop = make(s, [FAR, chair, THERE], Clock(), max_in_flight=1)
    await _run(loop)
    assert _spoken(rec)[:2] == ["fridge ahead, about 3 steps.", "Stop. chair in front. Move a little right."]


async def test_lost_target_uses_memory_then_not_found(session):
    s, rec = session
    loop = make(s, [NOTHING] * 12 + [THERE], Clock(), max_in_flight=1, memory_place="near the counter",
                memory_side="right")
    await _run(loop)
    said = _spoken(rec)
    assert said[0] == "The fridge was last seen near the counter. Turn slowly right. I'm looking."
    assert "I can't find the fridge. Try turning around slowly." in said


async def test_stale_result_is_dropped(session):
    s, rec = session
    loop = make(s, [THERE], Clock(), frames=False)
    loop._look = lambda frame, target: _ret(FAR if frame.ts == 5 else NOTHING)
    loop._frame_age = lambda frame: 0.5
    await loop.process(_frame(5))
    await loop.process(_frame(3))          # an older capture finishing later: ignored
    assert loop.acted_ts == 5 and _spoken(rec) == ["fridge ahead, about 3 steps."]


async def _ret(v):
    return v


# --- freshness: a look is judged by how long ago its frame was taken, at the moment its line is said ---------------

CHAIR = WalkView((400, 500), (300, 420, 500, 580), (Obstacle("chair", (600, 300, 900, 550)),))


def aged(s, clock, ages: dict[float, float | None] | None = None, delay: dict[float, float] | None = None):
    """A loop whose fake ER takes ``delay[ts]`` fake seconds and whose frame age is ``ages[ts]`` (when given) or
    the fake time since the frame was taken (frame ts == fake capture time)."""
    loop = make(s, [FAR], clock, frames=False)
    loop._frame_age = lambda f: ages[f.ts] if ages is not None else clock.t - f.ts
    loop.replies = {}

    async def look(frame, target):
        loop.calls.append(frame.ts)
        clock.t += (delay or {}).get(frame.ts, 0.0)
        return loop.replies.get(frame.ts, FAR)

    loop._look = look
    return loop


async def test_a_slow_look_is_too_old_to_say_go(session, monkeypatch):
    s, rec = session
    notices = _bridge(monkeypatch)
    clock = Clock(10.0)
    loop = aged(s, clock, delay={10.0: 0.5, 20.0: 2.4, 30.0: 4.0})
    await loop.process(_frame(10.0))                    # answered in 0.5 s: the count is said
    clock.t = 20.0
    await loop.process(_frame(20.0))                    # 2.4 s: direction only, no count
    clock.t = 30.0
    await loop.process(_frame(30.0))                    # 4 s: expired, never said
    assert _spoken(rec) == ["fridge ahead, about 3 steps.", "fridge ahead. Keep going slowly."]
    assert '"steps_about"' not in notices[1]
    assert loop.acted_ts == 30.0 and loop.stale_streak == 1


async def test_an_older_frame_finishing_later_is_dropped(session):
    s, rec = session
    clock = Clock(10.0)
    gate_a = asyncio.Event()
    loop = aged(s, clock, ages={1.0: 0.5, 2.0: 0.5})

    async def look(frame, target):
        if frame.ts == 1.0:
            await gate_a.wait()                         # the older look is slow ...
            return CHAIR
        return FAR

    loop._look = look
    older = asyncio.create_task(loop.process(_frame(1.0)))
    await asyncio.sleep(0)
    await loop.process(_frame(2.0))                     # ... the newer one finishes first and is acted on
    gate_a.set()
    await older                                         # the late, older answer is ignored even if a stop
    assert loop.acted_ts == 2.0 and _spoken(rec) == ["fridge ahead, about 3 steps."]


async def test_a_stop_is_said_even_when_old(session, monkeypatch):
    s, rec = session
    notices = _bridge(monkeypatch)
    loop = aged(s, Clock(), ages={1.0: 9.0, 2.0: None})
    loop.replies = {1.0: CHAIR, 2.0: WalkView(obstacles=(Obstacle("wet floor", (600, 300, 900, 550)),))}
    await loop.process(_frame(1.0))
    await loop.process(_frame(2.0))
    assert _spoken(rec) == ["Stop. chair in front. Move a little right.", "Stop. wet floor in front."]
    assert notices.urgent == [True, True]


async def test_a_frame_without_a_capture_time_is_stale(session):
    s, rec = session
    loop = make(s, [FAR], Clock(), frames=False)       # the real age: from the frame store's arrival time
    await loop.process(_frame(5))                       # never stored: its arrival is unknown
    assert _spoken(rec) == [] and loop.stale_streak == 1


def test_capture_age_from_server_arrival_and_phone_delay():
    import dataclasses

    from aira_live.frames import FrameStore

    store = FrameStore()

    @dataclasses.dataclass(frozen=True)
    class Captured:
        id: str
        jpeg_b64: str
        w: int
        h: int
        ts: float
        purpose: str
        capture_ts: float | None = None

    plain = _frame(1_700_000_000_000)
    early = Captured("a", TINY, 10, 10, 1_700_000_000_000, "walk", capture_ts=1_699_999_999_800)
    future = Captured("b", TINY, 10, 10, 1_700_000_000_000, "walk", capture_ts=1_700_000_000_500)
    for f in (plain, early, future):
        store.add(f)
    arrived = store._recent_frames[-1][0]
    assert walk.capture_age_s(store, plain, now=arrived + 1.0) == pytest.approx(1.0, abs=0.01)
    assert walk.capture_age_s(store, early, now=arrived + 1.0) == pytest.approx(1.2, abs=0.01)   # + phone encode time
    assert walk.capture_age_s(store, future, now=arrived + 1.0) is None                # capture after send
    assert walk.capture_age_s(store, _frame(1_700_000_000_000), now=arrived) is None   # never arrived here
    assert walk.capture_age_s(store, _frame(0), now=arrived) is None                   # no phone time at all


async def test_repeated_stale_looks_ask_him_for_a_fresh_view(session, monkeypatch):
    import json

    s, rec = session
    notices = _bridge(monkeypatch)
    loop = aged(s, Clock(), ages={1.0: 5.0, 2.0: 5.0, 3.0: 5.0, 4.0: 5.0})
    for ts in (1.0, 2.0, 3.0):
        await loop.process(_frame(ts))
    assert _spoken(rec) == ["Please slow down, I need a fresh look."]
    assert json.loads(notices[0].split(" (meaning: ")[0]) == {"action": "slow_down", "reason": "view_too_old",
                                                              "wait_for": "fresh_look"}
    await loop.process(_frame(4.0))                     # the count starts again: not said every tick
    assert len(_spoken(rec)) == 1 and loop.active


async def test_arrival_needs_a_fresh_view(session):
    s, rec = session
    clock = Clock(100.0)
    loop = aged(s, clock, ages={1.0: 3.0, 2.0: 3.0, 3.0: 3.0})
    loop.replies = {1.0: THERE, 2.0: THERE, 3.0: THERE}
    await loop.process(_frame(1.0))                     # 3 s old: "stop, hold still", the walk goes on
    assert loop.active and loop.ended is None
    assert _spoken(rec) == ["Stop. Hold still. Let me check you are at the fridge."]
    clock.t += 1.0
    await loop.process(_frame(2.0))                     # taken before he was told to hold still: not proof
    assert loop.active
    clock.t += 3.5
    await loop.process(_frame(3.0))                     # taken while he was holding still: he is there
    assert loop.ended == "arrived" and _spoken(rec)[-1].startswith("Stop. The fridge is right in front")


async def test_an_old_hold_still_no_longer_confirms(session):
    s, rec = session
    clock = Clock(100.0)
    loop = aged(s, clock, ages={1.0: 3.0, 2.0: 3.0})
    loop.replies = {1.0: THERE, 2.0: THERE}
    await loop.process(_frame(1.0))
    clock.t += walk.HOLD_MAX_S + 5                      # he may be walking again: a new hold, not an arrival
    await loop.process(_frame(2.0))
    assert loop.active and loop.hold_at == clock.t


async def test_a_fresh_arrival_ends_at_once(session):
    s, rec = session
    loop = aged(s, Clock(), ages={1.0: 1.0})
    loop.replies = {1.0: THERE}
    await loop.process(_frame(1.0))
    assert loop.ended == "arrived"


async def test_pipelines_two_looks_in_flight(session):
    s, rec = session
    started, release = [], asyncio.Event()

    async def slow_look(frame, target):
        started.append(frame.ts)
        await release.wait()
        return FAR

    loop = make(s, [FAR], Clock())
    loop._look = slow_look
    task = asyncio.create_task(loop.run())
    for _ in range(30):
        await asyncio.sleep(0)
    assert len(started) == 2                 # a second look started while the first is still running
    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
    await walk.stop_walk(s)
    await asyncio.sleep(0)
    assert task.done() or task.cancelled()


async def test_phone_boxes_fast_stop(session):
    s, rec = session
    clock = Clock()
    loop = make(s, [FAR], clock, frames=False)
    person = {"label": "person", "box": [500, 400, 950, 600]}
    assert await loop.on_phone_boxes([person])
    assert _spoken(rec) == ["Stop. person in front."]
    assert not await loop.on_phone_boxes([person])       # not again within 3 s
    clock.t += 3.5
    assert await loop.on_phone_boxes([person])
    assert not await loop.on_phone_boxes([{"label": "cup", "box": [100, 0, 200, 100]}])


async def test_walk_to_tool_starts_the_loop_and_walk_stop_ends_it(session, monkeypatch):
    s, rec = session

    async def no_memory(session, target):
        return None, None

    monkeypatch.setattr(walk, "remembered_place", no_memory)
    assert "walk_to" in NON_BLOCKING_TOOLS and "walk_stop" in TOOLS_BY_NAME
    out = await walk.walk_to("  the fridge ")
    assert out["active"] and out["target"] == "the fridge" and "the fridge" in out["say_en"] and "say" not in out
    loop = walk.active(s)
    assert loop is not None and loop.task is not None
    assert any(isinstance(m, RequestFrameMsg) and m.purpose == "walk" and m.continuous for m in rec.msgs)
    out = await walk.walk_stop()
    assert out["was_active"] and walk.active(s) is None and not loop.active
    assert (await walk.walk_to(""))["active"] is False


async def test_session_hooks_stop_the_walk(session):
    s, rec = session
    loop = make(s, [FAR], Clock(), frames=False)
    await walk.on_phone_boxes(s, type("M", (), {"objects": []})())
    await walk.stop_session(s)
    assert not loop.active and walk.active(s) is None
    await walk.stop_session(s)                                        # safe twice
