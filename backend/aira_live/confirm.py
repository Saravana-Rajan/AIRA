"""The model proposes, the backend commits.

Every consequential tool returns ``propose(...)`` first; the commit runs only through ``resolve(...)`` after a
spoken (``confirm_action``) or tapped (client ``confirm``) "yes". A proposal commits at most once.

- A proposal lives at ``shops/{shop}/proposals/{confirm_id}`` with ``status`` pending, committed, rejected or
  expired. It expires ``PROPOSAL_TTL_S`` seconds after it is made.
- A proposal belongs to the session that made it. Another session (even of the same shop, e.g. two judge phones
  in the demo shop) gets the same "unknown" reply as for a missing id, and the proposal stays pending.
- Only an explicit "yes" commits. A repeated answer never commits again: it says "already done" and carries the
  stored result, so the owner never hears a second "order sent".
- Exactly once: ``resolve`` holds a per-proposal lock, so a voice ``confirm_action`` and an on-screen ``confirm``
  at the same moment run the committer once. The lock is in-process, which is enough because aira-live runs as a
  single instance. Committers are also idempotent: proposing tools put ``ids.current_idem_key()`` in the payload.
- A committer that raises leaves the proposal pending, so the owner can retry it.
"""
import asyncio
import logging
import time
import uuid
import weakref
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from aira_live import config, monitor
from aira_live.contracts import ConfirmMsg
from aira_live.diag import diag, trunc
from aira_live.i18n import t
from aira_live.session import Session, get_session

log = logging.getLogger(__name__)

Committer = Callable[[dict, str | None], Awaitable[dict]]
PROPOSAL_TTL_S = 120
_COMMITTERS: dict[str, Committer] = {}
_KINDS = {"order", "payment", "sale", "discrepancy", "generic"}
# Held only while a resolve runs, so answered proposals do not pile up locks.
_LOCKS: "weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock]" = weakref.WeakValueDictionary()


class ProposalStore(Protocol):
    async def put(self, shop_id: str, confirm_id: str, doc: dict) -> None: ...
    async def get(self, shop_id: str, confirm_id: str) -> dict | None: ...
    async def update(self, shop_id: str, confirm_id: str, fields: dict) -> None: ...


class MemoryProposalStore:
    def __init__(self):
        self._docs: dict[tuple[str, str], dict] = {}

    async def put(self, shop_id: str, confirm_id: str, doc: dict) -> None:
        self._docs[(shop_id, confirm_id)] = dict(doc)

    async def get(self, shop_id: str, confirm_id: str) -> dict | None:
        d = self._docs.get((shop_id, confirm_id))
        return dict(d) if d else None

    async def update(self, shop_id: str, confirm_id: str, fields: dict) -> None:
        self._docs[(shop_id, confirm_id)].update(fields)


class FirestoreProposalStore:
    """shops/{shop}/proposals/{confirm_id} in the "aira" database."""

    def _ref(self, shop_id: str, confirm_id: str):
        from aira_live.db import db
        return db().collection("shops").document(shop_id).collection("proposals").document(confirm_id)

    async def put(self, shop_id: str, confirm_id: str, doc: dict) -> None:
        await self._ref(shop_id, confirm_id).set(doc)

    async def get(self, shop_id: str, confirm_id: str) -> dict | None:
        snap = await self._ref(shop_id, confirm_id).get()
        return snap.to_dict() if snap.exists else None

    async def update(self, shop_id: str, confirm_id: str, fields: dict) -> None:
        await self._ref(shop_id, confirm_id).update(fields)


_store: ProposalStore | None = None


def set_store(store: ProposalStore | None) -> None:
    global _store
    _store = store


def store() -> ProposalStore:
    global _store
    if _store is None:
        _store = MemoryProposalStore() if config.load().mock else FirestoreProposalStore()
    return _store


def register_committer(name: str, fn: Committer) -> None:
    _COMMITTERS[name] = fn


async def propose(kind: str, prompt: str, payload: dict, committer: str) -> dict:
    """Store a pending proposal for the current session, show it on the phone, return the question to ask.

    Raises ``KeyError`` when no committer is registered as ``committer`` (nothing is stored or sent).
    """
    if committer not in _COMMITTERS:
        raise KeyError(f"no committer registered as {committer!r}")
    s = get_session()
    cid = "cf_" + uuid.uuid4().hex[:10]
    await store().put(s.shop_id, cid, {
        "kind": kind, "prompt": prompt, "payload": payload, "committer": committer, "status": "pending",
        "createdAt": time.time(), "sessionId": s.session_id, "uid": s.uid,
    })
    await s.send(ConfirmMsg(id=cid, kind=kind if kind in _KINDS else "generic", prompt=prompt))
    diag("confirm", action="propose", session=s.session_id, shop=s.shop_id, id=cid, kind=kind,
         committer=committer, prompt=trunc(prompt))
    # The phone's ConfirmMsg reaches the monitor through main's send mirror when the session is on /ws; a session
    # without that mirror (tests, dev tools) gets the same event here, deduplicated by the hub's publishers.
    monitor.confirm_proposed(s, cid, kind if kind in _KINDS else "generic", prompt)
    return {"needs_confirm": True, "confirm_id": cid, "say": prompt}


async def resolve(confirm_id: str, answer: Literal["yes", "no"], value: str | None = None) -> dict:
    """Apply the owner's answer to a proposal of the current session; only an explicit "yes" commits.

    Every reply has ``ok``, ``say`` and ``status`` (committed, rejected, expired, pending or unknown). The first
    commit returns the committer's result; unknown, foreign, expired and uncommittable proposals add an ``error``.
    """
    s = get_session()
    key = (s.shop_id, confirm_id)
    lock = _LOCKS.get(key)
    if lock is None:  # no await between the lookup and the insert, so every concurrent resolve shares one lock
        lock = _LOCKS[key] = asyncio.Lock()
    async with lock:  # concurrent "yes" answers commit exactly once
        result = await _resolve_locked(s, confirm_id, answer, value)
    diag("confirm", action="resolve", session=s.session_id, shop=s.shop_id, id=confirm_id, answer=answer,
         value=None if value is None else trunc(value), status=result.get("status"), ok=result.get("ok"),
         error=result.get("error"), say=trunc(result.get("say", "")))
    monitor.event(s, "confirm_resolved", id=confirm_id, answer=answer, status=result.get("status"),
                  ok=result.get("ok"), say=str(result.get("say", "")), error=result.get("error"))
    return result


async def _resolve_locked(s: Session, confirm_id: str, answer: str, value: str | None) -> dict:
    st = store()
    doc = await st.get(s.shop_id, confirm_id)
    if doc is not None and doc.get("sessionId") != s.session_id:
        log.warning("session %s tried to answer proposal %s of another session", s.session_id, confirm_id)
        doc = None  # same reply as a missing id, so nothing about the proposal leaks
    if doc is None:
        return {"ok": False, "say": t("confirm.unknown", s.lang), "status": "unknown", "error": "unknown_confirm"}

    status = doc.get("status", "pending")
    if status == "committed":
        result = doc.get("result") or {}
        return {"ok": result.get("ok", True), "say": t("confirm.already", s.lang), "status": "committed",
                "result": result}
    if status == "rejected":
        return {"ok": False, "say": t("confirm.cancelled", s.lang), "status": "rejected"}

    created = doc.get("createdAt")
    if status == "expired" or (created is not None and time.time() - created > PROPOSAL_TTL_S):
        if status != "expired":
            await st.update(s.shop_id, confirm_id, {"status": "expired"})
        return {"ok": False, "say": t("confirm.expired", s.lang), "status": "expired", "error": "expired_confirm"}

    if str(answer).strip().lower() != "yes":
        result = {"ok": False, "say": t("confirm.cancelled", s.lang)}
        await st.update(s.shop_id, confirm_id, {"status": "rejected", "result": result, "resolvedAt": time.time()})
        return {**result, "status": "rejected"}

    committer = _COMMITTERS.get(doc.get("committer", ""))
    if committer is None:  # e.g. the committer's module is not imported in this process
        log.error("proposal %s names unregistered committer %r", confirm_id, doc.get("committer"))
        return {"ok": False, "say": t("error.internal", s.lang), "status": "pending", "error": "no_committer"}

    # A committer that raises leaves the proposal pending, so it can be retried.
    result = await committer(doc.get("payload") or {}, value)
    if not isinstance(result, dict):
        result = {"say": str(result)}
    await st.update(s.shop_id, confirm_id, {"status": "committed", "result": result, "resolvedAt": time.time()})
    return {"ok": True, **result, "status": "committed"}
