"""In-process registry of live sessions, used to speak vendor replies and alerts (no FCM).

aira-live runs with ``--max-instances 1``, so a webhook or job that calls ``notify`` lands on the instance that
holds the phone's WebSocket. Each entry is a ``Session`` plus its ``LiveBridge`` (``None`` in mock mode, or while
Live is still connecting).
"""
import logging

from aira_live.contracts import CueMsg, TranscriptMsg
from aira_live.session import Session

log = logging.getLogger(__name__)

_REG: dict[str, tuple[Session, object | None]] = {}  # session_id -> (session, bridge)


def register(session: Session, bridge: object | None = None) -> None:
    _REG[session.session_id] = (session, bridge)


def attach_bridge(session_id: str, bridge: object) -> None:
    """Give a registered session its Live bridge; a session that already left is ignored."""
    entry = _REG.get(session_id)
    if entry is not None:
        _REG[session_id] = (entry[0], bridge)


def unregister(session: Session | str) -> None:
    """Remove a session, given the ``Session`` or its ``session_id``. Unknown sessions are ignored."""
    _REG.pop(session if isinstance(session, str) else session.session_id, None)


def by_shop(shop_id: str) -> list[tuple[Session, object | None]]:
    return [(s, b) for s, b in list(_REG.values()) if s.shop_id == shop_id]


async def notify(shop_id: str, text: str, cue: str = "ack") -> int:
    """Speak ``text`` to every live session of a shop; returns how many got it.

    Each session gets the text as a caption plus an earcon. When it has a Live bridge, AIRA also says it in the
    live audio, in the owner's language. A session that fails (closed socket, dead bridge) is logged, skipped and
    not counted.
    """
    n = 0
    for s, bridge in by_shop(shop_id):
        try:
            await s.send(TranscriptMsg(role="aira", text=text, final=True))
            await s.send(CueMsg(cue=cue))
            if bridge is not None:
                await bridge.speak_notice(text)
        except Exception:
            log.warning("notify: session %s unreachable", s.session_id, exc_info=True)
            continue
        n += 1
    return n
