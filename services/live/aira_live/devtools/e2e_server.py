"""The mock aira-live for the phone app's browser test (apps/app/e2e), plus routes that script a session.

    AIRA_MOCK=1 uv run uvicorn aira_live.devtools.e2e_server:app --port <free port>

Everything the phone talks to is the real ``aira_live.main.app`` in mock mode (``/health``, ``/ws``: hello, ready,
echo, confirm replies). The extra routes only drive the one live session the way a Live tool call would, so the
test exercises the real phone-side path (confirm card -> double-tap -> ``confirm`` message -> ``confirm.resolve``
-> the committer -> the spoken result):

- ``GET  /e2e/sessions``            -> ``{"sessions": [session ids of the demo shop]}``
- ``POST /e2e/confirm``             ``{"kind": "sale", "prompt": "..."}`` -> ``{"confirmId": "cf_..."}``;
                                    the phone gets a ``confirm`` through ``confirm.propose``
- ``GET  /e2e/confirm/{confirm_id}`` -> ``{"status": pending | committed | rejected | expired}``
- ``POST /e2e/count``               ``{"counted": 12, "expected": 30, "final": false, "sku"?: "..."}``;
                                    the phone gets a ``count`` (a live delivery tally)

Mock mode only: every ``/e2e`` route answers 403 when the service is not in mock mode, so this file can never
reach a real shop (nothing here writes Firestore, sends a message or calls Gemini). With no live session the
routes answer 409. The committer records nothing: it sends the phone a ``state`` message and says "Done".
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool, StrictInt, StrictStr

from aira_live import config, confirm, sessions
from aira_live.contracts import CountMsg, StateMsg
from aira_live.main import app
from aira_live.session import Session, current_session, get_session

COMMITTER = "e2e.noop"
DEFAULT_SKU = "sakthi_curd_1l"

router = APIRouter(prefix="/e2e")


class ConfirmBody(BaseModel):
    kind: Literal["order", "payment", "sale", "discrepancy", "generic"] = "generic"
    prompt: StrictStr


class CountBody(BaseModel):
    counted: StrictInt
    expected: Optional[StrictInt]
    final: StrictBool
    sku: StrictStr = DEFAULT_SKU
    confidence: float = 0.9


async def _commit(payload: dict, value: str | None) -> dict:
    """The scripted "yes": show the result on the phone as a state change; nothing is stored anywhere."""
    s = get_session()
    entity = payload.get("entity") or "sale"
    await s.send(StateMsg(entity=entity, id=payload.get("id", "e2e"), state="COMPLETED",
                          summary=payload.get("summary", "")))
    return {"say": "Done. " + payload.get("summary", "")}


confirm.register_committer(COMMITTER, _commit)

_ENTITY = {"order": "order", "payment": "payment", "sale": "sale", "discrepancy": "delivery", "generic": "sale"}


def _mock_only() -> None:
    if not config.load().mock:
        raise HTTPException(status_code=403, detail="the e2e routes serve mock mode only")


def _session() -> Session:
    _mock_only()
    live = sessions.by_shop(config.load().demo_shop_id)
    if not live:
        raise HTTPException(status_code=409, detail="no live session: open the app and tap Start first")
    return live[-1][0]  # the newest phone


@router.get("/sessions")
async def list_sessions() -> dict:
    _mock_only()
    return {"sessions": [s.session_id for s, _ in sessions.by_shop(config.load().demo_shop_id)]}


@router.post("/confirm")
async def propose_confirm(body: ConfirmBody) -> dict:
    s = _session()
    current_session.set(s)  # confirm.propose stores the proposal for, and sends it to, this session
    payload = {"entity": _ENTITY[body.kind], "id": "e2e", "summary": body.prompt}
    result = await confirm.propose(body.kind, body.prompt, payload, COMMITTER)
    return {"confirmId": result["confirm_id"]}


@router.get("/confirm/{confirm_id}")
async def confirm_status(confirm_id: str) -> dict:
    _mock_only()
    doc = await confirm.store().get(config.load().demo_shop_id, confirm_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="unknown confirm id")
    return {"status": doc.get("status", "pending")}


@router.post("/count")
async def send_count(body: CountBody) -> dict:
    s = _session()
    await s.send(CountMsg(sku=body.sku, counted=body.counted, expected=body.expected, final=body.final,
                          confidence=body.confidence))
    return {"ok": True}


app.include_router(router)
