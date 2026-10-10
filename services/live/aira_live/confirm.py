"""The model proposes, the backend commits.

Every consequential tool returns ``propose(...)`` first; the commit runs only through ``resolve(...)`` after a
spoken (``confirm_action``) or tapped (client ``confirm``) "yes". A proposal commits at most once.
"""
import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from aira_live import config
from aira_live.contracts import ConfirmMsg
from aira_live.i18n import t
from aira_live.session import get_session

Committer = Callable[[dict, str | None], Awaitable[dict]]
_COMMITTERS: dict[str, Committer] = {}
_KINDS = {"order", "payment", "sale", "discrepancy", "generic"}
_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


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
    s = get_session()
    cid = uuid.uuid4().hex[:10]
    await store().put(s.shop_id, cid, {"kind": kind, "prompt": prompt, "payload": payload,
                                       "committer": committer, "status": "pending", "sessionId": s.session_id})
    await s.send(ConfirmMsg(id=cid, kind=kind if kind in _KINDS else "generic", prompt=prompt))
    return {"needs_confirm": True, "confirm_id": cid, "say": prompt}


async def resolve(confirm_id: str, answer: Literal["yes", "no"], value: str | None = None) -> dict:
    s = get_session()
    lock = _LOCKS.setdefault((s.shop_id, confirm_id), asyncio.Lock())
    async with lock:  # concurrent "yes" answers commit exactly once
        doc = await store().get(s.shop_id, confirm_id)
        if doc is None:
            return {"say": t("confirm.unknown", s.lang), "error": "unknown_confirm"}
        if doc["status"] != "pending":
            return {"say": t("confirm.already", s.lang), "status": doc["status"], "result": doc.get("result")}
        if answer == "no":
            await store().update(s.shop_id, confirm_id, {"status": "rejected"})
            return {"say": t("confirm.cancelled", s.lang), "status": "rejected"}
        # A committer that raises leaves the proposal pending, so it can be retried.
        result = await _COMMITTERS[doc["committer"]](doc["payload"], value)
        await store().update(s.shop_id, confirm_id, {"status": "committed", "result": result})
        return {"status": "committed", **result}
