"""Wraps Live tools so each call runs with its Session and tool-call id set, and its result is recorded.

ADK's ``FunctionTool`` passes its ``ToolContext`` (which carries ``function_call_id``) to a parameter named
``tool_context`` and hides that parameter from the declaration the model sees. ``wrap_tool`` adds that
parameter, so a tool body only needs ``get_session()`` and ``ids.current_idem_key()``: a retried call with the
same tool-call id gets the same idempotency key.
"""
import functools
import inspect
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

try:  # ADK finds the context parameter by this annotation, or by the name `tool_context`.
    from google.adk.tools import ToolContext
except ImportError:  # pragma: no cover - ADK is a declared dependency
    ToolContext = Any  # type: ignore[misc,assignment]

from aira_live import monitor
from aira_live.diag import diag, summarize, trunc
from aira_live.session import Session, current_session, current_tool_call_id

ToolFn = Callable[..., Awaitable[dict]]
_CTX = "tool_context"
_RUNNING: dict[str, int] = {}  # session id -> tool calls running now (the live-video nudge waits for them)


def tools_running(session_id: str) -> int:
    """How many wrapped tool calls of ``session_id`` are running now."""
    return _RUNNING.get(session_id, 0)
# Result keys worth a log line (never the whole result: a count may carry frames or long lists).
_LOGGED_RESULT_KEYS = ("say", "aim_hint", "confidence", "kind", "needs_confirm", "confirm_id", "error", "ok")


def _args_summary(sig: inspect.Signature, args: tuple, kwargs: dict) -> Any:
    kw = {k: v for k, v in kwargs.items() if k != _CTX}
    try:
        bound = dict(sig.bind_partial(*args, **kw).arguments)
    except (TypeError, ValueError):
        bound = {**({"_args": list(args)} if args else {}), **kw}
    bound.pop(_CTX, None)
    return summarize(bound)


def _log_call(name: str, call_id: str, session: Session, sig: inspect.Signature, args: tuple, kwargs: dict,
              started: float, result: Any = None, error: BaseException | None = None) -> None:
    """One ``aira="tool"`` line per call: name, args summary, duration, ok/error and the spoken result."""
    fields: dict[str, Any] = {"name": name, "call_id": call_id, "session": session.session_id,
                              "shop": session.shop_id, "args": _args_summary(sig, args, kwargs),
                              "ms": round((time.perf_counter() - started) * 1000)}
    if error is not None:
        fields.update(ok=False, error=type(error).__name__, message=trunc(str(error), 200))
        diag("tool", severity="WARNING", **fields)
    else:
        fields["ok"] = True
        if isinstance(result, dict):
            for key in _LOGGED_RESULT_KEYS:
                if key in result:
                    fields[key] = summarize(result[key])
        else:
            fields["result_type"] = type(result).__name__
        diag("tool", **fields)
    # The dashboard's Live page: the same summary (never the result body), under the tool's name.
    detail = {"tool": name, **{k: v for k, v in fields.items() if k not in ("name", "session", "shop", "call_id")}}
    monitor.event(session, "tool_call", **detail)


def _signature(fn: Callable[..., Any]) -> inspect.Signature:
    """The tool's signature with string annotations resolved, so ADK can build the declaration."""
    try:
        return inspect.signature(fn, eval_str=True)
    except Exception:  # unresolvable forward reference: keep the annotations as written
        return inspect.signature(fn)


def _call_id(tool_context: Any) -> str:
    call_id = getattr(tool_context, "function_call_id", None)
    return call_id if isinstance(call_id, str) and call_id else f"local-{uuid.uuid4().hex[:12]}"


def wrap_tool(fn: Callable[..., Any], session: Session) -> ToolFn:
    """Return an async tool with ``fn``'s name, doc and signature plus a keyword-only ``tool_context``.

    While ``fn`` runs, ``current_session`` is ``session`` and ``current_tool_call_id`` is the Live call id (or a
    ``local-<hex>`` id when there is none). A ``dict`` result is appended to ``session.tool_results``. A call
    that raises is not recorded. ``fn`` may be sync or async. A tool that already takes ``tool_context`` gets
    it passed through.
    """
    sig = _signature(fn)
    takes_ctx = _CTX in sig.parameters
    if takes_ctx:
        new_sig = sig
    else:
        params = list(sig.parameters.values())
        ctx_param = inspect.Parameter(_CTX, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=ToolContext)
        # A keyword-only parameter must come before **kwargs.
        at = next((i for i, p in enumerate(params) if p.kind is inspect.Parameter.VAR_KEYWORD), len(params))
        params.insert(at, ctx_param)
        new_sig = sig.replace(parameters=params)

    @functools.wraps(fn)
    async def wrapper(*args: Any, tool_context: Any = None, **kwargs: Any) -> Any:
        call_id = _call_id(tool_context)
        if takes_ctx:
            kwargs[_CTX] = tool_context
        s_token = current_session.set(session)
        c_token = current_tool_call_id.set(call_id)
        started = time.perf_counter()
        monitor.status(session, "thinking")
        sid = session.session_id
        _RUNNING[sid] = _RUNNING.get(sid, 0) + 1
        try:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
        except BaseException as exc:  # logged, never swallowed (CancelledError included)
            _log_call(fn.__name__, call_id, session, sig, args, kwargs, started, error=exc)
            raise
        finally:
            left = _RUNNING.get(sid, 1) - 1
            if left > 0:
                _RUNNING[sid] = left
            else:
                _RUNNING.pop(sid, None)
            current_tool_call_id.reset(c_token)
            current_session.reset(s_token)
        _log_call(fn.__name__, call_id, session, sig, args, kwargs, started, result=result)
        if isinstance(result, dict):
            session.tool_results.append({"tool": fn.__name__, "call_id": call_id, "result": result})
        return result

    wrapper.__signature__ = new_sig  # type: ignore[attr-defined]
    # functools.wraps shares fn's annotations dict; give the wrapper its own copy with tool_context in it.
    wrapper.__annotations__ = {**getattr(fn, "__annotations__", {}),
                               _CTX: new_sig.parameters[_CTX].annotation}
    return wrapper


def live_tools(fns: list[Callable[..., Any]], session: Session,
               non_blocking: frozenset[str] = frozenset()) -> list:
    """The tools handed to the Live agent for ``session``, each wrapped by ``wrap_tool``.

    A tool named in ``non_blocking`` becomes an ADK ``FunctionTool`` with Live behaviour ``NON_BLOCKING``, so
    the model keeps talking while it runs. The others stay plain callables (ADK wraps them itself).
    """
    out: list = []
    for fn in fns:
        wrapped = wrap_tool(fn, session)
        if fn.__name__ in non_blocking:
            from google.adk.tools import FunctionTool
            from google.genai import types

            tool = FunctionTool(wrapped)
            tool.behavior = types.Behavior.NON_BLOCKING
            out.append(tool)
        else:
            out.append(wrapped)
    return out
