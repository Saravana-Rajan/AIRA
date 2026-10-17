"""Audit trail: shops/{shopId}/auditLogs/{id} (spec §5). Use log_in_txn inside a business transaction."""
import uuid
from typing import Literal

from aira_live.business import paths
from aira_live.business.clock import now_iso
from aira_live.business.store import Txn, get_store

Actor = Literal["owner", "aira", "system"]
_ACTORS = ("owner", "aira", "system")


def _doc(aid: str, actor: str, action: str, ref_type: str, ref_id: str, outcome: str,
         evidence_uri: str | None) -> dict:
    if actor not in _ACTORS:
        raise ValueError(f"bad actor {actor!r}")
    return {"id": aid, "actor": actor, "action": action, "refType": ref_type, "refId": ref_id, "outcome": outcome,
            "evidenceUri": evidence_uri, "at": now_iso()}


async def log(shop_id: str, actor: Actor, action: str, ref_type: str, ref_id: str, outcome: str,
              evidence_uri: str | None = None) -> str:
    aid = uuid.uuid4().hex
    await get_store().set(paths.audit(shop_id, aid), _doc(aid, actor, action, ref_type, ref_id, outcome, evidence_uri))
    return aid


def log_in_txn(txn: Txn, shop_id: str, actor: Actor, action: str, ref_type: str, ref_id: str, outcome: str,
               evidence_uri: str | None = None) -> str:
    """WRITE only: call after every read of the surrounding transaction."""
    aid = uuid.uuid4().hex
    txn.set(paths.audit(shop_id, aid), _doc(aid, actor, action, ref_type, ref_id, outcome, evidence_uri))
    return aid
