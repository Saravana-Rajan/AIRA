"""The Live tool registry: every tool from the interfaces table, exact parameters, typed stubs, and the harness."""
import inspect
import json
import typing

import pytest

from aira_live import confirm
from aira_live.contracts import CueMsg
from aira_live.devtools import call_tool
from aira_live.session import get_session
from aira_live.tools import TOOL_FUNCS, TOOLS_BY_NAME

# docs/superpowers/plans/2026-10-10-00-interfaces.md, "Live tools" (+ the product_find count_needed addition).
EXPECTED = {
    "order_create": ["supplier", "items"], "order_status": ["order_id"],
    "delivery_start": ["order_id"], "delivery_count": ["sku", "mode"], "invoice_check": ["order_id"],
    "supplier_pay": ["order_id", "method"], "sale_start": ["items"], "sale_pay": ["sale_id", "method"],
    "stock_query": ["product"], "expiry_check": [],
    "product_find": ["product", "count_needed"], "product_identify": [], "location_remember": ["product", "zone"],
    "day_summary": [], "reorder_suggest": ["product"], "confirm_action": ["confirm_id", "answer"],
}

OPTIONAL_DEFAULTS = {
    ("order_status", "order_id"): None, ("delivery_start", "order_id"): None,
    ("delivery_count", "mode"): "one_by_one", ("stock_query", "product"): None,
    ("product_find", "count_needed"): 1, ("reorder_suggest", "product"): None,
}

LITERALS = {
    ("delivery_count", "mode"): ("one_by_one", "crate"),
    ("supplier_pay", "method"): ("cash", "upi"), ("sale_pay", "method"): ("cash", "upi"),
    ("confirm_action", "answer"): ("yes", "no"),
}

# Typed sample arguments for each stub. When an owner implements a tool, they remove it from STUBS
# and test it in their own tests/<area>/ folder.
SAMPLE_ARGS = {
    "delivery_start": {}, "delivery_count": {"sku": "sakthi_curd_1l"},
    "invoice_check": {"order_id": "o1"}, "supplier_pay": {"order_id": "o1", "method": "cash"},
    "sale_start": {"items": [{"name": "curd", "qty": 2}]}, "sale_pay": {"sale_id": "s1", "method": "upi"},
    "stock_query": {"product": "curd"}, "expiry_check": {},
}
STUBS = sorted(set(SAMPLE_ARGS)
               - {"delivery_start", "delivery_count", "invoice_check"}          # tests/tools/test_delivery.py
               - {"supplier_pay", "sale_start", "sale_pay", "stock_query", "expiry_check"})  # test_payments_sales.py


def test_every_tool_exists_with_exact_parameters():
    assert set(TOOLS_BY_NAME) == set(EXPECTED)
    assert len(TOOL_FUNCS) == len(EXPECTED) == len(TOOLS_BY_NAME)
    for name, params in EXPECTED.items():
        assert list(inspect.signature(TOOLS_BY_NAME[name]).parameters) == params, name


def test_tools_are_async_documented_and_return_dicts():
    for name, fn in TOOLS_BY_NAME.items():
        assert inspect.iscoroutinefunction(fn), name
        assert fn.__doc__ and fn.__doc__.strip(), f"{name} needs a docstring (Gemini reads it)"
        assert typing.get_type_hints(fn)["return"] is dict, name


def test_optional_parameters_and_their_defaults():
    for name, fn in TOOLS_BY_NAME.items():
        for p in inspect.signature(fn).parameters.values():
            expected = OPTIONAL_DEFAULTS.get((name, p.name), inspect.Parameter.empty)
            assert p.default == expected, f"{name}.{p.name}"


def test_literal_parameters_list_the_allowed_values():
    for (name, param), values in LITERALS.items():
        hint = typing.get_type_hints(TOOLS_BY_NAME[name])[param]
        assert typing.get_origin(hint) is typing.Literal and typing.get_args(hint) == values, f"{name}.{param}"


def test_items_are_lists_of_dicts():
    for name in ("order_create", "sale_start"):
        assert typing.get_type_hints(TOOLS_BY_NAME[name])["items"] == list[dict], name


@pytest.mark.parametrize("name", STUBS)
async def test_stubs_raise_not_implemented(name):
    with pytest.raises(NotImplementedError):
        await TOOLS_BY_NAME[name](**SAMPLE_ARGS[name])


@pytest.fixture
def memory_store(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    previous = confirm._store
    s = confirm.MemoryProposalStore()
    confirm.set_store(s)
    yield s
    confirm.set_store(previous)


def test_harness_runs_confirm_action(capsys, memory_store):
    call_tool.main(["confirm_action", "--args", json.dumps({"confirm_id": "nope", "answer": "yes"})])
    out = capsys.readouterr().out
    assert '"error": "unknown_confirm"' in out


def test_harness_prints_server_msgs_then_result_and_loads_images(capsys, monkeypatch, tmp_path):
    img = tmp_path / "crate.jpg"
    img.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")
    seen = {}

    async def fake_tool(sku: str) -> dict:
        """A tool that speaks a cue and reads the frames the harness loaded."""
        s = get_session()
        frames = s.frames.latest(5, purpose="count")
        seen.update(shop=s.shop_id, lang=s.lang, frames=[(f.id, f.purpose) for f in frames])
        await s.send(CueMsg(cue="thinking"))
        return {"say": f"Counted {sku}", "frames": len(frames)}

    monkeypatch.setitem(call_tool.TOOLS_BY_NAME, "fake_tool", fake_tool)
    call_tool.main(["fake_tool", "--args", '{"sku": "sakthi_curd_1l"}', "--image", str(img), "--image", str(img),
                    "--purpose", "count", "--shop", "test_shop", "--lang", "ta"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert json.loads(lines[0]) == {"t": "cue", "cue": "thinking"}
    assert json.loads(lines[-1]) == {"say": "Counted sakthi_curd_1l", "frames": 2}
    assert seen == {"shop": "test_shop", "lang": "ta", "frames": [("img0", "count"), ("img1", "count")]}


def test_harness_answers_request_frame_like_the_phone(capsys, monkeypatch, tmp_path):
    img = tmp_path / "notes.jpg"
    img.write_bytes(b"\xff\xd8notes\xff\xd9")

    async def fake_cash(amount: int) -> dict:
        """A tool that asks the phone for a burst of cash frames."""
        frames = await get_session().request_frames("cash", count=3, interval_ms=100, timeout_s=1)
        return {"say": "ok", "frames": [f.purpose for f in frames]}

    monkeypatch.setitem(call_tool.TOOLS_BY_NAME, "fake_cash", fake_cash)
    call_tool.main(["fake_cash", "--args", '{"amount": 60}', "--image", str(img)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert json.loads(lines[0]) == {"t": "request_frame", "purpose": "cash", "burst": {"count": 3, "intervalMs": 100}}
    assert json.loads(lines[-1]) == {"say": "ok", "frames": ["cash", "cash", "cash"]}


@pytest.mark.parametrize("bad", ["not json", "[1]"])
def test_harness_rejects_args_that_are_not_a_json_object(bad):
    with pytest.raises(SystemExit):
        call_tool.main(["day_summary", "--args", bad])


def test_harness_rejects_unknown_tool(capsys):
    with pytest.raises(SystemExit):
        call_tool.main(["no_such_tool"])
