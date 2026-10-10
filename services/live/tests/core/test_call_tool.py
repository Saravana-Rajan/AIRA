"""devtools.call_tool: the real-mode tool harness.

Each tool runs through ``tool_runtime.wrap_tool`` with a harness Session, so ``get_session()``,
``ids.current_idem_key()`` and ``request_frames`` behave as in a Live call. ``--confirm`` answers a proposal
through ``confirm_action``, the same path as a spoken "yes". No network: tools are fakes and proposals live in
memory. (The foundation's harness checks in ``tests/tools/test_tools.py`` still apply.)
"""
import json

import pytest

from aira_live import confirm, ids
from aira_live.devtools import call_tool
from aira_live.session import get_session
from aira_live.tools import TOOLS_BY_NAME


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    for name in ("GEMINI_API_KEY", "AIRA_MOCK"):
        monkeypatch.delenv(name, raising=False)
    previous = confirm._store
    confirm.set_store(confirm.MemoryProposalStore())
    yield
    confirm.set_store(previous)


def _lines(out: str) -> list[dict]:
    return [json.loads(line) for line in out.strip().splitlines()]


def _use_tools(monkeypatch, *fns) -> None:
    monkeypatch.setattr(call_tool, "load_tools", lambda: {fn.__name__: fn for fn in fns})


async def test_runs_the_tool_wrapped_with_frames_and_an_idem_key(tmp_path, monkeypatch, capsys):
    img = tmp_path / "crate.jpg"
    img.write_bytes(b"\xff\xd8fake-jpeg\xff\xd9")

    async def delivery_count(sku: str, mode: str = "one_by_one") -> dict:
        frames = await get_session().request_frames("count", count=2, timeout_s=1)
        return {"say": f"{sku}: {len(frames)} frames", "idem": ids.current_idem_key(),
                "purpose": frames[0].purpose}

    _use_tools(monkeypatch, delivery_count)
    rc = await call_tool.run(["delivery_count", "--args", '{"sku": "sakthi_curd_1l"}', "--image", str(img)])
    lines = _lines(capsys.readouterr().out)
    assert rc == 0
    assert lines[0] == {"t": "request_frame", "purpose": "count", "burst": {"count": 2, "intervalMs": 0}}
    assert lines[-1]["say"] == "sakthi_curd_1l: 2 frames" and lines[-1]["purpose"] == "count"
    assert len(lines[-1]["idem"]) == 32


async def test_fixed_session_and_call_id_replay_the_same_idem_key(monkeypatch, capsys):
    async def stock_query(product: str | None = None) -> dict:
        return {"say": "ok", "idem": ids.current_idem_key(), "session": get_session().session_id}

    _use_tools(monkeypatch, stock_query)
    argv = ["stock_query", "--session-id", "smoke1", "--call-id", "cli-1"]
    assert await call_tool.run(argv) == 0
    first = _lines(capsys.readouterr().out)[-1]
    assert await call_tool.run(argv) == 0
    second = _lines(capsys.readouterr().out)[-1]
    assert first["session"] == "smoke1"
    assert first["idem"] == second["idem"] == ids.idem_key("smoke1", "cli-1")


async def test_each_run_gets_a_fresh_call_id_by_default(monkeypatch, capsys):
    async def stock_query(product: str | None = None) -> dict:
        return {"say": "ok", "idem": ids.current_idem_key()}

    _use_tools(monkeypatch, stock_query)
    argv = ["stock_query", "--session-id", "smoke1"]
    await call_tool.run(argv)
    await call_tool.run(argv)
    keys = [line["idem"] for line in _lines(capsys.readouterr().out)]
    assert len(set(keys)) == 2


async def test_confirm_yes_commits_once_through_confirm_action(monkeypatch, capsys):
    committed = []

    async def commit(payload, value):
        committed.append(payload)
        return {"ok": True, "say": "Order sent."}

    monkeypatch.setitem(confirm._COMMITTERS, "cli.test", commit)

    async def order_create(supplier: str, items: list[dict]) -> dict:
        return await confirm.propose("order", f"Order from {supplier}?", {"items": items}, "cli.test")

    _use_tools(monkeypatch, order_create)
    rc = await call_tool.run(["order_create", "--args",
                              '{"supplier": "Sakthi vendor", "items": [{"sku": "x", "qty": 30}]}', "--confirm", "yes"])
    lines = _lines(capsys.readouterr().out)
    assert rc == 0 and committed == [{"items": [{"sku": "x", "qty": 30}]}]
    assert lines[0]["t"] == "confirm" and lines[0]["prompt"] == "Order from Sakthi vendor?"
    assert lines[1]["needs_confirm"] is True and lines[1]["confirm_id"] == lines[0]["id"]
    assert lines[-1]["status"] == "committed" and lines[-1]["say"] == "Order sent."


async def test_confirm_no_rejects_without_committing(monkeypatch, capsys):
    committed = []

    async def commit(payload, value):
        committed.append(payload)
        return {"ok": True, "say": "Paid."}

    monkeypatch.setitem(confirm._COMMITTERS, "cli.test", commit)

    async def supplier_pay(order_id: str, method: str) -> dict:
        return await confirm.propose("payment", "Pay 600 rupees cash?", {"order_id": order_id}, "cli.test")

    _use_tools(monkeypatch, supplier_pay)
    rc = await call_tool.run(["supplier_pay", "--args", '{"order_id": "o1", "method": "cash"}', "--confirm", "no"])
    assert rc == 0 and committed == []
    assert _lines(capsys.readouterr().out)[-1]["status"] == "rejected"


async def test_confirm_flag_on_a_result_that_needs_no_confirmation(monkeypatch, capsys):
    async def stock_query(product: str | None = None) -> dict:
        return {"say": "Twelve left."}

    _use_tools(monkeypatch, stock_query)
    assert await call_tool.run(["stock_query", "--confirm", "yes"]) == 0
    captured = capsys.readouterr()
    assert _lines(captured.out) == [{"say": "Twelve left."}]
    assert "nothing to confirm" in captured.err


async def test_preloaded_images_take_the_tools_default_purpose(tmp_path, monkeypatch, capsys):
    img = tmp_path / "pack.jpg"
    img.write_bytes(b"\xff\xd8pack\xff\xd9")

    async def product_identify() -> dict:
        frames = get_session().frames.latest(5, purpose="identify")
        return {"say": "ok", "frames": len(frames)}

    _use_tools(monkeypatch, product_identify)
    assert await call_tool.run(["product_identify", "--image", str(img)]) == 0
    assert _lines(capsys.readouterr().out)[-1] == {"say": "ok", "frames": 1}


async def test_unknown_tool_exits_2_and_lists_the_tools(monkeypatch, capsys):
    monkeypatch.setattr(call_tool, "load_tools", lambda: {"stock_query": None, "day_summary": None})
    assert await call_tool.run(["nope"]) == 2
    assert "available: day_summary, stock_query" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["not json", "[1]"])
async def test_args_that_are_not_a_json_object_exit_2(bad, capsys):
    assert await call_tool.run(["day_summary", "--args", bad]) == 2
    assert "--args" in capsys.readouterr().err


async def test_says_which_mode_it_runs_in_on_stderr(monkeypatch, capsys):
    async def day_summary() -> dict:
        return {"say": "ok"}

    _use_tools(monkeypatch, day_summary)
    await call_tool.run(["day_summary"])
    assert "mock mode" in capsys.readouterr().err
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    await call_tool.run(["day_summary", "--shop", "murugan_dairy"])
    err = capsys.readouterr().err
    assert "REAL mode" in err and "murugan_dairy" in err


def test_load_tools_is_the_live_registry():
    assert call_tool.load_tools() == TOOLS_BY_NAME
    assert "confirm_action" in call_tool.load_tools()


def test_main_returns_quietly_on_success_and_exits_with_the_error_code(monkeypatch, capsys):
    async def day_summary() -> dict:
        return {"say": "ok"}

    _use_tools(monkeypatch, day_summary)
    call_tool.main(["day_summary"])  # success: no SystemExit
    assert _lines(capsys.readouterr().out) == [{"say": "ok"}]
    with pytest.raises(SystemExit) as exc:
        call_tool.main(["missing_tool"])
    assert exc.value.code == 2


async def test_missing_image_exits_2_before_running_the_tool(monkeypatch, capsys):
    ran = []

    async def product_identify() -> dict:
        ran.append(True)
        return {"say": "ok"}

    _use_tools(monkeypatch, product_identify)
    assert await call_tool.run(["product_identify", "--image", "/no/such/pack.jpg"]) == 2
    assert "no such image" in capsys.readouterr().err and ran == []
