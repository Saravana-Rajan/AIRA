"""WebRTC signalling relay: live video goes phone -> dashboard peer-to-peer; the server only passes the notes.

The dashboard's Live page (a ``monitor.Subscriber`` on ``/monitor``) asks a phone session for video with
``{t:"rtc", kind:"want", to:<session id>}``. The phone (``/ws``) answers with an ``offer``, the dashboard with an
``answer``, both trickle ``ice`` candidates, and the phone says ``connected`` once the peer connection is up or
``bye`` when it is gone. Every message is a ``contracts.RtcMsg``; this module

- names the sender itself (``from`` is the session id or the monitor id ``monitor_id(sub)``; whatever the client
  put there is overwritten), and delivers only to a peer of the same shop (``from_phone`` / ``from_monitor``);
- never reads the SDP: it is capped (``MAX_SDP`` / ``MAX_CANDIDATE`` bytes), rate-limited per socket
  (``limiter``, ``MAX_PER_S`` a second) and logged through ``diag`` by kind and size only;
- pauses the session's JPEG preview (``monitor.set_preview``) while *every* monitor of the shop has a WebRTC
  picture of it (``preview_paused``), and resumes it on the first ``bye`` from either side, a monitor leaving
  (``monitor_left``) or a new monitor joining (``monitor_joined``);
- says ``bye`` for a peer that went away: to the phone when its monitor leaves, to the monitors when the session
  ends (``session_ended``).

No TURN server is configured: behind a strict NAT the peers never connect, the dashboard times out and keeps the
JPEG preview. ``monitor.py`` calls the hooks here lazily (it imports this module, this module imports it back).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from aira_live.contracts import RtcMsg, RtcOutMsg, dump_server_msg
from aira_live.diag import RateLimiter, diag

log = logging.getLogger(__name__)

MAX_SDP = 65_536        # bytes of SDP relayed in one offer / answer (a video-only offer is a few KB)
MAX_CANDIDATE = 1024    # bytes of one ICE candidate line
MAX_PER_S = 40          # signalling messages a socket may send a second (ICE trickles in bursts)

_MONITORS: dict[str, Any] = {}        # monitor id -> Subscriber
_IDS: dict[int, str] = {}             # id(Subscriber) -> monitor id
_LINKED: dict[str, set[str]] = {}     # session id -> monitor ids with a connected WebRTC picture of it
_PEERS: dict[str, set[str]] = {}      # monitor id -> session ids it signalled with (who to tell bye)
_PAUSED: set[str] = set()             # session ids whose JPEG preview this module stopped
_TASKS: set[asyncio.Task] = set()


def limiter() -> RateLimiter:
    return RateLimiter(MAX_PER_S)


def reset() -> None:
    for task in list(_TASKS):
        task.cancel()
    _TASKS.clear()
    _MONITORS.clear()
    _IDS.clear()
    _LINKED.clear()
    _PEERS.clear()
    _PAUSED.clear()


# ---------- monitors ----------
def monitor_joined(sub) -> str:
    """A monitor subscribed: give it an id (its ``from``). The shop's paused previews resume, as this monitor
    has no video yet (``monitor.subscribe`` calls this; a repeat call returns the same id)."""
    mid = _IDS.get(id(sub))
    if mid is None:
        mid = "m" + secrets.token_hex(4)
        _IDS[id(sub)] = mid
        _MONITORS[mid] = sub
        _schedule(_review_shop(sub.shop_id))
    return mid


def monitor_id(sub) -> str | None:
    return _IDS.get(id(sub))


def monitor_left(sub) -> None:
    """A monitor unsubscribed: forget its id, say ``bye`` to the sessions it signalled with and review the
    shop's previews (``monitor.unsubscribe`` calls this, after removing it from the hub)."""
    mid = _IDS.pop(id(sub), None)
    if mid is None:
        return
    _MONITORS.pop(mid, None)
    peers = _PEERS.pop(mid, set())
    for sid in list(_LINKED):
        _LINKED[sid].discard(mid)
        if not _LINKED[sid]:
            del _LINKED[sid]
    _schedule(_farewell(sub.shop_id, mid, peers))


async def _farewell(shop_id: str, mid: str, peers: set[str]) -> None:
    for sid in peers:
        session = _session(shop_id, sid)
        if session is not None:
            await _to_phone(session, RtcOutMsg(kind="bye", from_=mid, to=sid))
    await _review_shop(shop_id)


# ---------- sessions ----------
def session_ended(session) -> None:
    """The phone left: its monitors hear ``bye`` (pushed before the session-end line) and it is forgotten."""
    sid = session.session_id
    told = set(_LINKED.pop(sid, set()))
    for mid, sids in _PEERS.items():
        if sid in sids:
            sids.discard(sid)
            told.add(mid)
    for mid in told:
        sub = _MONITORS.get(mid)
        if sub is not None:
            sub.push(dump_server_msg(RtcOutMsg(kind="bye", from_=sid, to=mid)))
    _PAUSED.discard(sid)


def preview_paused(session_id: str) -> bool:
    """True while this module has stopped the session's JPEG preview (every monitor of its shop has video)."""
    return session_id in _PAUSED


# ---------- relay ----------
def _too_big(msg: RtcMsg) -> bool:
    if msg.sdp is not None and len(msg.sdp.encode("utf-8", "replace")) > MAX_SDP:
        return True
    return msg.candidate is not None and len(msg.candidate.candidate.encode("utf-8", "replace")) > MAX_CANDIDATE


def _size(msg: RtcMsg) -> int:
    if msg.sdp is not None:
        return len(msg.sdp)
    return len(msg.candidate.candidate) if msg.candidate is not None else 0


def _session(shop_id: str, session_id: str):
    try:
        from aira_live import sessions  # lazy: sessions imports monitor, which imports this module
        for s, _bridge in sessions.by_shop(shop_id):
            if s.session_id == session_id:
                return s
    except Exception:  # noqa: BLE001
        log.exception("rtc: session lookup failed")
    return None


async def _to_phone(session, out: RtcOutMsg) -> bool:
    try:
        await session.send(out)
    except Exception as exc:  # noqa: BLE001 - a phone that just left
        log.debug("rtc: %s not sent to %s: %s", out.kind, session.session_id, exc)
        return False
    return True


async def from_phone(session, msg: RtcMsg) -> bool:
    """A phone's signalling for monitor ``msg.to`` of its own shop. True when it was delivered."""
    sid, shop = session.session_id, session.shop_id
    sub = _MONITORS.get(msg.to)
    mid = msg.to if sub is not None and sub.shop_id == shop else None
    if mid is None or _too_big(msg):
        diag("rtc", kind=msg.kind, session=sid, shop=shop, monitor=msg.to, bytes=_size(msg), dropped=True)
        return False
    sub.push(dump_server_msg(RtcOutMsg(kind=msg.kind, from_=sid, to=mid, sdp=msg.sdp, candidate=msg.candidate)))
    diag("rtc", kind=msg.kind, session=sid, shop=shop, monitor=mid, bytes=_size(msg))
    _PEERS.setdefault(mid, set()).add(sid)
    if msg.kind == "connected":
        _LINKED.setdefault(sid, set()).add(mid)
        await _review_session(session)
    elif msg.kind == "bye":
        _unlink(sid, mid)
        await _review_session(session)
    return True


async def from_monitor(sub, msg: RtcMsg) -> bool:
    """A monitor's signalling for session ``msg.to`` of its shop. True when it was delivered."""
    mid = _IDS.get(id(sub))
    session = _session(sub.shop_id, msg.to) if mid else None
    if session is None or _too_big(msg):
        diag("rtc", kind=msg.kind, monitor=mid, shop=sub.shop_id, session=msg.to, bytes=_size(msg), dropped=True)
        return False
    sent = await _to_phone(session, RtcOutMsg(kind=msg.kind, from_=mid, to=msg.to, sdp=msg.sdp, candidate=msg.candidate))
    diag("rtc", kind=msg.kind, monitor=mid, shop=sub.shop_id, session=msg.to, bytes=_size(msg), dropped=not sent)
    if not sent:
        return False
    _PEERS.setdefault(mid, set()).add(msg.to)
    if msg.kind == "bye":
        _unlink(msg.to, mid)
        _PEERS[mid].discard(msg.to)
        await _review_session(session)
    return True


def _unlink(sid: str, mid: str) -> None:
    linked = _LINKED.get(sid)
    if linked is not None:
        linked.discard(mid)
        if not linked:
            del _LINKED[sid]


# ---------- the preview ----------
def _all_linked(shop_id: str, session_id: str) -> bool:
    from aira_live import monitor  # lazy: monitor imports this module

    subs = monitor._HUB.get(shop_id) or []
    if not subs:
        return False
    linked = _LINKED.get(session_id, set())
    return all(_IDS.get(id(s)) in linked for s in subs)


async def _review_session(session) -> None:
    """Stop the session's JPEG preview when every monitor of its shop has video; resume it otherwise."""
    from aira_live import monitor  # lazy

    sid = session.session_id
    want_pause = _all_linked(session.shop_id, sid)
    if want_pause and sid not in _PAUSED:
        _PAUSED.add(sid)
        await monitor.set_preview(session, False)
    elif not want_pause and sid in _PAUSED:
        _PAUSED.discard(sid)
        if monitor.monitored(session.shop_id):
            await monitor.set_preview(session, True)


async def _review_shop(shop_id: str) -> None:
    try:
        from aira_live import sessions  # lazy
        live = sessions.by_shop(shop_id)
    except Exception:  # noqa: BLE001
        log.exception("rtc: preview review failed for %s", shop_id)
        return
    for s, _bridge in live:
        if s.session_id in _PAUSED or s.session_id in _LINKED:
            await _review_session(s)


def _schedule(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    task = loop.create_task(coro, name="rtc")
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
