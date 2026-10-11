"""The ``cameras`` list in ``config/runtime``: ``[{name, source, urlEnv, modes, intervalS, enabled}, ...]``.

Default: empty, so nothing runs. Every entry is checked here; a bad one is skipped with a warning and never
stops the good ones. Names are lower-cased and limited to ``[a-z0-9_-]`` so they are safe in env names, log
lines and event payloads. The RTSP URL is never in this doc: ``urlEnv`` names the environment variable that
holds it (Secret Manager on Cloud Run), default ``CAMERA_<NAME>_URL``.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from aira_live import config as settings_mod

log = logging.getLogger(__name__)

Source = Literal["phone", "rtsp"]
Mode = Literal["delivery", "shelf", "counter", "security"]
SOURCES: tuple[str, ...] = ("phone", "rtsp")
MODES: tuple[str, ...] = ("delivery", "shelf", "counter", "security")

DEFAULT_INTERVAL_S = 5.0
MIN_INTERVAL_S = 1.0
MAX_INTERVAL_S = 600.0
DEFAULT_MAX_PER_MINUTE = 6
MAX_PER_MINUTE_CAP = 60
DEFAULT_AFTER_HOURS = ("22:00", "06:00")  # security mode: local (IST) shop-closed window, wraps midnight

_NAME = re.compile(r"^[a-z0-9_-]{1,32}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class CameraConfig:
    name: str
    source: Source
    modes: tuple[str, ...]
    interval_s: float = DEFAULT_INTERVAL_S
    enabled: bool = True
    url_env: str | None = None            # rtsp only: env var holding the RTSP URL
    shop_id: str = "murugan_dairy"
    max_per_minute: int = DEFAULT_MAX_PER_MINUTE
    after_hours: tuple[str, str] = DEFAULT_AFTER_HOURS


def safe_name(raw: Any) -> str | None:
    """``"Front Door"`` -> ``"front-door"``; ``None`` when nothing safe is left."""
    if not isinstance(raw, str):
        return None
    name = re.sub(r"[^a-z0-9_-]+", "-", raw.strip().lower()).strip("-")[:32]
    return name if _NAME.match(name) else None


def default_url_env(name: str) -> str:
    return "CAMERA_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper() + "_URL"


def _number(value: Any, default: float, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(min(hi, max(lo, value)))


def _after_hours(raw: Any) -> tuple[str, str]:
    if isinstance(raw, dict):
        start, end = raw.get("start"), raw.get("end")
        if isinstance(start, str) and isinstance(end, str) and _HHMM.match(start) and _HHMM.match(end):
            return (start, end)
    return DEFAULT_AFTER_HOURS


def parse_entry(raw: Any, shop_id: str) -> CameraConfig | None:
    """One list entry -> ``CameraConfig``, or ``None`` (with the reason logged) when it is unusable or disabled."""
    if not isinstance(raw, dict):
        log.warning("cameras: skipping entry that is not an object: %r", type(raw).__name__)
        return None
    name = safe_name(raw.get("name"))
    if name is None:
        log.warning("cameras: skipping entry with a missing or unsafe name")
        return None
    if raw.get("enabled", True) is False:
        log.info("cameras: %s is disabled", name)
        return None
    source = raw.get("source")
    if source not in SOURCES:
        log.warning("cameras: %s has unknown source %r (expected one of %s)", name, source, SOURCES)
        return None
    modes_raw = raw.get("modes")
    modes = tuple(m for m in (modes_raw if isinstance(modes_raw, list) else []) if m in MODES)
    if not modes:
        log.warning("cameras: %s has no known mode (expected some of %s)", name, MODES)
        return None
    url_env = None
    if source == "rtsp":
        env_raw = raw.get("urlEnv")
        url_env = env_raw.strip() if isinstance(env_raw, str) and env_raw.strip() else default_url_env(name)
    return CameraConfig(
        name=name,
        source=source,
        modes=modes,
        interval_s=_number(raw.get("intervalS"), DEFAULT_INTERVAL_S, MIN_INTERVAL_S, MAX_INTERVAL_S),
        enabled=True,
        url_env=url_env,
        shop_id=shop_id,
        max_per_minute=int(_number(raw.get("maxPerMinute"), DEFAULT_MAX_PER_MINUTE, 1, MAX_PER_MINUTE_CAP)),
        after_hours=_after_hours(raw.get("afterHours")),
    )


def parse_cameras(raw: Any, shop_id: str | None = None) -> list[CameraConfig]:
    """The enabled, valid cameras in the ``cameras`` field. Anything that is not a list means none."""
    if not isinstance(raw, list):
        return []
    shop = shop_id or settings_mod.load().demo_shop_id
    out: list[CameraConfig] = []
    seen: set[str] = set()
    for entry in raw:
        cam = parse_entry(entry, shop)
        if cam is None:
            continue
        if cam.name in seen:
            log.warning("cameras: duplicate name %s, keeping the first", cam.name)
            continue
        seen.add(cam.name)
        out.append(cam)
    return out


def rtsp_url(cam: CameraConfig) -> str | None:
    """The RTSP URL from the environment, or ``None``. Never log the value: it usually carries a password."""
    if not cam.url_env:
        return None
    return os.getenv(cam.url_env) or None
