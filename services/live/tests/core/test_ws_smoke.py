"""devtools.ws_smoke: the end-to-end voice smoke check, run against a real uvicorn server on localhost.

Offline: mock mode, or real mode with an in-memory Live backend and a fake system prompt. The one real-Gemini test
at the bottom is opt-in (``AIRA_LIVE_TESTS=1`` plus ``GEMINI_API_KEY`` and ``GCP_PROJECT``) and skipped by default.
Also checks that ``docs/ops/voice-smoke-test.md`` only uses flags the two devtools accept.
"""
import os
import re
import socket
import threading
import time
import wave
from pathlib import Path

import pytest
import uvicorn
from live_fakes import FakeBackend

from aira_live import confirm
from aira_live import main as main_mod
from aira_live.devtools import call_tool, ws_smoke
from aira_live.live_bridge import LiveEvent

REPO = Path(__file__).resolve().parents[4]
DOC = REPO / "docs" / "ops" / "voice-smoke-test.md"
QUESTION = "How much Sakthi curd do we have?"
_REAL_KEY = os.getenv("GEMINI_API_KEY")  # read before clean_env removes it, for the opt-in test


def _wav(path: Path, seconds: float = 0.2, rate: int = 16000, channels: int = 1, width: int = 2) -> Path:
    """A silent WAV (a synthetic test fixture, not a recording)."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\x00" * int(rate * seconds) * channels * width)
    return path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GEMINI_API_KEY", "AIRA_MOCK", "AIRA_ALLOW_NO_TOKEN", "AIRA_DEMO_SHOP"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def server():
    """aira-live served by uvicorn on a free localhost port, in a background thread; yields the ws:// URL."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(main_mod.app, log_level="warning", lifespan="off", ws="websockets-sansio"))
    thread = threading.Thread(target=srv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started:
        if time.time() > deadline or not thread.is_alive():
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.02)
    yield f"ws://127.0.0.1:{port}/ws"
    srv.should_exit = True
    thread.join(5)
    sock.close()


class AnsweringBackend(FakeBackend):
    """Live stand-in that answers every typed question with a caption and 0.1 s of audio."""

    async def send_text(self, text: str) -> None:
        await super().send_text(text)
        self.push(LiveEvent("output_transcript", text="Sakthi curd is on the top shelf.", final=True))
        self.push(LiveEvent("audio", audio=b"\x00\x01" * 2400))
        self.push(LiveEvent("turn_complete"))


@pytest.fixture
def real_mode(monkeypatch):
    """GEMINI_API_KEY set (real mode) and tokenless hello allowed, but Live is ``AnsweringBackend`` (no network)."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    previous = confirm._store
    confirm.set_store(confirm.MemoryProposalStore())
    backends: list[AnsweringBackend] = []

    async def fake_prompt(shop_id, lang, verbosity):
        return f"prompt for {shop_id}"

    def factory(session):
        backends.append(AnsweringBackend())
        return backends[-1]

    monkeypatch.setattr(main_mod, "build_system_prompt", fake_prompt)
    monkeypatch.setattr(main_mod.app.state, "backend_factory", factory)
    yield backends
    confirm.set_store(previous)


# ---------- mock mode ----------
async def test_typed_question_in_mock_mode(server):
    res = await ws_smoke.smoke(server, text=QUESTION, idle_s=0.4)
    assert res.ready is not None and res.ready["mock"] is True
    assert any(m["t"] == "transcript" and m["role"] == "aira" and QUESTION in m["text"] for m in res.messages)
    assert res.errors == [] and res.audio_bytes == 0
    assert res.ok  # a mock server never speaks, so no audio is expected


async def test_wav_question_streams_audio_in_mock_mode(server, tmp_path):
    res = await ws_smoke.smoke(server, wav=_wav(tmp_path / "q.wav"), idle_s=0.3, pace=False)
    assert res.ready is not None and res.errors == [] and res.ok


# ---------- real mode, offline Live backend ----------
async def test_real_mode_answer_is_measured(server, real_mode):
    res = await ws_smoke.smoke(server, text=QUESTION, shop="someone_else", idle_s=0.5)
    assert res.ready is not None and res.ready["mock"] is False
    assert real_mode[0].sent_text == [QUESTION]
    assert any(m["t"] == "transcript" and m["text"] == "Sakthi curd is on the top shelf." for m in res.messages)
    assert res.audio_bytes == 4800 and res.audio_seconds == pytest.approx(0.1)
    assert res.first_audio_ms is not None and 0 <= res.first_audio_ms < 5000
    assert res.ok


async def test_refused_hello_is_reported(server, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")  # real mode with tokenless mode off: hello is refused
    res = await ws_smoke.smoke(server, text=QUESTION, idle_s=0.3)
    assert res.ready is None and [m["code"] for m in res.errors] == ["permission"]
    assert not res.ok


# ---------- CLI ----------
async def test_cli_runs_and_reports_the_p50_first_audio_latency(server, real_mode, capsys):
    rc = await ws_smoke.run([server, "--text", QUESTION, "--idle", "0.4", "--runs", "2"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("first audio") >= 2 and "p50 first-audio latency" in out and "over 2 runs" in out


async def test_cli_exits_1_when_the_server_refuses(server, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert await ws_smoke.run([server, "--text", QUESTION, "--idle", "0.3"]) == 1


async def test_cli_says_when_the_server_is_in_mock_mode(server, capsys):
    assert await ws_smoke.run([server, "--text", QUESTION, "--idle", "0.3"]) == 0
    assert "MOCK" in capsys.readouterr().out


# ---------- input checks (no server) ----------
async def test_cli_reports_a_server_it_cannot_reach(capsys):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens on this port now
    assert await ws_smoke.run([f"ws://127.0.0.1:{port}/ws", "--text", QUESTION, "--idle", "0.3"]) == 1
    assert "cannot reach" in capsys.readouterr().out


async def test_wav_must_be_16k_mono_pcm16(tmp_path):
    with pytest.raises(ValueError, match="16 kHz mono PCM16"):
        await ws_smoke.smoke("ws://127.0.0.1:9/ws", wav=_wav(tmp_path / "8k.wav", rate=8000))
    with pytest.raises(ValueError, match="16 kHz mono PCM16"):
        await ws_smoke.smoke("ws://127.0.0.1:9/ws", wav=_wav(tmp_path / "st.wav", channels=2))


async def test_needs_exactly_one_of_wav_or_text(tmp_path):
    with pytest.raises(ValueError):
        await ws_smoke.smoke("ws://127.0.0.1:9/ws")
    with pytest.raises(ValueError):
        await ws_smoke.smoke("ws://127.0.0.1:9/ws", wav=_wav(tmp_path / "q.wav"), text=QUESTION)


async def test_cli_needs_a_wav_or_text_but_not_both(tmp_path, capsys):
    assert await ws_smoke.run(["ws://127.0.0.1:9/ws", str(_wav(tmp_path / "q.wav")), "--text", "hi"]) == 2
    assert await ws_smoke.run(["ws://127.0.0.1:9/ws"]) == 2


def test_p50():
    assert ws_smoke.p50([]) is None
    assert ws_smoke.p50([300.0, None, 100.0, 200.0]) == 200.0


# ---------- the manual smoke-test doc ----------
def _commands(text: str, module: str) -> list[str]:
    joined = re.sub(r"\\\n\s*", " ", text)  # join shell line continuations
    return [line for line in joined.splitlines() if module in line]


def test_doc_uses_only_flags_the_devtools_accept():
    text = DOC.read_text(encoding="utf-8")
    for module, parser in (("aira_live.devtools.call_tool", call_tool.build_parser()),
                           ("aira_live.devtools.ws_smoke", ws_smoke.build_parser())):
        lines = _commands(text, module)
        assert lines, f"the doc shows no {module} command"
        known = set(parser._option_string_actions)
        for line in lines:
            tail = line.split(module, 1)[1]
            for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*", tail):
                assert flag in known, f"{module} has no {flag}: {line.strip()}"


def test_doc_covers_the_deployed_check():
    text = DOC.read_text(encoding="utf-8")
    for needle in ("AIRA_ALLOW_NO_TOKEN=1", "/health", "wss://", "ffmpeg", "p50", "AIRA_LIVE_TESTS=1"):
        assert needle in text, needle


# ---------- opt-in: real Gemini Live + Firestore (skipped by default) ----------
@pytest.mark.skipif(not (os.getenv("AIRA_LIVE_TESTS") == "1" and _REAL_KEY and os.getenv("GCP_PROJECT")),
                    reason="real Gemini Live: set AIRA_LIVE_TESTS=1, GEMINI_API_KEY and GCP_PROJECT")
async def test_real_server_answers_a_typed_question_with_audio(server, monkeypatch):
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    monkeypatch.setenv("GEMINI_API_KEY", _REAL_KEY)
    res = await ws_smoke.smoke(server, text=QUESTION, idle_s=8.0, ready_timeout_s=30.0)
    assert res.ready is not None and res.ready["mock"] is False, res.messages
    assert res.errors == [] and res.audio_bytes > 0 and res.ok
