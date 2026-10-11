"""The motion gate: only changed frames pass, at most N per minute. Frames are generated with Pillow."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from aira_live.cameras.motion import MotionGate, mean_abs_diff, gray_small


def jpeg(color=(200, 200, 200), box=None, size=(320, 240)) -> bytes:
    img = Image.new("RGB", size, color)
    if box:
        ImageDraw.Draw(img).rectangle(box, fill=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_first_frame_always_passes():
    gate = MotionGate(clock=Clock())
    assert gate.allow(jpeg()) is True


def test_identical_frames_do_not_pass_again():
    gate = MotionGate(clock=Clock())
    assert gate.allow(jpeg()) is True
    assert gate.allow(jpeg()) is False
    assert gate.allow(jpeg()) is False


def test_a_changed_frame_passes():
    gate = MotionGate(clock=Clock())
    assert gate.allow(jpeg()) is True
    assert gate.allow(jpeg(box=(40, 40, 200, 200))) is True   # a dark object appeared


def test_tiny_sensor_noise_is_below_the_threshold():
    gate = MotionGate(clock=Clock())
    assert gate.allow(jpeg((200, 200, 200))) is True
    assert gate.allow(jpeg((203, 201, 200))) is False       # a few levels of brightness drift


def test_rate_limit_per_minute_then_window_slides():
    clock = Clock()
    gate = MotionGate(max_per_minute=2, clock=clock)
    frames = [jpeg(), jpeg(box=(0, 0, 160, 240)), jpeg(box=(160, 0, 320, 240)), jpeg()]
    assert gate.allow(frames[0]) is True
    clock.t += 5
    assert gate.allow(frames[1]) is True
    clock.t += 5
    assert gate.allow(frames[2]) is False   # changed, but over budget this minute
    clock.t += 55                            # the first frame's slot is now 65 s old
    assert gate.allow(frames[3]) is True


def test_a_frame_blocked_by_the_budget_still_becomes_the_reference():
    clock = Clock()
    gate = MotionGate(max_per_minute=1, clock=clock)
    assert gate.allow(jpeg()) is True
    assert gate.allow(jpeg(box=(0, 0, 160, 240))) is False   # over budget
    clock.t += 61
    assert gate.allow(jpeg(box=(0, 0, 160, 240))) is False   # unchanged since the blocked frame


def test_undecodable_bytes_never_pass_and_never_raise():
    gate = MotionGate(clock=Clock())
    assert gate.allow(b"\xff\xd8 not really a jpeg") is False
    assert gate.allow(jpeg()) is True


def test_mean_abs_diff_is_zero_for_the_same_frame_and_large_for_black_vs_white():
    a = gray_small(jpeg((255, 255, 255)))
    b = gray_small(jpeg((0, 0, 0)))
    assert mean_abs_diff(a, a) == 0.0
    assert mean_abs_diff(a, b) > 200


def test_reset_makes_the_next_frame_pass():
    gate = MotionGate(clock=Clock())
    gate.allow(jpeg())
    gate.reset()
    assert gate.allow(jpeg()) is True
