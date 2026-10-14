"""Scene memory checks (AIRA_SCENE_MEMORY): Gemini Live tells AIRA what it sees, silently, so it can answer
"where did I put my glasses?" after the camera has turned away (aira_live.scene_memory).

When the phone marks a live frame ``changed`` (a new object or a settled new view), the scene is due a look. On
a following forwarded frame, in a quiet moment of the conversation, one "[SCENE CHECK n]" note goes into the
Live session; Live answers with the ``scene_report`` tool, whose response is SILENT, so nothing is spoken.

Quiet moments only (the same gate as the walk's checks, LiveBridge.request_nav_check): measured on the real Live
API, a note sent while AIRA answered cut her answer, and one sent while the owner spoke was usually lost. A
check is never queued: a busy moment just waits for the next frame, and the newest picture is the one looked at.
At most one check in flight (stale, duplicate and out-of-order answers are dropped by check id), at most one every
MIN_GAP_S, and a refresh every REFRESH_S while frames flow, so the cost is bounded (a few hundred tokens a check).
No checks during a walk: the walk's own reports feed the memory then (tools.walk).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from aira_live.diag import diag

log = logging.getLogger(__name__)

MIN_GAP_S = 4.0       # at most one scene check this often
REFRESH_S = 30.0      # with no scene change, still look again this often (keeps "in view now" honest)
TIMEOUT_S = 6.0       # a check with no answer after this long is forgotten (its late answer is dropped)
CHECK_PREFIX = "[SCENE CHECK"


def check_text(check_id: int) -> str:
    return (f"{CHECK_PREFIX} {check_id}] System check, not from the owner. Look at the newest camera frame and "
            f"call scene_report once with check_id={check_id}, listing the objects you can clearly see that he "
            "might ask about later (his things, furniture, food, containers, doors): for each its name, what it "
            "is on or in, what is next to it, and its side (left, ahead, right). List only what you clearly see; "
            "an empty list if nothing is clear. Do not speak.")


class SceneWatcher:
    def __init__(self, session_id: str, clock: Callable[[], float] = time.monotonic):
        self.session_id = session_id
        self._clock = clock
        self.due = True                 # a new scene to look at (the first frame counts as new)
        self.seq = 0
        self.pending: tuple[int, float] | None = None   # (check id, sent at)
        self.last_sent_at = float("-inf")
        self.sent = self.answered = self.dropped = 0

    def scene_changed(self) -> None:
        self.due = True

    async def on_frame(self, bridge: object, *, walking: bool = False) -> bool:
        """A live frame reached Live: send a scene check if one is due and the voice channel is quiet."""
        now = self._clock()
        if self.pending is not None and now - self.pending[1] > TIMEOUT_S:
            diag("scene_check", session=self.session_id, check=self.pending[0], skipped="timeout")
            self.pending = None
            self.dropped += 1
        if walking or self.pending is not None or now - self.last_sent_at < MIN_GAP_S:
            return False
        if not self.due and now - self.last_sent_at < REFRESH_S:
            return False
        send = getattr(bridge, "request_nav_check", None)   # the quiet-moment gate (never lifts a stop)
        if send is None:
            return False
        check_id = self.seq + 1
        try:
            ok = bool(await send(check_text(check_id)))
        except Exception:   # noqa: BLE001 - the next frame tries again
            log.debug("scene check not sent", exc_info=True)
            return False
        if not ok:
            return False     # busy: still due, the next frame tries again with the newer picture
        self.seq, self.pending, self.last_sent_at, self.due = check_id, (check_id, now), now, False
        self.sent += 1
        diag("scene_check", session=self.session_id, check=check_id, sent=True)
        return True

    def accept(self, check_id: int) -> bool:
        """True when ``check_id`` answers the check in flight (then it is no longer in flight)."""
        if self.pending is None or int(check_id) != self.pending[0]:
            diag("scene_check", session=self.session_id, check=check_id, skipped="stale")
            return False
        self.pending = None
        self.answered += 1
        return True


_WATCHERS: dict[str, SceneWatcher] = {}


def watcher(session_id: str) -> SceneWatcher:
    w = _WATCHERS.get(session_id)
    if w is None:
        w = _WATCHERS[session_id] = SceneWatcher(session_id)
    return w


def forget(session_id: str) -> None:
    _WATCHERS.pop(session_id, None)


async def on_live_frame(session, bridge, changed: bool) -> None:
    """main.live_frame hook (AIRA_SCENE_MEMORY on): a frame reached Live. Never raises."""
    try:
        w = watcher(session.session_id)
        if changed:
            w.scene_changed()
        await w.on_frame(bridge, walking=bool(getattr(session, "walking", False)))
    except Exception:   # noqa: BLE001
        log.warning("scene watch failed for %s", getattr(session, "session_id", "?"), exc_info=True)
