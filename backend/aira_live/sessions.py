"""In-process registry of live sessions, used to speak vendor replies and alerts (no FCM).

aira-live runs with ``--max-instances 1``, so a webhook or job that calls ``notify`` lands on the instance that
holds the phone's WebSocket. Each entry is a ``Session`` plus its ``LiveBridge`` (``None`` in mock mode, or while
Live is still connecting).
"""
import logging
import time

from aira_live import monitor
from aira_live.contracts import CueMsg, TranscriptMsg
from aira_live.diag import diag, trunc
from aira_live.session import Session

log = logging.getLogger(__name__)

_REG: dict[str, tuple[Session, object | None]] = {}  # session_id -> (session, bridge)
_IPS: dict[str, str] = {}  # session_id -> client address (main.MAX_SESSIONS_PER_IP)

# A session that just ended (WebSocket blip: screen lock, network switch) can be resumed by a hello carrying
# ``resume=<session_id>`` within this window: it keeps its session id (so pending proposals, the active delivery
# and the pending sale still belong to it) and its Live resumption handle.
RESUME_WINDOW_S = 60.0
_ENDED: dict[str, tuple[float, str | None]] = {}  # session_id -> (ended at, Live resumption handle)
# session_id -> that session's sent-notice ledger (Session.notice_ledger), kept across a resume so a reconnect
# never replays a notice the owner already heard. Pruned with ``_ENDED``.
_LEDGERS: dict[str, dict[str, float]] = {}


def register(session: Session, bridge: object | None = None, ip: str | None = None) -> None:
    _REG[session.session_id] = (session, bridge)
    if ip:
        _IPS[session.session_id] = ip
    earlier = _LEDGERS.get(session.session_id)
    if earlier is not None and earlier is not session.notice_ledger:  # a resumed session: continue its ledger
        earlier.update(session.notice_ledger)
        session.notice_ledger = earlier
    _LEDGERS[session.session_id] = session.notice_ledger


def count(ip: str | None = None) -> int:
    """How many live sessions are registered (all shops), or how many came from ``ip``."""
    if ip is None:
        return len(_REG)
    return sum(1 for sid, addr in _IPS.items() if addr == ip and sid in _REG)


def remember_ended(session_id: str, handle: str | None) -> None:
    """Keep a just-ended session resumable for ``RESUME_WINDOW_S`` seconds."""
    now = time.monotonic()
    for stale in [sid for sid, (at, _) in _ENDED.items() if now - at > RESUME_WINDOW_S]:
        del _ENDED[stale]
        if stale not in _REG:
            _LEDGERS.pop(stale, None)
    _ENDED[session_id] = (now, handle)


def take_ended(session_id: str | None) -> tuple[bool, str | None]:
    """``(found, handle)`` for a resumable session; consumes it. Unknown, stale or live ids give ``(False, None)``."""
    if not session_id or session_id in _REG:
        return False, None
    entry = _ENDED.pop(session_id, None)
    if entry is None or time.monotonic() - entry[0] > RESUME_WINDOW_S:
        return False, None
    return True, entry[1]


def attach_bridge(session_id: str, bridge: object) -> None:
    """Give a registered session its Live bridge; a session that already left is ignored."""
    entry = _REG.get(session_id)
    if entry is not None:
        _REG[session_id] = (entry[0], bridge)


def unregister(session: Session | str) -> None:
    """Remove a session, given the ``Session`` or its ``session_id``. Unknown sessions are ignored."""
    sid = session if isinstance(session, str) else session.session_id
    _REG.pop(sid, None)
    _IPS.pop(sid, None)


def by_shop(shop_id: str) -> list[tuple[Session, object | None]]:
    return [(s, b) for s, b in list(_REG.values()) if s.shop_id == shop_id]


async def notify(shop_id: str, text: str, cue: str = "ack") -> int:
    """Speak ``text`` to every live session of a shop; returns how many got it.

    Each session gets the text as a caption plus an earcon. When it has a Live bridge, AIRA also says it in the
    live audio, in the language the owner last spoke. A session that fails (closed socket, dead bridge) is
    logged, skipped and not counted. A session that already got the same notice within
    ``session.NOTICE_DEDUPE_S`` seconds is skipped too: a loop may raise the same line again, the owner hears
    it once.
    """
    n = 0
    targets = by_shop(shop_id)
    for s, bridge in targets:
        if s.notice_is_repeat(text):
            continue
        try:
            await s.send(TranscriptMsg(role="aira", text=text, final=True, lang=s.lang))
            await s.send(CueMsg(cue=cue))
            if bridge is not None:
                await bridge.speak_notice(text)
        except Exception:
            log.warning("notify: session %s unreachable", s.session_id, exc_info=True)
            continue
        n += 1
        monitor.notified(s, text, cue)
    diag("notify", shop=shop_id, text=trunc(text), cue=cue, sessions=len(targets), delivered=n)
    return n
