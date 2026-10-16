"""Low light (tools/light.py): in the dark the flashlight turns on and AIRA says so (it informs, it does not
ask); it turns off again when it is bright; the owner can override by voice and that holds the auto-on; an
unsupported torch is said once; the switch loop still asks, guides to a switch and never to a socket, and judges
the press by the brightness before and after. Everything is faked; no model, no network."""
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


# ---------- torch: on in the dark, off when bright, informing (never asking on the passive path) ----------
def torch_on_dark_count(rec):
    return [m.text for m in msgs(rec, TranscriptMsg)].count(line("light.torch_on_dark", "ta"))


async def test_dark_turns_the_torch_on_and_informs_once_per_episode(session, flags):
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))
    # auto-on + a spoken line, and no question
    assert msgs(rec, ConfirmMsg) == []
    assert [m.on for m in msgs(rec, TorchMsg)] == [True]
    assert msgs(rec, TranscriptMsg)[-1].text == line("light.torch_on_dark", "ta")
    assert light.state(s).torch_requested == "on"          # not on until the phone says so
    # still dark, the phone has not applied it yet: the command is held and the line is not repeated
    await light.on_light(s, report("dark", 10.0, "off"))
    assert [m.on for m in msgs(rec, TorchMsg)] == [True]
    assert torch_on_dark_count(rec) == 1
    # the phone reports it on, then it is bright, then dark again: a new episode informs once more
    await light.on_light(s, report("dim", 40.0, "on"))
    assert light.state(s).torch_requested is None
    await light.on_light(s, report("bright", 150.0, "on"))
    await light.on_light(s, report("dark", 9.0, "off"))
    assert torch_on_dark_count(rec) == 2


async def test_relight_is_quiet_after_the_phone_auto_off_while_still_dark(session, flags):
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))   # on + spoken
    await light.on_light(s, report("dark", 11.0, "on"))    # the phone applied it
    # the phone's own 5-minute auto-off fires while it is still dark (the torch is reported off again)
    await light.on_light(s, report("dark", 10.0, "off"))
    # it re-lights, but without a second spoken line (still the same dark episode)
    assert [m.on for m in msgs(rec, TorchMsg)] == [True, True]
    assert torch_on_dark_count(rec) == 1


async def test_owner_off_by_voice_holds_the_auto_on_until_it_is_not_dark(session, flags):
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))   # auto-on
    await light.on_light(s, report("dark", 11.0, "on"))    # the phone applied it
    await light.flashlight(False)                          # the owner turns it off by voice
    await light.on_light(s, report("dark", 10.0, "off"))   # the phone reports it off, still dark
    assert [m.on for m in msgs(rec, TorchMsg)] == [True, False]   # it stays off: the override holds
    # bright then dark again lifts the override, and auto-on resumes
    await light.on_light(s, report("bright", 150.0, "off"))
    await light.on_light(s, report("dark", 9.0, "off"))
    assert [m.on for m in msgs(rec, TorchMsg)] == [True, False, True]


async def test_unsupported_torch_is_said_once_and_not_commanded_again(session, flags):
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))         # auto-on tried
    assert [m.on for m in msgs(rec, TorchMsg)] == [True]
    await light.on_light(s, report("dark", 12.0, "unsupported")) # the phone cannot
    said = [m.text for m in msgs(rec, TranscriptMsg)]
    assert said.count(line("light.torch_unsupported", "ta")) == 1
    await light.on_light(s, report("dark", 11.0, "unsupported"))
    await light.on_light(s, report("bright", 150.0, "unsupported"))
    await light.on_light(s, report("dark", 9.0, "unsupported"))
    said = [m.text for m in msgs(rec, TranscriptMsg)]
    assert said.count(line("light.torch_unsupported", "ta")) == 1
    assert [m.on for m in msgs(rec, TorchMsg)] == [True]         # never commanded again in this session
    assert msgs(rec, ConfirmMsg) == []


async def test_bright_again_sustained_turns_it_off_and_says_so(session, flags):
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))   # auto-on
    # dim is not bright, even at a high raw-luma spike: no turn-off (the decision ignores the noisy raw luma)
    await light.on_light(s, report("dim", 240.0, "on"))    # the phone applied it; dim level
    before = len(msgs(rec, TorchMsg))                       # one command so far (the on)
    await light.on_light(s, report("bright", 95.0, "on"))   # bright level: the timer starts (low raw luma, ignored)
    s.clock.t += light.TORCH_OFF_BRIGHT_S - 1
    await light.on_light(s, report("bright", 240.0, "on"))  # still bright, not long enough yet
    assert len(msgs(rec, TorchMsg)) == before
    s.clock.t += 2
    await light.on_light(s, report("bright", 110.0, "on"))  # sustained bright: auto-off + inform, no question
    assert [m.on for m in msgs(rec, TorchMsg)] == [True, False]
    assert msgs(rec, TranscriptMsg)[-1].text == line("light.torch_off_bright", "ta")
    assert msgs(rec, ConfirmMsg) == []
    # not commanded twice in the same torch-on episode
    await light.on_light(s, report("bright", 240.0, "on"))
    assert [m.on for m in msgs(rec, TorchMsg)] == [True, False]


async def test_a_dip_out_of_bright_resets_the_turn_off_timer(session, flags):
    # The real-world fix: the raw luma swings wildly, but the level is hysteresis-smoothed, so the turn-off rides
    # the level. A real drop out of "bright" restarts the timer; it only turns off after a sustained bright stretch.
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))   # auto-on
    await light.on_light(s, report("bright", 150.0, "on"))  # the phone applied it + bright: the timer starts
    s.clock.t += light.TORCH_OFF_BRIGHT_S - 1
    await light.on_light(s, report("dim", 80.0, "on"))     # a real dip out of bright resets the timer
    s.clock.t += 2
    await light.on_light(s, report("bright", 150.0, "on"))  # bright again, but the clock restarted
    assert [m.on for m in msgs(rec, TorchMsg)] == [True]    # not off yet
    s.clock.t += light.TORCH_OFF_BRIGHT_S
    await light.on_light(s, report("bright", 150.0, "on"))  # now sustained from the restart
    assert [m.on for m in msgs(rec, TorchMsg)] == [True, False]


async def test_auto_off_still_fires_after_the_owner_turns_it_on_manually(session, flags):
    # Regression: a spoken "turn it on" while the torch is already on left torch_requested stuck at "on" (the
    # phone's next report is not a state change, so the old code never cleared it), and that blocked the
    # automatic turn-off. The request now clears as soon as the phone confirms the state.
    s, rec = session
    await light.on_light(s, report("dark", 12.0, "off"))   # auto-on
    await light.on_light(s, report("dark", 11.0, "on"))    # the phone applied it (a real change clears the flag)
    await light.flashlight(True)                           # the owner also says "turn it on" (already on)
    await light.on_light(s, report("dark", 11.0, "on"))    # no state change, but the flag must still clear here
    assert light.state(s).torch_requested is None
    await light.on_light(s, report("bright", 150.0, "on"))  # a sustained bright now turns it off automatically
    s.clock.t += light.TORCH_OFF_BRIGHT_S + 1
    await light.on_light(s, report("bright", 150.0, "on"))
    assert [m.on for m in msgs(rec, TorchMsg)] == [True, True, False]  # auto-on, the owner's on, then auto-off


async def test_off_offered_resets_each_episode_so_the_auto_off_fires_every_time(session, flags):
    # Regression (turned off the first time, not the second): after the auto-off, the owner also said "turn it
    # off" while the torch was already off; that set off_offered, and because no torch->off change followed it,
    # the flag stuck and carried into the next torch-on episode, blocking that episode's auto-off. off_offered now
    # resets when the torch turns on, so every episode gets a fresh auto-off.
    s, rec = session
    # episode 1: auto-on, bright, auto-off, the phone reports off
    await light.on_light(s, report("dark", 12.0, "off"))
    await light.on_light(s, report("bright", 150.0, "on"))
    s.clock.t += light.TORCH_OFF_BRIGHT_S + 1
    await light.on_light(s, report("bright", 150.0, "on"))   # auto-off
    await light.on_light(s, report("bright", 150.0, "off"))   # the phone applied the off
    # the owner then also says "turn it off" while it is already off (no torch change follows to clear the flag)
    await light.flashlight(False)
    await light.on_light(s, report("bright", 150.0, "off"))
    # episode 2: the view goes dark again -> auto-on -> bright -> the auto-off must fire again
    await light.on_light(s, report("dark", 9.0, "off"))     # auto-on (episode 2)
    await light.on_light(s, report("bright", 150.0, "on"))   # applied + bright; a fresh episode clears off_offered
    assert light.state(s).off_offered is False
    s.clock.t += light.TORCH_OFF_BRIGHT_S + 1
    await light.on_light(s, report("bright", 150.0, "on"))
    assert msgs(rec, TorchMsg)[-1].on is False               # the second episode turned off automatically


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


async def test_a_pending_offer_is_answered_by_his_own_request(session, flags, monkeypatch):
    # The passive dark path no longer asks, but the switch flow still proposes the torch first: the owner's own
    # flashlight request answers that pending offer.
    s, rec = session
    monkeypatch.setitem(sys.modules, light.ROUTE_MODULE, None)
    monkeypatch.setattr(light.SwitchLoop, "run", _idle_run)
    light.state(s).level, light.state(s).luma = "dark", 10.0
    await light.light_switch_find()
    assert light.state(s).pending
    await light.flashlight(on=False)
    assert light.state(s).pending == {}


def test_the_flashlight_tool_is_listed_only_with_aira_torch():
    from aira_live.tools import active_tools
    assert "flashlight" in {f.__name__ for f in active_tools(False, torch=True)}
    assert "flashlight" not in {f.__name__ for f in active_tools(False)}
