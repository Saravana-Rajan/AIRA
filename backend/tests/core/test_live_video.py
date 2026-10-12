"""Live video: 1 fps camera frames into Gemini Live, the scene-change nudge gate, the newest-frame cache."""
import base64
from types import SimpleNamespace

import pytest
from live_fakes import FakeBackend

from aira_live import config, live_video, main, tool_runtime
from aira_live import frames as frames_mod
from aira_live.frames import Frame
from aira_live.live_bridge import AdkLiveBackend, LiveBridge
from aira_live.live_video import (MIN_GAP_S, NUDGE_DEDUPE_S, NUDGE_GAP_S, NUDGE_PREFIX, LiveVideo, labels_key,
                                  narrates)
from aira_live.prompt import RULES

JPEG = base64.b64encode(b"\xff\xd8jpeg").decode()


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


async def _bridge(s, nudge=True, clock=None):
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [], live_nudge=nudge)
    clock = clock or Clock()
    bridge.video._clock, bridge.video._window_at = clock, clock.t
    await bridge.start()
    return bridge, b, clock


def _frame(ts=1.0, purpose="live"):
    return Frame(id=f"f{ts}", jpeg_b64=JPEG, w=768, h=432, ts=ts, purpose=purpose)


# ---------- forwarding + throttle ----------
async def test_frames_go_into_live_as_jpeg_at_most_once_a_second(mk_session):
    s, _ = mk_session()
    bridge, b, clock = await _bridge(s)
    assert await bridge.video.offer(bridge, JPEG)
    clock.t += 0.3
    assert not await bridge.video.offer(bridge, JPEG)  # too soon: dropped
    clock.t += MIN_GAP_S
    assert await bridge.video.offer(bridge, JPEG)
    assert b.sent_video == [b"\xff\xd8jpeg", b"\xff\xd8jpeg"]
    assert (bridge.video.forwarded, bridge.video.dropped) == (2, 1)
    await bridge.close()


async def test_frames_are_dropped_while_the_live_queue_is_behind_or_closed(mk_session):
    s, _ = mk_session()
    bridge, b, clock = await _bridge(s)
    b.queued = live_video.MAX_BACKLOG + 1
    assert not await bridge.video.offer(bridge, JPEG)
    b.queued = 0
    assert not await bridge.video.offer(bridge, "%%% not base64")
    await bridge.close()
    clock.t += 5
    assert not await bridge.video.offer(bridge, JPEG)
    assert b.sent_video == []


async def test_adk_backend_sends_a_realtime_jpeg_blob():
    sent = []
    be = AdkLiveBackend(SimpleNamespace(uid="u"))
    be._queue = SimpleNamespace(send_realtime=sent.append, _queue=SimpleNamespace(qsize=lambda: 3))
    await be.send_video(b"jpg")
    assert sent[0].mime_type == "image/jpeg" and sent[0].data == b"jpg"
    assert be.backlog() == 3


async def test_counts_are_logged_once_a_minute_without_content(mk_session, monkeypatch):
    lines = []
    monkeypatch.setattr(live_video, "diag", lambda kind, **kw: lines.append((kind, kw)))
    s, _ = mk_session()
    bridge, _, clock = await _bridge(s)
    await bridge.video.offer(bridge, JPEG)
    await bridge.video.offer(bridge, JPEG)
    assert lines == []
    clock.t += 61
    await bridge.video.offer(bridge, JPEG)
    assert len(lines) == 1 and lines[0][0] == "live_video"
    kw = lines[0][1]
    assert kw["forwarded"] == 2 and kw["dropped"] == 1 and JPEG not in str(kw)
    await bridge.close()


# ---------- main: purpose "live" ----------
async def test_main_forwards_live_frames_caches_them_and_never_stores_them_for_tools(mk_session):
    s, _ = mk_session()
    bridge, b, _ = await _bridge(s)
    assert await main.live_frame(s, bridge, _frame(), video=True)
    assert b.sent_video and s.frames.latest(5) == []
    assert s.latest_frame(1.0).id == "f1.0"
    await bridge.close()


async def test_flag_off_keeps_frames_out_of_live(mk_session, monkeypatch):
    s, _ = mk_session()
    bridge, b, _ = await _bridge(s)
    assert not await main.live_frame(s, bridge, _frame(), video=False)
    assert not await main.live_frame(s, None, _frame(2.0), video=True)  # mock mode: no bridge
    assert b.sent_video == []
    monkeypatch.setenv("AIRA_LIVE_VIDEO", "0")
    monkeypatch.setenv("AIRA_LIVE_NUDGE", "0")
    assert config.load().live_video is False and config.load().live_nudge is False
    monkeypatch.delenv("AIRA_LIVE_VIDEO")
    monkeypatch.delenv("AIRA_LIVE_NUDGE")
    assert config.load().live_video is True and config.load().live_nudge is True
    await bridge.close()


# ---------- the newest-frame cache ----------
async def test_latest_frame_is_fresh_only_and_look_frame_reuses_it(mk_session, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(frames_mod, "time", SimpleNamespace(monotonic=clock))
    s, _ = mk_session()
    s.frames.remember_recent(_frame(5.0))
    assert s.latest_frame(1.0).ts == 5.0
    assert (await s.look_frame("ask"))[0].ts == 5.0  # 768 px live frame: no request to the phone
    clock.t += 0.8
    assert s.latest_frame(1.0) is not None
    clock.t += 0.5
    assert s.latest_frame(1.0) is None and s.latest_frame(2.0) is not None


# ---------- the nudge ----------
def _nudges(b):
    return [t for t in b.sent_text if t.startswith(NUDGE_PREFIX)]


async def test_a_settled_change_nudges_live_in_the_session_language(mk_session):
    s, _ = mk_session()
    s.lang = "ta"
    bridge, b, _ = await _bridge(s)
    await bridge.video.offer(bridge, JPEG, changed=True, labels=["Bottle", "cup"])
    [text] = _nudges(b)
    assert "Tamil" in text and "bottle, cup" in text and "stay silent" in text
    await bridge.close()


async def test_nudge_rate_limit_and_same_labels_dedupe(mk_session):
    s, _ = mk_session()
    bridge, b, clock = await _bridge(s)
    await bridge.video.offer(bridge, JPEG, changed=True, labels=["bottle"])
    clock.t += 1.0
    await bridge.video.offer(bridge, JPEG, changed=True, labels=["cup"])        # < 4 s: rate limited
    assert len(_nudges(b)) == 1
    clock.t += NUDGE_GAP_S
    await bridge.video.offer(bridge, JPEG, changed=True, labels=["bottle"])     # same labels within 15 s
    assert len(_nudges(b)) == 1
    await bridge.video.offer(bridge, JPEG, changed=True, labels=["cup"])        # throttled frame: no nudge
    clock.t += 1.0
    await bridge.video.offer(bridge, JPEG, changed=True, labels=["cup"])
    assert len(_nudges(b)) == 2
    clock.t += NUDGE_DEDUPE_S
    await bridge.video.offer(bridge, JPEG, changed=True, labels=["bottle"])
    assert len(_nudges(b)) == 3
    await bridge.video.offer(bridge, JPEG)  # unchanged frames never nudge
    assert len(_nudges(b)) == 3
    await bridge.close()


@pytest.mark.parametrize("state, reason", [
    ("answering", "aira_speaking"), ("partial", "user_speaking"), ("just_spoke", "just_spoke"),
    ("tool", "tool_running"), ("ambient", "other_voice"), ("off", "off")])
async def test_no_nudge_while_anyone_speaks_or_a_tool_runs(mk_session, monkeypatch, state, reason):
    s, _ = mk_session()
    bridge, b, clock = await _bridge(s, nudge=state != "off")
    if state == "answering":
        bridge._answering = True
    elif state == "partial":
        bridge._in_partial = "how much is"
    elif state == "just_spoke":
        bridge.last_speech_at = clock.t - 0.5
    elif state == "tool":
        monkeypatch.setitem(tool_runtime._RUNNING, s.session_id, 1)
    elif state == "ambient":
        bridge.video._other_busy = lambda: True
    assert bridge.video.nudge_block(bridge, ["bottle"], clock.t) == reason
    await bridge.video.offer(bridge, JPEG, changed=True, labels=["bottle"])
    assert _nudges(b) == [] and b.sent_video  # the frame still reaches Live
    await bridge.close()


async def test_tool_runtime_counts_running_calls(mk_session):
    s, _ = mk_session()
    seen = []

    async def probe() -> dict:
        seen.append(tool_runtime.tools_running(s.session_id))
        return {"ok": True}

    await tool_runtime.wrap_tool(probe, s)()
    assert seen == [1] and tool_runtime.tools_running(s.session_id) == 0


async def test_live_narration_silences_the_er_scene_loop(mk_session):
    s, _ = mk_session()
    on, _, _ = await _bridge(s, nudge=True)
    off, _, _ = await _bridge(s, nudge=False)
    assert narrates(on) and not narrates(off) and not narrates(None)
    await on.close()
    await off.close()


def test_labels_key_is_order_and_case_free():
    assert labels_key(["Cup", " bottle ", "cup"]) == "bottle,cup" and labels_key(None) == ""


def test_prompt_lets_live_see_but_keeps_numbers_on_tools():
    p = "\n".join(RULES).lower()
    assert "live video" in p and "hold still or step back" in p
    assert "never state a count, a price read from a label, an amount of money, or an expiry date" in p
    assert "never invent counts or amounts" in p and "[system note] camera:" in p


async def test_a_walk_silences_nudges_precounts_and_scene_lines(mk_session, monkeypatch):
    from aira_live.tools import describe, walk
    from aira_live.vision import precount

    s, _ = mk_session()
    bridge, b, clock = await _bridge(s)
    monkeypatch.setitem(walk._ACTIVE, s.session_id, object())
    assert s.walking and describe._bridge_busy(s)
    assert bridge.video.nudge_block(bridge, ["bottle"], clock.t) == "other_voice"
    started = []
    monkeypatch.setattr(precount, "scene_changed", lambda *a, **k: started.append(a))
    await main.live_frame(s, bridge, _frame(), video=True, changed=True, precount_on=True)
    assert started == [] and _nudges(b) == [] and b.sent_video
    monkeypatch.delitem(walk._ACTIVE, s.session_id)
    assert not s.walking
    await bridge.close()
