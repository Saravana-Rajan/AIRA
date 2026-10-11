"""Camera config parsing: the ``cameras`` list in config/runtime. Off by default: an empty list means nothing runs."""
from __future__ import annotations

import pytest

from aira_live.cameras import config as cc


def test_default_is_empty_so_nothing_runs():
    assert cc.parse_cameras(None) == []
    assert cc.parse_cameras([]) == []
    assert cc.parse_cameras("not a list") == []


def test_parses_a_full_entry():
    cams = cc.parse_cameras([{
        "name": "Front-Door", "source": "rtsp", "urlEnv": "CAMERA_FRONT_URL",
        "modes": ["delivery", "security"], "intervalS": 10, "enabled": True, "maxPerMinute": 3,
    }])
    assert len(cams) == 1
    cam = cams[0]
    assert cam.name == "front-door"  # lower-cased, safe for env names and event payloads
    assert cam.source == "rtsp"
    assert cam.url_env == "CAMERA_FRONT_URL"
    assert cam.modes == ("delivery", "security")
    assert cam.interval_s == 10.0
    assert cam.enabled is True
    assert cam.max_per_minute == 3
    assert cam.shop_id == "sunrise_dairy"


def test_defaults_fill_in():
    cam = cc.parse_cameras([{"name": "shelf1", "source": "phone", "modes": ["shelf"]}])[0]
    assert cam.interval_s == cc.DEFAULT_INTERVAL_S
    assert cam.max_per_minute == cc.DEFAULT_MAX_PER_MINUTE
    assert cam.url_env is None  # a phone has no URL
    assert cam.enabled is True


def test_rtsp_url_env_defaults_to_camera_name_url():
    cam = cc.parse_cameras([{"name": "back yard", "source": "rtsp", "modes": ["security"]}])[0]
    assert cam.name == "back-yard"
    assert cam.url_env == "CAMERA_BACK_YARD_URL"


@pytest.mark.parametrize("entry", [
    {"source": "rtsp", "modes": ["delivery"]},                          # no name
    {"name": "x", "source": "usb", "modes": ["delivery"]},              # unknown source
    {"name": "x", "source": "rtsp", "modes": []},                       # no modes
    {"name": "x", "source": "rtsp", "modes": ["teleport"]},             # only unknown modes
    {"name": "x", "source": "rtsp", "modes": ["delivery"], "enabled": False},
    {"name": "../", "source": "rtsp", "modes": ["delivery"]},           # nothing safe left in the name
    "a string, not a dict",
])
def test_invalid_or_disabled_entries_are_skipped(entry):
    assert cc.parse_cameras([entry]) == []


def test_unknown_modes_are_dropped_but_known_ones_kept():
    cam = cc.parse_cameras([{"name": "a", "source": "phone", "modes": ["counter", "teleport", "shelf"]}])[0]
    assert cam.modes == ("counter", "shelf")


def test_interval_and_rate_are_clamped():
    cam = cc.parse_cameras([{"name": "a", "source": "phone", "modes": ["shelf"], "intervalS": 0, "maxPerMinute": 0}])[0]
    assert cam.interval_s == cc.MIN_INTERVAL_S
    assert cam.max_per_minute == 1
    cam = cc.parse_cameras([{"name": "a", "source": "phone", "modes": ["shelf"], "intervalS": 10_000}])[0]
    assert cam.interval_s == cc.MAX_INTERVAL_S


def test_one_bad_entry_does_not_lose_the_good_ones():
    cams = cc.parse_cameras([{"bad": 1}, {"name": "ok", "source": "phone", "modes": ["counter"]}])
    assert [c.name for c in cams] == ["ok"]


def test_duplicate_names_keep_the_first():
    cams = cc.parse_cameras([{"name": "a", "source": "phone", "modes": ["counter"]},
                             {"name": "A", "source": "rtsp", "modes": ["shelf"]}])
    assert len(cams) == 1 and cams[0].source == "phone"


def test_rtsp_url_comes_from_env_only(monkeypatch):
    cam = cc.parse_cameras([{"name": "front", "source": "rtsp", "modes": ["security"]}])[0]
    monkeypatch.delenv("CAMERA_FRONT_URL", raising=False)
    assert cc.rtsp_url(cam) is None
    monkeypatch.setenv("CAMERA_FRONT_URL", "rtsp://user:pw@10.0.0.5/stream")
    assert cc.rtsp_url(cam) == "rtsp://user:pw@10.0.0.5/stream"


def test_after_hours_window_parses_and_defaults():
    cam = cc.parse_cameras([{"name": "a", "source": "phone", "modes": ["security"]}])[0]
    assert cam.after_hours == cc.DEFAULT_AFTER_HOURS
    cam = cc.parse_cameras([{"name": "a", "source": "phone", "modes": ["security"],
                             "afterHours": {"start": "21:30", "end": "05:00"}}])[0]
    assert cam.after_hours == ("21:30", "05:00")
    cam = cc.parse_cameras([{"name": "a", "source": "phone", "modes": ["security"],
                             "afterHours": {"start": "nope", "end": "05:00"}}])[0]
    assert cam.after_hours == cc.DEFAULT_AFTER_HOURS
