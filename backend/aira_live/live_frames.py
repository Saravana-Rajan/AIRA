"""The newest ``live`` frame of each session (the 1 fps camera video), for tools that need a picture now.

Asking the phone for a fresh frame costs 2-4 s (request, capture, upload). A tool that can use a picture under
a second old takes it from here instead: ``Session.latest_frame(max_age_s)``. Age is measured on the server's
own clock when the frame arrived (the phone's ``ts`` is another clock and is never compared with ours); the
frame keeps its phone ``ts`` as usual. One frame per session, dropped with the session.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from aira_live.frames import Frame

_monotonic: Callable[[], float] = time.monotonic  # tests swap in a clock
_LATEST: dict[str, tuple[Frame, float]] = {}       # session id -> (frame, server time it arrived)


def remember(session_id: str, frame: Frame) -> None:
    """Keep ``frame`` as the newest live frame of ``session_id`` (an older phone ``ts`` never replaces a newer)."""
    held = _LATEST.get(session_id)
    if held is not None and held[0].ts > frame.ts:
        return
    _LATEST[session_id] = (frame, _monotonic())


def latest(session_id: str, max_age_s: float = 1.0) -> Frame | None:
    """The newest live frame of ``session_id`` if it arrived at most ``max_age_s`` ago, else None."""
    held = _LATEST.get(session_id)
    if held is None or _monotonic() - held[1] > max_age_s:
        return None
    return held[0]


def forget(session_id: str) -> None:
    _LATEST.pop(session_id, None)
