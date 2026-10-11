"""Idempotency keys: every committing tool derives its key from the session and the tool-call id."""
import hashlib


def idem_key(session_id: str, tool_call_id: str) -> str:
    return hashlib.sha256(f"{session_id}:{tool_call_id}".encode()).hexdigest()[:32]


def current_idem_key() -> str:
    """Idempotency key for the tool call running now (same tool-call id → same key).

    Committing tools call this instead of building their own key. Raises RuntimeError outside a wrapped tool call.
    """
    from aira_live.session import current_tool_call_id, get_session

    call_id = current_tool_call_id.get()
    if not call_id:
        raise RuntimeError("current_idem_key() called outside a tool call")
    try:
        session = get_session()
    except LookupError:
        raise RuntimeError("current_idem_key() called without a current session") from None
    return idem_key(session.session_id, call_id)
