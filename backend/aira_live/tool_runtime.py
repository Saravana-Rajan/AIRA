"""Wraps Live tools so each call runs with its Session and tool-call id set, and its result is recorded.

ADK's ``FunctionTool`` passes its ``ToolContext`` (which carries ``function_call_id``) to a parameter named
``tool_context`` and hides that parameter from the declaration the model sees. ``wrap_tool`` adds that
parameter, so a tool body only needs ``get_session()`` and ``ids.current_idem_key()``: a retried call with the
same tool-call id gets the same idempotency key.
"""
import asyncio
import contextvars
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


# ---------- long-press Stop: cancel the session's tool calls ----------
# Each session has a stop generation, bumped by every Stop. A call remembers the generation it started in; a call
# that ends in a later generation is stale: its result is replaced by ``CANCELLED`` (Live gets nothing to say,
# scheduled SILENT for a non-blocking tool), it is never recorded, and a running call is cancelled outright.
CANCELLED: dict = {"cancelled": True, "status": "cancelled", "retryable": False,
                   "message": "The owner pressed Stop. Do not say this result.", "say_en": ""}
_GEN: dict[str, int] = {}                     # session id -> stop generation
_TASKS: dict[str, set[asyncio.Task]] = {}     # session id -> running tool bodies (cancelled by stop)
_CALL_GEN: contextvars.ContextVar[tuple[str, int] | None] = contextvars.ContextVar("aira_call_gen", default=None)


def generation(session_id: str) -> int:
    """The stop generation of ``session_id`` (0 until its first Stop)."""
    return _GEN.get(session_id, 0)


def stop(session_id: str) -> None:
    """Long-press Stop: every tool call of ``session_id`` running now is cancelled and its result discarded.
    Idempotent; other sessions are untouched."""
    _GEN[session_id] = _GEN.get(session_id, 0) + 1
    for task in list(_TASKS.get(session_id, ())):
        task.cancel()


def end_session(session_id: str) -> None:
    """The session ended: cancel its running tool calls and forget its state."""
    stop(session_id)
    _GEN.pop(session_id, None)
    _TASKS.pop(session_id, None)


def call_is_stale() -> bool:
    """True inside a tool call that the owner stopped (a Stop came after it started). Outside a call: False."""
    started = _CALL_GEN.get()
    return started is not None and _GEN.get(started[0], 0) != started[1]


def with_status(result: dict) -> dict:
    """``result`` plus the structured fields Gemini Live expects of every function response, added only when
    missing (existing keys are kept): ``status`` ("error" when the tool reports an error or ``ok: false``,
    "no_results" when it is not sure, else "ok"), ``retryable`` (False) and ``message`` (its ``say_en`` or
    ``say``)."""
    if "status" in result and "retryable" in result and "message" in result:
        return result
    out = dict(result)
    if "status" not in out:
        if out.get("error") or out.get("ok") is False:
            out["status"] = "error"
        elif out.get("sure") is False:
            out["status"] = "no_results"
        else:
            out["status"] = "ok"
    out.setdefault("retryable", False)
    out.setdefault("message", str(out.get("say_en") or out.get("say") or ""))
    return out


_STOPPED = object()  # a tool body cancelled by stop() / end_session()


def _cancelled() -> dict:
    return dict(CANCELLED)


async def _run_cancellable(sid: str, body: Awaitable) -> Any:
    """Await a tool body as its own task, tracked so ``stop(sid)`` can cancel it. The task copies the current
    context, so the body sees the session and call id. Returns ``_STOPPED`` when a Stop cancelled it; a
    cancellation of the caller itself (Live closing) propagates as before."""
    task = asyncio.ensure_future(body)
    _TASKS.setdefault(sid, set()).add(task)
    try:
        return await task
    except asyncio.CancelledError:
        me = asyncio.current_task()
        if task.cancelled() and (me is None or me.cancelling() == 0):
            return _STOPPED  # stopped by the owner (or the session ended), not by our caller
        raise
    finally:
        tasks = _TASKS.get(sid)
        if tasks is not None:
            tasks.discard(task)
            if not tasks:
                _TASKS.pop(sid, None)
# Result keys worth a log line (never the whole result: a count may carry frames or long lists).
_LOGGED_RESULT_KEYS = ("say", "say_en", "approx", "aim_hint", "confidence", "kind", "needs_confirm", "confirm_id", "error", "ok")


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
    diag("latency", stage="tool", session=session.session_id, name=name, call_id=call_id, ms=fields["ms"],
         ok=fields["ok"])
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
        gen = generation(sid)
        gen_token = _CALL_GEN.set((sid, gen))
        _RUNNING[sid] = _RUNNING.get(sid, 0) + 1
        try:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await _run_cancellable(sid, result)
        except BaseException as exc:  # logged, never swallowed (CancelledError included)
            _log_call(fn.__name__, call_id, session, sig, args, kwargs, started, error=exc)
            raise
        finally:
            left = _RUNNING.get(sid, 1) - 1
            if left > 0:
                _RUNNING[sid] = left
            else:
                _RUNNING.pop(sid, None)
            _CALL_GEN.reset(gen_token)
            current_tool_call_id.reset(c_token)
            current_session.reset(s_token)
        if result is _STOPPED or generation(sid) != gen:  # the owner pressed Stop while it ran: never delivered, recorded or spoken
            _log_call(fn.__name__, call_id, session, sig, args, kwargs, started, result=_cancelled())
            return _cancelled()
        if isinstance(result, dict):
            result = with_status(result)
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
               non_blocking: frozenset[str] = frozenset(), silent: frozenset[str] = frozenset()) -> list:
    """The tools handed to the Live agent for ``session``, each wrapped by ``wrap_tool``.

    A tool named in ``non_blocking`` becomes an ADK ``FunctionTool`` with Live behaviour ``NON_BLOCKING``, so
    the model keeps talking while it runs. Its result is scheduled ``WHEN_IDLE`` (spoken as soon as the holding
    line ends), or ``INTERRUPT`` when it is a path / safety result (``is_urgent``): that one cuts in at once.
    A tool named in ``silent`` (the walk's ``nav_report``: a system observation, never spoken) is also
    NON_BLOCKING and its response is always scheduled ``SILENT``. The others stay plain callables (ADK wraps
    them itself).
    """
    out: list = []
    for fn in fns:
        wrapped = wrap_tool(fn, session)
        if fn.__name__ in silent:
            from google.adk.tools import FunctionTool
            from google.genai import types

            tool = FunctionTool(_silent(wrapped))
            tool.behavior = types.Behavior.NON_BLOCKING
            tool.response_scheduling = types.FunctionResponseScheduling.SILENT
            out.append(tool)
        elif fn.__name__ in non_blocking:
            from google.adk.tools import FunctionTool
            from google.genai import types

            tool = FunctionTool(_scheduled(wrapped))
            tool.behavior = types.Behavior.NON_BLOCKING
            tool.response_scheduling = types.FunctionResponseScheduling.WHEN_IDLE
            out.append(tool)
        else:
            out.append(wrapped)
    return out


def is_urgent(result: Any) -> bool:
    """A result about the way ahead or a hazard: it must interrupt whatever AIRA is saying."""
    return isinstance(result, dict) and (result.get("kind") == "path" or bool(result.get("urgent")))


def _silent(wrapped: ToolFn) -> ToolFn:
    """``wrapped`` whose result always goes back as a SILENT ``FunctionResponse``: Live never speaks it."""
    from google.genai import types

    @functools.wraps(wrapped)
    async def run(*args: Any, **kwargs: Any) -> Any:
        result = await wrapped(*args, **kwargs)
        return types.FunctionResponse(response=result if isinstance(result, dict) else {"result": result},
                                      scheduling=types.FunctionResponseScheduling.SILENT)

    run.__signature__ = wrapped.__signature__  # type: ignore[attr-defined]
    return run


def _scheduled(wrapped: ToolFn) -> ToolFn:
    """``wrapped`` returning an INTERRUPT-scheduled ``FunctionResponse`` for an urgent result (ADK reads the
    scheduling of a returned FunctionResponse and sends its ``response``) and a SILENT one for a call cancelled by
    Stop; any other result is returned as is."""
    from google.genai import types

    @functools.wraps(wrapped)
    async def run(*args: Any, **kwargs: Any) -> Any:
        result = await wrapped(*args, **kwargs)
        if isinstance(result, dict) and result.get("cancelled") is True:  # stopped: Live must not speak it
            return types.FunctionResponse(response=result,
                                          scheduling=types.FunctionResponseScheduling.SILENT)
        if is_urgent(result):
            return types.FunctionResponse(response=result,
                                          scheduling=types.FunctionResponseScheduling.INTERRUPT)
        return result

    run.__signature__ = wrapped.__signature__  # type: ignore[attr-defined]
    return run
