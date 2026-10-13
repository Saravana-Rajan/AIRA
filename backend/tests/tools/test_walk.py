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


def _bridge(monkeypatch):
    notices: list[str] = []

    class Bridge:
        _notices: dict = {}

        async def speak_notice(self, text):
            notices.append(text)

    monkeypatch.setattr(walk, "_bridge_of", lambda s: Bridge())
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
    await loop.process(_frame(5))
    await loop.process(_frame(3))          # an older capture finishing later: ignored
    assert loop.acted_ts == 5 and _spoken(rec) == ["fridge ahead, about 3 steps."]


async def _ret(v):
    return v


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
