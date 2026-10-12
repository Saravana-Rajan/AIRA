"""Shared Gemini helper for AIRA vision. Model IDs come ONLY from aira_live.models."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, Literal

from google import genai
from google.genai import types
from pydantic import BaseModel

from aira_live import config, monitor
from aira_live.diag import diag, trunc
from aira_live.models import Role, fallback_for, model_for

log = logging.getLogger(__name__)
Thinking = Literal["low", "medium", "high"]
_client: Any = None


class VisionError(RuntimeError):
    """Primary model, its retry, and the fallback all failed."""


class ToolReply(BaseModel):
    say_first: str
    detail: str = ""
    confidence: float = 0.0
    needs_retake: bool = False
    aim_hint: str = ""

    def to_dict(self) -> dict:
        return self.model_dump()


def set_client(c: Any) -> None:
    """Inject a client (tests). Pass None to reset to the real client."""
    global _client
    _client = c


def client() -> Any:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.load().gemini_api_key)
    return _client


def image_part(jpeg_b64: str) -> types.Part:
    return types.Part.from_bytes(data=base64.b64decode(jpeg_b64), mime_type="image/jpeg")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _balanced(s: str, start: int, open_c: str, close_c: str) -> str | None:
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def extract_json(text: str | None) -> Any:
    """Parse JSON from a model reply that may be fenced or wrapped in prose. Raises ValueError if none."""
    if not text or not text.strip():
        raise ValueError("empty model reply")
    m = _FENCE.search(text)
    candidate = (m.group(1) if m else text).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    starts = [(candidate.find(o), o, c) for o, c in (("[", "]"), ("{", "}")) if candidate.find(o) != -1]
    for start, o, c in sorted(starts):
        chunk = _balanced(candidate, start, o, c)
        if chunk:
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON found in model reply: {text[:80]!r}")


def _config(thinking: Thinking) -> types.GenerateContentConfig:
    base = {"temperature": 0.2, "response_mime_type": "application/json"}
    try:
        # google-genai accepts thinking_level ("low" | "medium" | "high") for Gemini 3.x and Robotics-ER 2.
        return types.GenerateContentConfig(**base, thinking_config=types.ThinkingConfig(thinking_level=thinking))
    except Exception:  # older SDK without thinking_level: still return a JSON-mode config
        return types.GenerateContentConfig(**base)


async def generate_json(role: Role, parts: list, schema_hint: str,
                        thinking: Thinking = "low", timeout_s: float = 6.0) -> dict:
    """Call the role's model for JSON: primary, one retry, then ``fallback_for(role)``.

    The timeout applies to each attempt. A JSON list reply is wrapped as ``{"items": [...]}``, and
    ``"_model"`` names the model that answered. Raises VisionError when every attempt fails (timeout,
    API error, or a reply that is not a JSON object or list).
    """
    contents = [*parts, f"Return ONLY valid JSON, no prose. Schema: {schema_hint}"]
    primary = model_for(role)
    attempts = [primary, primary]
    fb = fallback_for(role)
    if fb and fb != primary:
        attempts.append(fb)
    cfg = _config(thinking)
    last: Exception | None = None
    started = time.perf_counter()
    for attempt, model in enumerate(attempts):
        try:
            resp = await asyncio.wait_for(
                client().aio.models.generate_content(model=model, contents=contents, config=cfg),
                timeout=timeout_s,
            )
            data = extract_json(resp.text)
            if isinstance(data, list):
                out = {"items": data}
            elif isinstance(data, dict):
                out = dict(data)
            else:
                raise ValueError(f"model reply is JSON {type(data).__name__}, not an object or list")
            out["_model"] = model
            ms = round((time.perf_counter() - started) * 1000)
            diag("vision", role=role, model=model, attempt=attempt, parts=len(parts), thinking=thinking,
                 ms=ms, keys=sorted(k for k in out if k != "_model"))
            monitor.count_vision(model, ms)  # the Live page's session stats (ER 2 / Flash calls, latency)
            return out
        except Exception as e:  # timeout, API error, or unparseable reply -> next attempt
            last = e
            log.warning("vision.generate_json role=%s model=%s failed: %r", role, model, e)
            diag("vision_error", severity="WARNING", role=role, model=model, attempt=attempt, parts=len(parts),
                 ms=round((time.perf_counter() - started) * 1000), error=type(e).__name__,
                 message=trunc(str(e), 200))
    raise VisionError(f"{role}: all attempts failed: {last!r}")
