"""End-to-end voice smoke check: ask aira-live one question over ``/ws`` and report what comes back.

Usage (steps and expected output: ``docs/ops/voice-smoke-test.md``):
    uv run python -m aira_live.devtools.ws_smoke ws://localhost:8080/ws question_16k.wav
    uv run python -m aira_live.devtools.ws_smoke wss://<cloud-run-host>/ws --text "How much Sakthi curd do we have?"

- Connects like the phone: the ``Origin`` of the hosting site (``--origin``; the server refuses an upgrade
  without an allowed one) and a ``hello`` whose ``idToken`` is the shared demo key from env ``AIRA_APP_KEY``
  (never on the command line; the server must run with ``AIRA_ALLOW_NO_TOKEN=1`` or in mock mode, and always
  serves its demo shop), then waits for ``ready``.
- Asks the question either as a 16 kHz mono PCM16 WAV streamed in 40 ms ``audio`` chunks at real-time pace
  (then 1.6 s of silence so Live's voice activity detection ends the turn), or as a typed ``text`` message.
- Prints every server message except audio, then the seconds of 24 kHz audio AIRA spoke and the first-audio
  latency: from the end of the question (last speech chunk, or the text) to AIRA's first audio chunk.
- ``--runs N`` repeats the check on fresh connections and prints the p50 first-audio latency.

Exit code 0 when every run got ``ready``, no ``error`` and, unless the server is in mock mode, some audio.
"""
import argparse
import asyncio
import base64
import json
import os
import statistics
import sys
import time
import typing
import wave
from dataclasses import dataclass, field
from pathlib import Path

import websockets

from aira_live.contracts import Lang

SAMPLE_RATE_IN = 16000
CHUNK_BYTES = 1280  # 40 ms of 16 kHz mono PCM16
CHUNK_S = 0.04
SILENCE_CHUNKS = 40  # 1.6 s of silence after the question
BYTES_PER_S_OUT = 48000  # 24 kHz mono PCM16
DEFAULT_ORIGIN = "https://aira-shop.web.app"  # the hosting site, always in the server's allowlist


@dataclass
class SmokeResult:
    ready: dict | None = None
    messages: list[dict] = field(default_factory=list)  # every server message except audio, in order
    audio_bytes: int = 0
    first_audio_ms: float | None = None  # end of the question -> first AIRA audio chunk

    @property
    def errors(self) -> list[dict]:
        return [m for m in self.messages if m.get("t") == "error"]

    @property
    def mock(self) -> bool:
        return bool(self.ready and self.ready.get("mock"))

    @property
    def audio_seconds(self) -> float:
        return self.audio_bytes / BYTES_PER_S_OUT

    @property
    def ok(self) -> bool:
        return self.ready is not None and not self.errors and (self.mock or self.audio_bytes > 0)


def read_question_wav(path: str | Path) -> bytes:
    """The PCM16 samples of a 16 kHz mono PCM16 WAV; ``ValueError`` for any other format."""
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SAMPLE_RATE_IN, 1, 2):
            raise ValueError(f"{path}: need a 16 kHz mono PCM16 WAV "
                             f"(got {w.getframerate()} Hz, {w.getnchannels()} ch, {8 * w.getsampwidth()}-bit); "
                             f"convert with: ffmpeg -i in.m4a -ar 16000 -ac 1 -sample_fmt s16 out.wav")
        return w.readframes(w.getnframes())


def _audio_msg(pcm: bytes) -> str:
    return json.dumps({"t": "audio", "pcm16": base64.b64encode(pcm).decode()})


async def smoke(url: str, *, wav: str | Path | None = None, text: str | None = None,
                shop: str = "sunrise_dairy", lang: str = "en", idle_s: float = 10.0,
                ready_timeout_s: float = 20.0, pace: bool = True, uid: str = "smoke",
                origin: str = DEFAULT_ORIGIN, app_key: str | None = None) -> SmokeResult:
    """Ask one question (a WAV or typed ``text``, exactly one) and collect the answer.

    The answer is over when no server message arrived for ``idle_s`` seconds after the question, or when the
    server closes the socket. ``pace=False`` streams the WAV as fast as possible (tests only). ``app_key``
    defaults to env ``AIRA_APP_KEY`` and is sent as ``hello.idToken``; ``origin`` is the Origin header.
    """
    if app_key is None:
        app_key = os.getenv("AIRA_APP_KEY") or None
    if (wav is None) == (text is None):
        raise ValueError("give exactly one of wav or text")
    pcm = read_question_wav(wav) if wav is not None else b""
    res = SmokeResult()
    loop = asyncio.get_running_loop()
    got_reply = asyncio.Event()  # ready or error after hello (or the socket closed)
    last_msg_at = loop.time()
    question_end: float | None = None

    async with websockets.connect(url, max_size=2 ** 23, origin=origin) as ws:
        async def receive() -> None:
            nonlocal last_msg_at
            try:
                async for raw in ws:
                    now = loop.time()
                    last_msg_at = now
                    m = json.loads(raw)
                    if m.get("t") == "audio":
                        res.audio_bytes += len(base64.b64decode(m.get("pcm24", "")))
                        if res.first_audio_ms is None and question_end is not None:
                            res.first_audio_ms = (now - question_end) * 1000
                        continue
                    res.messages.append(m)
                    if m.get("t") == "ready" and res.ready is None:
                        res.ready = m
                    if m.get("t") in ("ready", "error"):
                        got_reply.set()
            except websockets.ConnectionClosed:
                pass
            finally:
                got_reply.set()

        receiver = asyncio.create_task(receive())
        try:
            hello = {"t": "hello", "uid": uid, "shopId": shop, "lang": lang, "verbosity": "short",
                     "appVersion": "ws-smoke"}
            if app_key:
                hello["idToken"] = app_key
            await ws.send(json.dumps(hello))
            try:
                await asyncio.wait_for(got_reply.wait(), timeout=ready_timeout_s)
            except TimeoutError:
                return res
            if res.ready is None:  # refused (error) or closed
                await asyncio.wait({receiver}, timeout=1.0)
                return res

            if text is not None:
                await ws.send(json.dumps({"t": "text", "text": text}))
                question_end = loop.time()
            else:
                for i in range(0, len(pcm), CHUNK_BYTES):
                    await ws.send(_audio_msg(pcm[i:i + CHUNK_BYTES]))
                    if pace:
                        await asyncio.sleep(CHUNK_S)
                question_end = loop.time()
                for _ in range(SILENCE_CHUNKS):
                    await ws.send(_audio_msg(b"\x00" * CHUNK_BYTES))
                    if pace:
                        await asyncio.sleep(CHUNK_S)

            last_msg_at = max(last_msg_at, question_end)
            while not receiver.done() and loop.time() - last_msg_at < idle_s:
                await asyncio.sleep(min(0.05, idle_s))
        except websockets.ConnectionClosed:
            pass
        finally:
            receiver.cancel()
            try:
                await receiver
            except asyncio.CancelledError:
                pass
    return res


def p50(values: list[float | None]) -> float | None:
    """The median of the measured values (``None`` entries are runs without audio); ``None`` if there are none."""
    measured = [v for v in values if v is not None]
    return statistics.median(measured) if measured else None


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m aira_live.devtools.ws_smoke",
                                 description="Ask aira-live one question over /ws and report the answer.")
    ap.add_argument("url", help="ws://localhost:8080/ws or wss://<cloud-run-host>/ws")
    ap.add_argument("wav", nargs="?", help="the question as a 16 kHz mono PCM16 WAV")
    ap.add_argument("--text", help="ask this typed question instead of a WAV")
    ap.add_argument("--shop", default="sunrise_dairy", help="shopId sent in hello (the server picks the shop)")
    ap.add_argument("--lang", default="en", choices=typing.get_args(Lang))
    ap.add_argument("--idle", type=float, default=10.0, help="seconds of silence from the server that end a run")
    ap.add_argument("--runs", type=int, default=1, help="repeat on fresh connections and print the p50 latency")
    ap.add_argument("--origin", default=DEFAULT_ORIGIN,
                    help="Origin header; must be in the server's AIRA_ALLOWED_ORIGINS (the app key comes from env "
                         "AIRA_APP_KEY, never from the command line)")
    return ap


def _parse(argv: list[str]) -> argparse.Namespace:
    ap = build_parser()
    ns = ap.parse_args(argv)
    if (ns.wav is None) == (ns.text is None):
        ap.error("give a WAV file or --text, not both")
    if ns.runs < 1:
        ap.error("--runs must be at least 1")
    return ns


def _report(n: int, res: SmokeResult) -> None:
    for m in res.messages:
        print(json.dumps(m, ensure_ascii=False))
    if res.ready is None:
        print(f"run {n}: no ready from the server (hello refused or timed out)")
        return
    if res.mock:
        print(f"run {n}: the server is in MOCK mode (no GEMINI_API_KEY): no voice answer is expected")
    latency = f"{res.first_audio_ms:.0f} ms" if res.first_audio_ms is not None else "none"
    print(f"run {n}: received {res.audio_bytes} bytes of 24 kHz audio (~{res.audio_seconds:.1f} s); "
          f"first audio {latency}; {len(res.errors)} errors; {'OK' if res.ok else 'FAILED'}")


async def run(argv: list[str]) -> int:
    try:
        ns = _parse(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    results = []
    for n in range(1, ns.runs + 1):
        try:
            res = await smoke(ns.url, wav=ns.wav, text=ns.text, shop=ns.shop, lang=ns.lang, idle_s=ns.idle,
                              origin=ns.origin)
        except ValueError as exc:  # a WAV in the wrong format
            print(f"ws_smoke: {exc}", file=sys.stderr)
            return 2
        except (OSError, websockets.InvalidURI, websockets.InvalidHandshake) as exc:
            print(f"run {n}: cannot reach {ns.url}: {exc}")
            res = SmokeResult()
        else:
            _report(n, res)
        results.append(res)
    if ns.runs > 1:
        mid = p50([r.first_audio_ms for r in results])
        print(f"p50 first-audio latency: {f'{mid:.0f} ms' if mid is not None else 'none'} over {ns.runs} runs")
    return 0 if all(r.ok for r in results) else 1


def main(argv: list[str] | None = None) -> None:
    rc = asyncio.run(run(sys.argv[1:] if argv is None else argv))
    if rc:
        raise SystemExit(rc)


if __name__ == "__main__":
    main()
