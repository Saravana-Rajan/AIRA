"""Who hears a line. OWNER: Kanish (kanish/02).

Every phone and judge tab is a live session of the same demo shop, and ``sessions.notify`` speaks to all of them.
That is wrong for the shop's own business: one judge's "Give back 30 rupees" or a real vendor's reply must not
interrupt every other judge. So:
- ``speak_to(s, ...)``: only the session that is doing the thing (payments, a judge's demo vendor).
- ``speak_to_owners(shop_id, ...)``: the shop owner's sessions, never judges (real vendor replies).
Both send the earcon and hand the facts to Live (or caption them without a Live bridge), the way
``sessions.notify`` does (``sessions.deliver``). Tests patch these two functions to record ``(shop_id, text, cue)``.
"""
import logging

from aira_live import sessions
from aira_live.session import Session

log = logging.getLogger(__name__)

# The judge page gives every browser tab a "judge-" uid (frontend/app/src/demo/hello.ts). The shop's own phone
# never does.
JUDGE_UID_PREFIX = "judge-"


def is_judge(s: Session) -> bool:
    return str(s.uid).startswith(JUDGE_UID_PREFIX)


def _bridge_of(s: Session):
    return next((b for sess, b in sessions.by_shop(s.shop_id) if sess.session_id == s.session_id), None)


async def _deliver(s: Session, bridge, text: str, cue: str, urgent: bool = False) -> bool:
    try:
        await sessions.deliver(s, bridge, text, cue, urgent)
    except Exception:
        log.warning("speak: session %s unreachable", s.session_id, exc_info=True)
        return False
    return True


async def speak_to(s: Session, text: str, cue: str = "ack", urgent: bool = False) -> bool:
    """Tell this one session ``text`` (facts). False when it could not be reached (it left)."""
    return await _deliver(s, _bridge_of(s), text, cue, urgent)


async def speak_to_owners(shop_id: str, text: str, cue: str = "ack", urgent: bool = False) -> int:
    """Tell the shop owner's live sessions ``text`` (facts; not judges); returns how many heard it. A session that
    heard the same line moments ago is skipped, as ``sessions.notify`` does."""
    n = 0
    for s, bridge in sessions.by_shop(shop_id):
        if is_judge(s) or s.notice_is_repeat(text):
            continue
        if await _deliver(s, bridge, text, cue, urgent):
            n += 1
    return n
