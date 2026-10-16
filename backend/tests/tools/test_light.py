"""Low light (tools/light.py): the flashlight is proposed, never commanded without a spoken yes; an unsupported
torch is said once; bright again asks before turning off; the switch loop guides to a switch and never to a
socket; the press is judged by the brightness before and after. Everything is faked; no model, no network."""
import sys
import types

import pytest

from aira_live import confirm
from aira_live.contracts import (
    ConfirmMsg, CueMsg, LightMsg, RequestFrameMsg, TargetMsg, TorchMsg, TranscriptMsg,
)
from aira_live.session import current_session
from aira_live.system_lines import line
from aira_live.tools import light
from aira_live.vision.switches import BoardDetection, SwitchItem

SW1 = SwitchItem("switch", (320, 420, 380, 480))
SW2 = SwitchItem("switch", (320, 500, 380, 560))
SOCKET_NEXT = SwitchItem("socket", (320, 560, 380, 620))


def board(*items, conf=0.9) -> BoardDetection:
    return BoardDetection(board=(300, 400, 500, 700), items=tuple(items), confidence=conf)


class Clock:
    def __init__(self, t0: float = 1000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def session(mk_session, monkeypatch):
    s, rec = mk_session(lang="ta")
    token = current_session.set(s)
    confirm.set_store(confirm.MemoryProposalStore())
    clock = Clock()
    monkeypatch.setattr(light, "_monotonic", clock)
    s.clock = clock
    yield s, rec
    current_session.reset(token)
    light._STATE.clear()
    light._LOOPS.clear()


@pytest.fixture
def flags(monkeypatch):
    monkeypatch.setenv("AIRA_TORCH", "1")
    monkeypatch.setenv("AIRA_LOWLIGHT", "1")


def msgs(rec, kind):
    return [m for m in rec.msgs if isinstance(m, kind)]


def report(level="dark", luma=20.0, torch="off") -> LightMsg:
    return LightMsg(t="light", level=level, luma=luma, torch=torch)


# ---------- flags off: nothing but a record ----------
async def test_flags_off_records_the_report_and_proposes_nothing(session, monkeypatch):
    monkeypatch.delenv("AIRA_TORCH", raising=False)
    s, rec = session
    await light.on_light(s, report("dark", 5.0, "off"))
    await light.on_light(s, report("dark", 5.0, "off"))
    st = light.state(s)
    assert st.level == "dark" and st.luma == 5.0 and st.torch == "off"
    assert msgs(rec, ConfirmMsg) == [] and msgs(rec, TorchMsg) == [] and msgs(rec, TranscriptMsg) == []


async def test_light_switch_tool_is_registered_only_with_the_flag():
    from aira_live.tools import active_tools

    assert "light_switch_find" not in {f.__name__ for f in active_tools()}
    assert "light_switch_find" in {f.__name__ for f in active_tools(light=True)}


# ---------- torch: proposed once per dark episode, commanded only after yes ----------
async def test_dark_proposes_the_torch_once_and_no_command_goes_without_a_yes(session, flags):
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))
    await light.on_light(s, report("dark", 10.0, "off"))   # still dark: no second question
    confirms = msgs(rec, ConfirmMsg)
    assert len(confirms) == 1 and confirms[0].kind == "generic" and confirms[0].prompt == light.TORCH_OFFER_EN
    assert msgs(rec, TorchMsg) == []
    # without a Live bridge the phone captions the fixed Tamil line
    assert msgs(rec, TranscriptMsg)[-1].text == line("light.torch_offer", "ta")
    # "no" never commands; and the same dark episode is not asked again
    r = await confirm.resolve(confirms[0].id, "no")
    assert r["status"] == "rejected" and msgs(rec, TorchMsg) == []
    await light.on_light(s, report("dark", 8.0, "off"))
    assert len(msgs(rec, ConfirmMsg)) == 1
    # bright again, then dark again: a new episode, one new question
    await light.on_light(s, report("bright", 150.0, "off"))
    await light.on_light(s, report("dark", 9.0, "off"))
    assert len(msgs(rec, ConfirmMsg)) == 2


async def test_yes_sends_the_torch_command_and_the_phone_report_is_the_truth(session, flags):
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))
    cid = msgs(rec, ConfirmMsg)[0].id
    r = await confirm.resolve(cid, "yes")
    assert r["status"] == "committed" and r["say_en"] == light.TORCH_ON_EN
    assert [m.on for m in msgs(rec, TorchMsg)] == [True]
    st = light.state(s)
    assert st.torch == "off" and st.torch_requested == "on"   # not on until the phone says so
    await light.on_light(s, report("dim", 70.0, "on"))
    assert st.torch == "on" and st.torch_requested is None
    # a yes twice commits once: one command only
    await confirm.resolve(cid, "yes")
    assert len(msgs(rec, TorchMsg)) == 1


async def test_unsupported_torch_is_said_once_and_never_offered_again(session, flags):
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))
    await confirm.resolve(msgs(rec, ConfirmMsg)[0].id, "yes")
    await light.on_light(s, report("dark", 12.0, "unsupported"))
    said = [m.text for m in msgs(rec, TranscriptMsg)]
    assert said.count(line("light.torch_unsupported", "ta")) == 1
    await light.on_light(s, report("dark", 11.0, "unsupported"))
    await light.on_light(s, report("bright", 150.0, "unsupported"))
    await light.on_light(s, report("dark", 9.0, "unsupported"))
    said = [m.text for m in msgs(rec, TranscriptMsg)]
    assert said.count(line("light.torch_unsupported", "ta")) == 1
    assert len(msgs(rec, ConfirmMsg)) == 1   # never offered again in this session


async def test_bright_again_sustained_asks_before_turning_off(session, flags):
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))
    await confirm.resolve(msgs(rec, ConfirmMsg)[0].id, "yes")
    await light.on_light(s, report("dim", 70.0, "on"))
    # the torch itself lighting a shelf: bright but under the bar, or not long enough: no question
    await light.on_light(s, report("bright", 100.0, "on"))
    s.clock.t += 60
    await light.on_light(s, report("bright", 100.0, "on"))
    assert len(msgs(rec, ConfirmMsg)) == 1
    await light.on_light(s, report("bright", 150.0, "on"))
    s.clock.t += light.TORCH_OFF_BRIGHT_S - 1
    await light.on_light(s, report("bright", 150.0, "on"))
    assert len(msgs(rec, ConfirmMsg)) == 1
    s.clock.t += 2
    await light.on_light(s, report("bright", 150.0, "on"))
    confirms = msgs(rec, ConfirmMsg)
    assert len(confirms) == 2 and confirms[1].prompt == light.TORCH_OFF_OFFER_EN
    assert [m.on for m in msgs(rec, TorchMsg)] == [True]   # still on: no command without the yes
    await light.on_light(s, report("bright", 150.0, "on"))
    assert len(msgs(rec, ConfirmMsg)) == 2                  # asked once per torch-on episode
    r = await confirm.resolve(confirms[1].id, "yes")
    assert r["say_en"] == light.TORCH_OFF_EN and [m.on for m in msgs(rec, TorchMsg)] == [True, False]


# ---------- the light switch tool ----------
async def test_switch_find_delegates_to_a_route_follower_when_one_is_importable(session, flags, monkeypatch):
    s, rec = session
    calls = []

    async def route_to(sess, place):
        calls.append((sess.session_id, place))
        return {"active": True, "target": place, "say_en": "Walking to the switch board."}

    monkeypatch.setitem(sys.modules, light.ROUTE_MODULE, types.SimpleNamespace(route_to=route_to))
    out = await light.light_switch_find()
    assert calls == [(s.session_id, light.ROUTE_PLACE)]
    assert out["routed"] is True and out["then"] == "light_switch_find" and out["say_en"] == "Walking to the switch board."
    assert msgs(rec, RequestFrameMsg) == []   # the camera loop starts only once the route says so


async def test_switch_find_without_a_map_starts_the_camera_loop(session, flags, monkeypatch):
    s, rec = session
    monkeypatch.setitem(sys.modules, light.ROUTE_MODULE, None)   # import fails: no Part A on this branch
    monkeypatch.setattr(light.SwitchLoop, "run", _idle_run)
    out = await light.light_switch_find()
    assert out["active"] is True and out["say_en"] == light.SWITCH_START_EN
    req = msgs(rec, RequestFrameMsg)
    assert req[-1].purpose == "guide" and req[-1].continuous is True
    assert light.active(s) is not None
    await light.stop_session(s)
    assert light.active(s) is None and msgs(rec, RequestFrameMsg)[-1].stop is True


async def test_switch_find_in_the_dark_offers_the_torch_first_and_starts_after_the_report(session, flags, monkeypatch):
    s, rec = session
    monkeypatch.setitem(sys.modules, light.ROUTE_MODULE, None)
    monkeypatch.setattr(light.SwitchLoop, "run", _idle_run)
    light.state(s).level, light.state(s).luma = "dark", 10.0
    out = await light.light_switch_find()
    assert out["needs_confirm"] is True and out["then"] == "light_switch_find"
    assert light.active(s) is None and msgs(rec, TorchMsg) == []
    await confirm.resolve(out["confirm_id"], "yes")
    assert msgs(rec, TorchMsg)[-1].on is True and light.active(s) is None
    await light.on_light(s, report("dim", 60.0, "on"))     # the phone's report starts the switch loop
    assert light.active(s) is not None


async def _idle_run(self):
    return None


# ---------- the switch loop: code decides, never toward a socket ----------
async def test_loop_sends_a_target_for_a_switch_and_refuses_when_unsure(session, flags):
    s, rec = session
    loop = await light.start_switch(s, run=False)
    d = await loop.on_detection(board(SW1, SW2), "f1")
    t = msgs(rec, TargetMsg)[-1]
    assert d.kind == "guide" and t.point == SW1.centre and t.label == d.name and t.frame_id == "f1"
    # the same board with a socket next to the second switch, asked for the second switch: no target, stop
    loop.index = 1
    before = len(msgs(rec, TargetMsg))
    d = await loop.on_detection(board(SW1, SW2, SOCKET_NEXT), "f2")
    assert d.kind == "unsure" and len(msgs(rec, TargetMsg)) == before
    assert loop.active is False and light.active(s) is None
    assert msgs(rec, RequestFrameMsg)[-1].stop is True
    assert msgs(rec, TranscriptMsg)[-1].text == line("light.not_sure", "ta")


async def test_loop_stops_when_the_model_cannot_tell_a_switch_from_a_socket(session, flags):
    s, rec = session
    loop = await light.start_switch(s, run=False)
    unknown = SwitchItem("unknown", (320, 500, 380, 560))
    d = await loop.on_detection(board(SW1, unknown), "f1")
    assert d.kind == "unsure" and msgs(rec, TargetMsg) == [] and loop.active is False
    assert msgs(rec, CueMsg)[-1].cue == "unsure"


async def test_loop_says_not_sure_for_a_too_dark_low_confidence_view(session, flags):
    s, rec = session
    loop = await light.start_switch(s, run=False)
    d = await loop.on_detection(board(SW1, conf=0.3), "f1")
    assert d.kind == "unsure" and d.reason == "low_confidence" and msgs(rec, TargetMsg) == []
    assert loop.active is False and msgs(rec, TranscriptMsg)[-1].text == line("light.not_sure", "ta")


async def test_a_board_out_of_view_is_said_once_after_three_misses_and_guidance_goes_on(session, flags):
    s, rec = session
    loop = await light.start_switch(s, run=False)
    for i in range(3):
        await loop.on_detection(BoardDetection(None, (), 0.0), f"f{i}")
    said = [m.text for m in msgs(rec, TranscriptMsg)]
    assert said.count(line("light.no_board", "ta")) == 1 and loop.active is True
    await loop.on_detection(BoardDetection(None, (), 0.0), "f4")
    assert [m.text for m in msgs(rec, TranscriptMsg)].count(line("light.no_board", "ta")) == 1


async def test_press_then_brightness_rose_means_the_light_is_on_and_offers_the_torch_off(session, flags):
    s, rec = session
    st = light.state(s)
    st.luma, st.level, st.torch = 20.0, "dark", "on"
    loop = await light.start_switch(s, run=False, press_window_s=0.01)
    await loop.on_detection(board(SW1, SW2), "f1")
    await light.handle_client_event(s, "fingertip_in_target", {"frameId": "f1"})
    assert loop.stage == "press" and loop.press_luma == 20.0
    assert msgs(rec, TranscriptMsg)[-1].text == line("light.switch_press", "ta", name=loop.decision.name)
    assert light.brightness_rose(20.0, 20.0 + light.BRIGHT_RISE_LUMA) and not light.brightness_rose(20.0, 40.0)
    assert not light.brightness_rose(None, 90.0)
    await light.on_light(s, report("dim", 45.0, "on"))     # a small change: still waiting
    assert loop.stage == "press"
    await light.on_light(s, report("bright", 90.0, "on"))  # +70: the light came on
    said = [m.text for m in msgs(rec, TranscriptMsg)]
    assert line("light.on_now", "ta") in said and loop.active is False and light.active(s) is None
    off = [c for c in msgs(rec, ConfirmMsg) if c.prompt == light.TORCH_OFF_OFFER_EN]
    assert len(off) == 1 and msgs(rec, TorchMsg) == []   # offered, not switched off


async def test_press_with_no_rise_says_still_dark_and_moves_to_the_next_switch(session, flags):
    s, rec = session
    st = light.state(s)
    st.luma, st.level = 20.0, "dark"
    loop = await light.start_switch(s, run=False)
    await loop.on_detection(board(SW1, SW2), "f1")
    assert (await loop.on_touch())["stage"] == "press"
    await light.on_light(s, report("dark", 25.0, "off"))   # +5: not a light
    assert loop.stage == "press"
    await loop.on_press_timeout()
    assert msgs(rec, TranscriptMsg)[-1].text == line("light.still_dark", "ta")
    assert loop.stage == "seek" and loop.index == 1 and loop.active is True
    d = await loop.on_detection(board(SW1, SW2), "f2")
    assert d.point == SW2.centre and msgs(rec, TargetMsg)[-1].point == SW2.centre
    # the last switch pressed without a rise: no more switches, guidance ends
    await loop.on_touch()
    await loop.on_press_timeout()
    assert loop.active is False and light.active(s) is None
    assert msgs(rec, TranscriptMsg)[-1].text == line("light.still_dark", "ta")


async def test_a_touch_outside_a_switch_loop_or_before_a_target_is_ignored(session, flags):
    s, rec = session
    await light.handle_client_event(s, "fingertip_in_target", {})   # no loop
    loop = await light.start_switch(s, run=False)
    assert await loop.on_touch() is None                               # no target yet
    await light.handle_client_event(s, "talk", {})
    assert msgs(rec, TranscriptMsg) == [] and loop.stage == "seek"


async def test_stop_and_forget_clear_the_session(session, flags):
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))
    await light.start_switch(s, run=False)
    await light.stop_session(s)
    light.forget(s.session_id)
    assert light.active(s) is None and s.session_id not in light._STATE
    await light.stop_session(s)   # twice is fine


# ---------- the owner's own "flashlight on/off" by voice ----------
async def test_owner_says_turn_off_and_it_goes_off_at_once_without_a_yes(session, flags):
    s, rec = session
    await light.on_light(s, report(level="dark", luma=20.0, torch="on"))   # it is on (after an earlier yes)
    out = await light.flashlight(on=False)
    assert out["on"] is False and out["say_en"] == light.TORCH_OFF_EN
    assert [m.on for m in msgs(rec, TorchMsg)] == [False]                 # no confirm needed to turn it off
    assert light.state(s).off_offered is True                            # and no "shall I turn it off?" later


async def test_owner_says_turn_on_and_it_comes_on_or_says_it_cannot(session, flags):
    s, rec = session
    out = await light.flashlight(on=True)
    assert out["on"] is True and [m.on for m in msgs(rec, TorchMsg)] == [True]   # his request is the consent
    light.state(s).torch = "unsupported"
    out = await light.flashlight(on=True)
    assert out["torch"] == "unsupported" and out["say_en"] == light.TORCH_UNSUPPORTED_EN
    assert len(msgs(rec, TorchMsg)) == 1                                  # never pretends: no second command


async def test_a_pending_offer_is_answered_by_his_own_request(session, flags):
    s, rec = session
    await light.on_light(s, report())
    s.clock.t += 5
    await light.on_light(s, report())                                     # sustained dark: the offer is pending
    assert light.state(s).pending
    await light.flashlight(on=False)
    assert light.state(s).pending == {}


def test_the_flashlight_tool_is_listed_only_with_aira_torch():
    from aira_live.tools import active_tools
    assert "flashlight" in {f.__name__ for f in active_tools(False, torch=True)}
    assert "flashlight" not in {f.__name__ for f in active_tools(False)}
