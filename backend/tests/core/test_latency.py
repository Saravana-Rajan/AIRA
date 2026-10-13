"""Reply latency: latency diag lines, quick end-of-turn VAD, holding line for background tools, frame reuse,
fast vision attempts and no LLM work on the Live event pump."""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from live_fakes import FakeBackend, wait_until

from aira_live import live_bridge, safety, session as session_mod, tool_runtime
from aira_live.frames import Frame
from aira_live.live_bridge import LiveBridge, LiveEvent, vad_extras
from aira_live.prompt import HOLDING_RULE, RULES
from aira_live.tools import NON_BLOCKING_TOOLS, TOOL_FUNCS
from aira_live.tool_runtime import is_urgent, live_tools
from aira_live.vision import gemini


def _frame(i: int, side: int = 640, purpose: str = "describe") -> Frame:
    return Frame(id=f"f{i}", jpeg_b64="eHh4eA==", w=side, h=side * 3 // 4, ts=float(i), purpose=purpose)


@pytest.fixture
def diag_lines(monkeypatch):
    lines: list[tuple[str, dict]] = []

    def rec(ev, /, **f):
        lines.append((ev, f))

    for mod in (live_bridge, session_mod, tool_runtime, gemini):
        monkeypatch.setattr(mod, "diag", rec)
    return lines


def _latency(lines, stage):
    return [f for ev, f in lines if ev == "latency" and f.get("stage") == stage]


# ---------- 1. measure ----------
async def test_first_audio_after_the_owner_speaks_logs_latency(mk_session, diag_lines, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(live_bridge, "_monotonic", lambda: clock[0])
    s, _ = mk_session()
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    await bridge.start()
    b.push(LiveEvent("input_transcript", text="How much is curd?", final=True))
    await wait_until(lambda: any(ev == "transcript" for ev, _ in diag_lines))
    clock[0] = 101.25
    b.push(LiveEvent("audio", audio=b"\x01\x02"))
    b.push(LiveEvent("audio", audio=b"\x03\x04"))  # only the first chunk of the answer is measured
    await wait_until(lambda: _latency(diag_lines, "first_audio"))
    await asyncio.sleep(0.01)
    (line,) = _latency(diag_lines, "first_audio")
    assert line["ms"] == 1250 and line["ms_after_final"] == 1250
    await bridge.close()


async def test_tool_call_logs_total_latency(mk_session, diag_lines):
    s, _ = mk_session()

    async def stock_query() -> dict:
        """Stock."""
        return {"say": "ok"}

    await tool_runtime.wrap_tool(stock_query, s)()
    (line,) = _latency(diag_lines, "tool")
    assert line["name"] == "stock_query" and line["ok"] is True and isinstance(line["ms"], int)


async def test_frame_wait_is_logged(mk_session, diag_lines):
    s, _ = mk_session()
    assert await s.request_frames("ask", timeout_s=0.05) == []
    (line,) = _latency(diag_lines, "frame_wait")
    assert line["reused"] is False and line["got"] == 0 and line["ms"] >= 40


# ---------- 2. quick turn end (VAD) ----------
def test_vad_ends_the_turn_quickly_and_keeps_proactive_audio():
    from google.adk.agents.run_config import RunConfig
    from google.genai import types

    extras = vad_extras(types)
    aad = extras["realtime_input_config"].automatic_activity_detection
    assert aad.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_HIGH
    assert aad.silence_duration_ms == 500 and aad.prefix_padding_ms == 200 and aad.disabled is False
    cfg = RunConfig(response_modalities=["AUDIO"], **live_bridge.live_extras(types), **extras)
    assert cfg.realtime_input_config is not None


async def test_the_llm_safety_check_never_blocks_the_next_answer(mk_session, monkeypatch):
    s, rec = mk_session()
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    await bridge.start()
    gate = asyncio.Event()

    async def slow_verify(spoken, results):
        await gate.wait()
        return safety.Verdict(ok=True) if hasattr(safety, "Verdict") else SimpleNamespace(ok=True)

    monkeypatch.setattr(safety, "check_numbers", lambda spoken, results: SimpleNamespace(ok=True, needs_llm=True))
    monkeypatch.setattr(safety, "llm_verify", slow_verify)
    s.tool_results.append({"tool": "stock_query", "call_id": "c1", "result": {"say": "Two packs."}})
    b.push(LiveEvent("output_transcript", text="Two packs.", final=True))
    b.push(LiveEvent("turn_complete"))
    b.push(LiveEvent("audio", audio=b"\x09\x09"))  # the next answer, while the verifier still runs
    await wait_until(lambda: any(m.t == "audio" for m in rec.msgs))
    gate.set()
    await bridge.close()


# ---------- 3. instant acknowledgement ----------
def test_slow_vision_tools_run_in_the_background():
    names = {f.__name__ for f in TOOL_FUNCS}
    for slow in ("camera_ask", "product_identify", "product_find", "invoice_check", "supplier_pay", "sale_pay",
                 "delivery_count"):
        assert slow in NON_BLOCKING_TOOLS and slow in names


def test_holding_rule_forbids_numbers_until_the_result():
    assert HOLDING_RULE in RULES
    low = HOLDING_RULE.lower()
    for word in ("holding phrase", "never say a number", "price", "amount", "count", "way is clear", "safe",
                 "let me look", "counting, hold still"):
        assert word in low
    for tool in NON_BLOCKING_TOOLS - {"walk_to"}:  # walk_to has its own rule: it speaks its 'say', then guides
        assert tool in HOLDING_RULE


async def test_path_results_interrupt_and_others_wait_for_idle(mk_session):
    from google.genai import types

    s, _ = mk_session()

    async def camera_ask(question: str) -> dict:
        """Look."""
        return {"say": "Box ahead.", "kind": "path" if "path" in question else "look"}

    (tool,) = live_tools([camera_ask], s, frozenset({"camera_ask"}))
    assert tool.response_scheduling == types.FunctionResponseScheduling.WHEN_IDLE
    assert "question" in tool._get_declaration().model_dump_json()
    ctx = SimpleNamespace(function_call_id="fc1")
    look = await tool.func(question="what is this", tool_context=ctx)
    assert look == {"say": "Box ahead.", "kind": "look"}
    path = await tool.func(question="is the path clear", tool_context=ctx)
    assert isinstance(path, types.FunctionResponse)
    assert path.scheduling == types.FunctionResponseScheduling.INTERRUPT and path.response["kind"] == "path"
    assert s.tool_results[-1]["result"]["kind"] == "path"  # the safety checker still sees the plain dict
    assert is_urgent({"urgent": True}) and not is_urgent({"kind": "count"}) and not is_urgent(None)


# ---------- 4. frame reuse ----------
async def test_a_recent_big_frame_is_reused_without_asking_the_phone(mk_session, diag_lines):
    s, sent = mk_session()
    s.frames.add(_frame(1, 640))
    got = await s.look_frame("ask", timeout_s=0.05)
    assert [f.id for f in got] == ["f1"] and sent.msgs == []
    assert _latency(diag_lines, "frame_wait")[0]["reused"] is True


async def test_old_small_or_preview_frames_are_not_reused(mk_session, monkeypatch):
    s, sent = mk_session()
    s.frames.add(_frame(1, 360, purpose="preview"))
    s.frames.add(_frame(2, 320, purpose="describe"))
    assert await s.look_frame("ask", timeout_s=0.05) == [] and len(sent.msgs) == 1  # asked the phone
    s.frames.add(_frame(3, 640))
    real = time.monotonic
    monkeypatch.setattr("aira_live.frames.time.monotonic", lambda: real() + 1.5)  # now 1.5 s old
    assert await s.look_frame("ask", timeout_s=0.05) == [] and len(sent.msgs) == 2
    monkeypatch.setattr("aira_live.frames.time.monotonic", real)
    assert await s.look_frame("ask", timeout_s=0.05, min_side=1280) == []  # a read needs a full frame


async def test_camera_count_never_reuses_a_frame(mk_session, monkeypatch):
    from aira_live.session import current_session
    from aira_live.tools import camera

    s, _ = mk_session()
    s.frames.add(_frame(1, 1280))
    asked = []

    async def fake_frames(purpose, count=1, interval_ms=0, timeout_s=4.0):
        asked.append((purpose, count))
        return []

    monkeypatch.setattr(s, "request_frames", fake_frames)
    token = current_session.set(s)
    try:
        await camera.camera_ask("how many milk packets are there?", kind="count", item="milk packet")
    finally:
        current_session.reset(token)
    assert asked == [("count", camera.COUNT_FRAMES)]


# ---------- 5. model speed ----------
async def test_look_uses_low_thinking_4s_and_a_single_retry(mk_session, monkeypatch):
    from aira_live.session import current_session
    from aira_live.tools import camera

    s, _ = mk_session()
    s.frames.add(_frame(1, 1280, purpose="ask"))
    seen = {}

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0, max_attempts=3):
        seen.update(role=role, thinking=thinking, timeout_s=timeout_s, max_attempts=max_attempts)
        return {"answer": "A milk packet.", "objects": [], "confidence": 0.9}

    monkeypatch.setattr(camera, "generate_json", fake)
    token = current_session.set(s)
    try:
        out = await camera.camera_ask("what is in front of me?")
    finally:
        current_session.reset(token)
    assert seen == {"role": "point", "thinking": "low", "timeout_s": 4.0, "max_attempts": 2}
    assert out["kind"] == "look"


async def test_generate_json_max_attempts_is_primary_then_one_retry(monkeypatch, diag_lines):
    calls = []

    class Models:
        async def generate_content(self, model, contents, config):
            calls.append(model)
            raise RuntimeError("down")

    monkeypatch.setattr(gemini, "client", lambda: SimpleNamespace(aio=SimpleNamespace(models=Models())))
    monkeypatch.setattr(gemini, "model_for", lambda role: "er2")
    monkeypatch.setattr(gemini, "fallback_for", lambda role: "flash")
    with pytest.raises(gemini.VisionError):
        await gemini.generate_json("point", ["p"], "{}", max_attempts=2)
    assert calls == ["er2", "flash"]
    calls.clear()
    with pytest.raises(gemini.VisionError):
        await gemini.generate_json("point", ["p"], "{}")
    assert calls == ["er2", "er2", "flash"]


async def test_label_read_runs_both_models_at_once(monkeypatch):
    from aira_live.tools import camera

    running, peak = [0], [0]

    async def fake(role, parts, schema_hint, thinking="low", timeout_s=6.0, **_kw):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        await asyncio.sleep(0.01)
        running[0] -= 1
        return {"text": "Full cream milk", "confidence": 0.9}

    monkeypatch.setattr(camera, "generate_json", fake)
    out = await camera._read_twice("eHh4eA==")
    assert peak[0] == 2 and out["agreed"] is True


# ---------- latency report script ----------
def _report_module():
    root = Path(__file__).resolve().parents[3] / "shared" / "scripts"
    sys.path.insert(0, str(root))
    try:
        import latency_report
    finally:
        sys.path.remove(str(root))
    return latency_report


def test_latency_report_percentiles_and_no_hard_coded_project(monkeypatch):
    lr = _report_module()
    rows = [{"jsonPayload": {"aira": "latency", "stage": "first_audio", "ms": ms}} for ms in (100, 200, 300, 400)]
    rows += [{"jsonPayload": {"aira": "latency", "stage": "tool", "name": "camera_ask", "ms": 5000}}]
    rows += [{"jsonPayload": {"aira": "client", "tag": "latency", "data": {"stage": "play_start", "ms": 60}}}]
    table = lr.summarize(rows)
    assert table["first_audio"]["n"] == 4 and table["first_audio"]["p50"] == 200
    assert table["first_audio"]["p90"] == 400
    assert table["tool:camera_ask"]["p50"] == 5000 and table["phone:play_start"]["p50"] == 60
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    with pytest.raises(SystemExit):
        lr.main([])
    src = (Path(lr.__file__)).read_text()
    assert "gemini-enterprise" not in src
    cmd = lr.gcloud_command("my-proj", "1h", 500)
    assert "--project=my-proj" in cmd and any('jsonPayload.aira="latency"' in c for c in cmd)
