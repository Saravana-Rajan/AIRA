"""Diagnostic logging for Cloud Logging: one JSON line per event on stdout.

Cloud Run parses a JSON line written to stdout as ``jsonPayload``, so every event can be filtered with
``jsonPayload.aira="tool"`` and the like. On by default; env ``AIRA_DIAG=0`` switches it off.

Never log secrets, tokens, raw audio or image bytes. ``diag`` drops the value of every key in ``REDACT_KEYS``
at any depth (keeping only its size), and callers log sizes and metadata, using ``summarize`` / ``trunc`` for
free-form values (tool arguments, spoken lines) so a line stays short.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

MAX_STR = 80            # free-form strings (tool args, spoken lines) are cut here
MAX_CLIENT_DATA = 2048  # bytes of a client "log" message's data that are kept
MAX_LIST = 10
MAX_DEPTH = 4
REDACT_KEYS = frozenset({
    "jpeg", "jpeg_b64", "frame_b64", "pcm16", "pcm24", "audio", "image", "bytes_b64",
    "idtoken", "id_token", "token", "key", "api_key", "apikey", "secret", "password", "authorization",
    "gemini_api_key", "app_key", "job_token",
})


_suppressed = 0  # > 0 inside ``suppressed()``: a devtool whose stdout is parsed (call_tool) runs


def enabled() -> bool:
    return _suppressed == 0 and os.getenv("AIRA_DIAG", "1") != "0"


@contextmanager
def suppressed() -> Iterator[None]:
    """No diag lines while the block runs (a CLI that prints its own JSON to stdout). Re-entrant."""
    global _suppressed
    _suppressed += 1
    try:
        yield
    finally:
        _suppressed -= 1


def trunc(value: Any, n: int = MAX_STR) -> str:
    """``value`` as text, cut to ``n`` characters with a marker saying how much was dropped."""
    s = value if isinstance(value, str) else str(value)
    return s if len(s) <= n else f"{s[:n]}…(+{len(s) - n})"


def _is_secret(key: Any) -> bool:
    return isinstance(key, str) and key.lower() in REDACT_KEYS


def _redacted(value: Any) -> str:
    if isinstance(value, str) and value.startswith("<redacted "):
        return value  # already redacted by summarize(): keep the original size
    size = len(value) if isinstance(value, (str, bytes, list, tuple, dict)) else 0
    return f"<redacted {size}>"


def summarize(value: Any, depth: int = 0) -> Any:
    """``value`` with every string cut to MAX_STR, secret keys redacted and lists cut to MAX_LIST items."""
    if depth > MAX_DEPTH:
        return "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return trunc(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        return {str(k): (_redacted(v) if _is_secret(k) else summarize(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out = [summarize(v, depth + 1) for v in items[:MAX_LIST]]
        if len(items) > MAX_LIST:
            out.append(f"…(+{len(items) - MAX_LIST})")
        return out
    return trunc(repr(value))


def redact(value: Any, depth: int = 0) -> Any:
    """Only the secret keys replaced (other values kept whole), for structured fields such as client log data."""
    if depth > MAX_DEPTH * 2:
        return "…"
    if isinstance(value, dict):
        return {str(k): (_redacted(v) if _is_secret(k) else redact(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


def cap_json(data: Any, limit: int = MAX_CLIENT_DATA) -> Any:
    """``data`` itself when its JSON fits in ``limit`` bytes, else a marker with its size and its first bytes."""
    try:
        s = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        s = repr(data)
    size = len(s.encode("utf-8"))
    if size <= limit:
        return data
    return {"_truncated": True, "size": size, "head": s[: limit // 4]}


class RateLimiter:
    """At most ``max_per_s`` events in any wall-clock second; the rest are counted in ``dropped``."""

    def __init__(self, max_per_s: int = 20, clock=time.monotonic):
        self.max_per_s = max_per_s
        self._clock = clock
        self._bucket = -1
        self._n = 0
        self.dropped = 0

    def allow(self, now: float | None = None) -> bool:
        now = self._clock() if now is None else now
        bucket = int(now)
        if bucket != self._bucket:
            self._bucket, self._n = bucket, 0
        self._n += 1
        if self._n > self.max_per_s:
            self.dropped += 1
            return False
        return True


# Free-form user content (what the owner said or typed, prompts, spoken replies). Logged only when
# AIRA_DIAG_CONTENT=1 (turn it on briefly to debug); otherwise only its length is kept.
CONTENT_KEYS = frozenset({"text", "prompt", "say", "answer", "value", "message_text"})


def content_enabled() -> bool:
    return os.environ.get("AIRA_DIAG_CONTENT", "0") == "1"


def _strip_content(fields: dict) -> dict:
    if content_enabled():
        return fields
    return {k: (f"<{len(v)} chars>" if k in CONTENT_KEYS and isinstance(v, str) else v) for k, v in fields.items()}


def diag(event: str, /, *, severity: str = "INFO", **fields: Any) -> None:
    """Write one JSON line ``{"severity": ..., "aira": event, "ts": ..., **fields}`` to stdout. Never raises.

    ``event`` is positional-only, so a field may be called ``event`` too (``diag("ws", event="open")``).
    User content (CONTENT_KEYS) is reduced to its length unless AIRA_DIAG_CONTENT=1.
    """
    if not enabled():
        return
    try:
        rec = {"severity": severity, "aira": event, "ts": round(time.time(), 3), **redact(_strip_content(fields))}
        print(json.dumps(rec, ensure_ascii=False, default=str), flush=True)
    except Exception:
        pass
