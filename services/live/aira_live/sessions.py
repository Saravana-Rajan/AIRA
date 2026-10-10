"""In-process registry of live sessions (aira-live runs with --max-instances 1), used to speak alerts."""
import logging
from collections import defaultdict

from aira_live.contracts import CueMsg, TranscriptMsg
from aira_live.session import Session

log = logging.getLogger(__name__)

_REG: dict[str, set[Session]] = defaultdict(set)


def register(session: Session) -> None:
    _REG[session.shop_id].add(session)


def unregister(session: Session) -> None:
    _REG[session.shop_id].discard(session)


async def notify(shop_id: str, text: str, cue: str = "ack") -> int:
    """Speak ``text`` to every live session of a shop; returns how many got it. A broken socket is skipped.

    In mock mode it is a transcript plus a cue; saravana/01 routes it through Live audio.
    """
    n = 0
    for s in list(_REG.get(shop_id, ())):
        try:
            await s.send(TranscriptMsg(role="aira", text=text, final=True))
            await s.send(CueMsg(cue=cue))
        except Exception:
            log.warning("notify: session %s unreachable", s.session_id)
            continue
        n += 1
    return n
