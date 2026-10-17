"""Tool runtime: every Live tool runs with its Session + tool-call id set, and its result is recorded."""
import asyncio
import inspect
from types import SimpleNamespace

import pytest

from aira_live import ids
from aira_live.session import current_session, current_tool_call_id, get_session
from aira_live.tool_runtime import wrap_tool


def test_wrapped_signature_keeps_params_and_adds_tool_context(mk_session):
    s, _ = mk_session()

    async def stock_query(product: str | None = None) -> dict:
        """How much stock is recorded."""
        return {"say": "ok"}

    w = wrap_tool(stock_query, s)
    assert list(inspect.signature(w).parameters) == ["product", "tool_context"]
    assert inspect.signature(w).parameters["tool_context"].kind is inspect.Parameter.KEYWORD_ONLY
    assert w.__name__ == "stock_query" and w.__doc__ == "How much stock is recorded."
    assert inspect.iscoroutinefunction(w)


async def test_wrapped_call_sets_session_and_stable_idem_key(mk_session):
    s, _ = mk_session()
    seen = {}

    async def sale_start(items: list[dict]) -> dict:
        seen["sid"] = get_session().session_id
        seen.setdefault("keys", []).append(ids.current_idem_key())
        return {"say": "Total ₹60.", "total": 60}

    w = wrap_tool(sale_start, s)
    ctx = SimpleNamespace(function_call_id="call-7")
    await w(items=[{"sku": "sakthi_curd_1l", "qty": 1}], tool_context=ctx)
    await w(items=[{"sku": "sakthi_curd_1l", "qty": 1}], tool_context=ctx)  # retried call, same id

    assert seen["sid"] == s.session_id
    assert seen["keys"][0] == seen["keys"][1] == ids.idem_key(s.session_id, "call-7")
    assert [r["result"]["total"] for r in s.tool_results] == [60, 60]
    assert s.tool_results[0]["tool"] == "sale_start" and s.tool_results[0]["call_id"] == "call-7"


def test_current_idem_key_outside_a_tool_call_raises():
    with pytest.raises(RuntimeError):
        ids.current_idem_key()


def test_current_idem_key_with_call_id_but_no_session_raises():
    token = current_tool_call_id.set("call-1")
    try:
        with pytest.raises(RuntimeError):
            ids.current_idem_key()
    finally:
        current_tool_call_id.reset(token)


async def test_different_call_ids_give_different_keys(mk_session):
    s, _ = mk_session()
    keys = []

    async def order_create(supplier: str) -> dict:
        keys.append(ids.current_idem_key())
        return {"say": "ok"}

    w = wrap_tool(order_create, s)
    await w(supplier="Sakthi", tool_context=SimpleNamespace(function_call_id="a"))
    await w(supplier="Sakthi", tool_context=SimpleNamespace(function_call_id="b"))
    assert keys[0] != keys[1]


@pytest.mark.parametrize("ctx", [None, SimpleNamespace(function_call_id=""), SimpleNamespace()])
async def test_missing_call_id_gets_a_unique_local_id(mk_session, ctx):
    s, _ = mk_session()

    async def day_summary() -> dict:
        return {"say": "ok", "call": current_tool_call_id.get()}

    w = wrap_tool(day_summary, s)
    r1 = await w(tool_context=ctx)
    r2 = await w(tool_context=ctx)
    assert r1["call"].startswith("local-") and r2["call"].startswith("local-")
    assert r1["call"] != r2["call"]
    assert [r["call_id"] for r in s.tool_results] == [r1["call"], r2["call"]]


async def test_context_vars_are_reset_after_the_call(mk_session):
    s, _ = mk_session()
    outer, _ = mk_session()
    token = current_session.set(outer)
    try:
        async def stock_query() -> dict:
            assert get_session() is s
            return {"say": "ok"}

        await wrap_tool(stock_query, s)(tool_context=SimpleNamespace(function_call_id="c1"))
        assert get_session() is outer and current_tool_call_id.get() == ""
    finally:
        current_session.reset(token)


async def test_failed_call_is_not_recorded_and_context_is_reset(mk_session):
    s, _ = mk_session()

    async def supplier_pay(amount: int) -> dict:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await wrap_tool(supplier_pay, s)(amount=10, tool_context=SimpleNamespace(function_call_id="c1"))
    assert s.tool_results == []
    assert current_tool_call_id.get() == ""
    with pytest.raises(LookupError):
        get_session()


async def test_non_dict_result_is_returned_but_not_recorded(mk_session):
    s, _ = mk_session()

    async def weird() -> dict:
        return None  # type: ignore[return-value]

    assert await wrap_tool(weird, s)() is None
    assert s.tool_results == []


async def test_sync_tool_is_supported(mk_session):
    s, _ = mk_session()

    def order_status(order_id: str) -> dict:
        return {"say": "Sent.", "key": ids.current_idem_key()}

    w = wrap_tool(order_status, s)
    assert inspect.iscoroutinefunction(w)
    r = await w("o1", tool_context=SimpleNamespace(function_call_id="c9"))
    assert r["key"] == ids.idem_key(s.session_id, "c9")
    assert s.tool_results[0]["result"] is r


async def test_tool_that_already_takes_tool_context_gets_it_passed_through(mk_session):
    s, _ = mk_session()

    async def product_find(product: str, tool_context=None) -> dict:
        return {"say": "ok", "ctx": tool_context}

    w = wrap_tool(product_find, s)
    assert list(inspect.signature(w).parameters) == ["product", "tool_context"]
    ctx = SimpleNamespace(function_call_id="c2")
    r = await w(product="curd", tool_context=ctx)
    assert r["ctx"] is ctx and s.tool_results[0]["call_id"] == "c2"


async def test_var_keyword_tool_keeps_a_valid_signature(mk_session):
    s, _ = mk_session()

    async def flexible(a: int, **extra) -> dict:
        return {"say": "ok", "extra": extra}

    w = wrap_tool(flexible, s)
    assert list(inspect.signature(w).parameters) == ["a", "tool_context", "extra"]
    r = await w(1, b=2, tool_context=SimpleNamespace(function_call_id="c3"))
    assert r["extra"] == {"b": 2}


async def test_concurrent_calls_each_see_their_own_call_id(mk_session):
    s, _ = mk_session()
    a, b = asyncio.Event(), asyncio.Event()

    async def delivery_count(name: str) -> dict:
        before = ids.current_idem_key()
        (a if name == "x" else b).set()
        await a.wait()
        await b.wait()  # both calls are in flight at the same time here
        assert ids.current_idem_key() == before
        return {"say": name, "key": before}

    w = wrap_tool(delivery_count, s)
    rx, ry = await asyncio.gather(
        w(name="x", tool_context=SimpleNamespace(function_call_id="cx")),
        w(name="y", tool_context=SimpleNamespace(function_call_id="cy")),
    )
    assert rx["key"] == ids.idem_key(s.session_id, "cx")
    assert ry["key"] == ids.idem_key(s.session_id, "cy")


async def test_background_task_started_by_a_tool_keeps_its_context(mk_session):
    """Non-blocking tools (delivery_count, sale_pay) keep working after they return."""
    s, _ = mk_session()
    seen = {}

    async def later():
        await asyncio.sleep(0)
        seen["sid"] = get_session().session_id
        seen["key"] = ids.current_idem_key()

    async def sale_pay(method: str) -> dict:
        seen["task"] = asyncio.create_task(later())
        return {"say": "Waiting for the soundbox."}

    await wrap_tool(sale_pay, s)(method="upi", tool_context=SimpleNamespace(function_call_id="bg-1"))
    await seen["task"]
    assert seen["sid"] == s.session_id and seen["key"] == ids.idem_key(s.session_id, "bg-1")


async def test_adk_function_tool_hides_tool_context_and_passes_the_call_id(mk_session):
    from google.adk.tools import FunctionTool

    s, _ = mk_session()

    async def stock_query(product: str, days: int = 1) -> dict:
        """How much stock is recorded."""
        return {"say": "ok", "key": ids.current_idem_key(), "days": days}

    tool = FunctionTool(wrap_tool(stock_query, s))
    decl = tool._get_declaration()
    assert decl.name == "stock_query"
    schema = decl.parameters_json_schema or decl.parameters
    props = schema["properties"] if isinstance(schema, dict) else schema.properties
    assert set(props) == {"product", "days"}

    r = await tool.run_async(args={"product": "curd", "days": 2.0},  # Live sends JSON numbers as floats
                             tool_context=SimpleNamespace(function_call_id="adk-1"))
    assert r["key"] == ids.idem_key(s.session_id, "adk-1")
    assert r["days"] == 2 and type(r["days"]) is int  # ADK still sees the type hints through the wrapper
    assert s.tool_results[-1]["call_id"] == "adk-1" and s.tool_results[-1]["tool"] == "stock_query"


async def test_string_annotations_are_resolved_for_adk(mk_session):
    """A tool module with `from __future__ import annotations` has string annotations."""
    from google.adk.tools import FunctionTool

    s, _ = mk_session()

    async def order_create(supplier: "str", qty: "int") -> "dict":
        return {"say": "ok", "qty": qty}

    w = wrap_tool(order_create, s)
    assert inspect.signature(w).parameters["qty"].annotation is int
    tool = FunctionTool(w)
    assert tool._get_declaration().name == "order_create"
    r = await tool.run_async(args={"supplier": "Sakthi", "qty": 30.0},
                             tool_context=SimpleNamespace(function_call_id="c-30"))
    assert r["qty"] == 30 and type(r["qty"]) is int


def test_every_registered_tool_can_be_wrapped_for_adk(mk_session):
    from google.adk.tools import FunctionTool

    from aira_live.tools import TOOL_FUNCS

    s, _ = mk_session()
    for fn in TOOL_FUNCS:
        w = wrap_tool(fn, s)
        params = inspect.signature(w).parameters
        assert "tool_context" in params, fn.__name__
        decl = FunctionTool(w)._get_declaration()
        assert decl.name == fn.__name__
        schema = decl.parameters_json_schema or decl.parameters
        props = (schema.get("properties") if isinstance(schema, dict) else schema.properties) if schema else {}
        assert "tool_context" not in (props or {}), fn.__name__


# ---------- diagnostics: one aira="tool" line per call ----------
def _tool_lines(capsys) -> list[dict]:
    import json
    out = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    return [r for r in out if r.get("aira") == "tool"]


async def test_tool_call_is_logged_with_args_summary_duration_and_say(mk_session, capsys, monkeypatch):
    monkeypatch.delenv("AIRA_DIAG", raising=False)
    monkeypatch.setenv("AIRA_DIAG_CONTENT", "1")  # this test reads the logged 'say' text
    s, _ = mk_session()

    async def camera_ask(question: str) -> dict:
        return {"say": "Not sure. " + "x" * 100, "aim_hint": "vision.aim.show_items", "confidence": 0.3,
                "objects": [{"label": "crate", "point": [1, 2]}]}

    w = wrap_tool(camera_ask, s)
    capsys.readouterr()
    await w(question="q" * 100, tool_context=SimpleNamespace(function_call_id="call-9"))
    recs = _tool_lines(capsys)
    assert len(recs) == 1
    r = recs[0]
    assert r["name"] == "camera_ask" and r["call_id"] == "call-9" and r["ok"] is True
    assert r["session"] == s.session_id and r["shop"] == "sunrise_dairy"
    assert r["args"] == {"question": "q" * 80 + "…(+20)"}
    assert r["say"].startswith("Not sure.") and len(r["say"]) < 100
    assert r["aim_hint"] == "vision.aim.show_items" and r["confidence"] == 0.3
    assert isinstance(r["ms"], int) and r["ms"] >= 0
    assert "objects" not in r  # only the spoken bits of a result, never the whole payload


async def test_tool_error_is_logged_and_reraised(mk_session, capsys, monkeypatch):
    monkeypatch.delenv("AIRA_DIAG", raising=False)
    s, _ = mk_session()

    async def order_create(supplier: str) -> dict:
        raise ValueError("boom")

    w = wrap_tool(order_create, s)
    capsys.readouterr()
    with pytest.raises(ValueError):
        await w(supplier="Sakthi", tool_context=None)
    recs = _tool_lines(capsys)
    assert len(recs) == 1
    assert recs[0]["ok"] is False and recs[0]["error"] == "ValueError" and recs[0]["message"] == "boom"
    assert recs[0]["severity"] == "WARNING" and recs[0]["args"] == {"supplier": "Sakthi"}
    assert s.tool_results == []


async def test_tool_log_never_carries_frame_bytes(mk_session, capsys, monkeypatch):
    monkeypatch.delenv("AIRA_DIAG", raising=False)
    s, _ = mk_session()

    async def frame_tool(frames: list[dict]) -> dict:
        return {"say": "ok"}

    w = wrap_tool(frame_tool, s)
    capsys.readouterr()
    await w(frames=[{"jpeg": "/9j/AAAA" * 50, "w": 640}], tool_context=None)
    out = capsys.readouterr().out
    assert "/9j/" not in out and "<redacted 400>" in out


async def test_tool_log_can_be_switched_off(mk_session, capsys, monkeypatch):
    monkeypatch.setenv("AIRA_DIAG", "0")
    s, _ = mk_session()

    async def day_summary() -> dict:
        return {"say": "ok"}

    capsys.readouterr()
    await wrap_tool(day_summary, s)(tool_context=None)
    assert _tool_lines(capsys) == []
