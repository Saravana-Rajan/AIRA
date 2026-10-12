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
    for start, o, c in sorted(starts)[:1]:  # the outermost value only; an inner object is not the answer
        chunk = _balanced(candidate, start, o, c)
        if chunk:
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                pass
    salvaged = _salvage_truncated(candidate)
    if salvaged is not None:
        return salvaged
    raise ValueError(f"no JSON found in model reply: {text[:80]!r}")


MAX_OUTPUT_TOKENS = 4096


def _salvage_truncated(text: str) -> Any:
    """Recover a reply cut off mid-JSON: keep every COMPLETE object of the first list, drop the partial tail.
    '{"objects": [{"a": 1}, {"b": 2}, {"c": [2' -> {"objects": [{"a": 1}, {"b": 2}]}; None when nothing is whole."""
    lb = text.find("[")
    if lb == -1:
        return None
    items, depth, start, in_str, esc = [], 0, None, False, False
    for i in range(lb + 1, len(text)):
        ch = text[i]
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
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    items.append(json.loads(text[start:i + 1]))
                except json.JSONDecodeError:
                    return None
                start = None
        elif ch == "]" and depth == 0:
            break
    if not items:
        return None
    head = text[:lb].strip()
    key = re.search(r'"([^"]+)"\s*:\s*$', head)
    return {key.group(1): items} if key and head.startswith("{") else items


def _config(thinking: Thinking, max_output_tokens: int = MAX_OUTPUT_TOKENS,
            json_schema: dict | None = None) -> types.GenerateContentConfig:
    # Box lists (box_2d for every object) can be long; a low default output cap truncated replies mid-JSON.
    # Temperature is left at the model default, as in the official Robotics-ER samples: a low temperature hurts
    # the reasoning of Gemini 3-class models.
    base: dict[str, Any] = {"response_mime_type": "application/json", "max_output_tokens": max_output_tokens}
    if json_schema is not None:
        base["response_json_schema"] = json_schema
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


# --- counting: the official Robotics-ER pointing call ---------------------------------------------------------

class TruncatedReply(VisionError):
    """The reply hit the output cap (or stopped mid-JSON): its point list may be short, so it is never a count."""


COUNT_MAX_OUTPUT_TOKENS = 16384   # thinking eats the 4096 default before a long point list is written
COUNT_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
                    "label": {"type": "string"},
                },
                "required": ["point", "label"],
            },
        },
    },
    "required": ["items"],
}


def _hit_max_tokens(resp: Any) -> bool:
    for cand in getattr(resp, "candidates", None) or []:
        reason = getattr(cand, "finish_reason", None)
        if reason is not None and "MAX_TOKENS" in str(getattr(reason, "name", reason)):
            return True
    return False


def _strict_json(text: str | None) -> Any:
    """JSON from a schema-constrained reply; a fenced reply is accepted, a truncated one is TruncatedReply."""
    if not text or not text.strip():
        raise ValueError("empty model reply")
    m = _FENCE.search(text)
    candidate = (m.group(1) if m else text).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        if _salvage_truncated(candidate) is not None:
            raise TruncatedReply("reply stopped mid-JSON") from None
        raise


async def generate_count_json(parts: list, thinking: Thinking = "medium", timeout_s: float = 25.0) -> dict:
    """The ``count`` role's model (Robotics-ER), constrained by ``COUNT_JSON_SCHEMA``: one attempt plus one
    retry, and NO Flash fallback (a weaker counter must not answer; the caller says "not sure" instead).

    Returns ``{"items": [...], "_model": ...}``. Raises ``TruncatedReply`` when the reply hit the output cap
    (never salvaged: a cut list undercounts) and ``VisionError`` when both attempts fail.
    """
    contents = [*parts]
    model = model_for("count")
    cfg = _config(thinking, max_output_tokens=COUNT_MAX_OUTPUT_TOKENS, json_schema=COUNT_JSON_SCHEMA)
    last: Exception | None = None
    started = time.perf_counter()
    for attempt in range(2):
        try:
            resp = await asyncio.wait_for(
                client().aio.models.generate_content(model=model, contents=contents, config=cfg),
                timeout=timeout_s,
            )
            if _hit_max_tokens(resp):
                raise TruncatedReply("MAX_TOKENS")
            data = _strict_json(resp.text)
            if isinstance(data, list):
                out: dict = {"items": data}
            elif isinstance(data, dict):
                out = dict(data)
            else:
                raise ValueError(f"model reply is JSON {type(data).__name__}, not an object or list")
            out["_model"] = model
            ms = round((time.perf_counter() - started) * 1000)
            diag("vision", role="count", model=model, attempt=attempt, parts=len(parts), thinking=thinking,
                 ms=ms, keys=sorted(k for k in out if k != "_model"))
            monitor.count_vision(model, ms)
            return out
        except TruncatedReply:
            diag("vision_error", severity="WARNING", role="count", model=model, attempt=attempt,
                 ms=round((time.perf_counter() - started) * 1000), error="TruncatedReply")
            raise
        except Exception as e:  # timeout, API error, or unparseable reply -> the one retry
            last = e
            log.warning("vision.generate_count_json model=%s failed: %r", model, e)
            diag("vision_error", severity="WARNING", role="count", model=model, attempt=attempt, parts=len(parts),
                 ms=round((time.perf_counter() - started) * 1000), error=type(e).__name__,
                 message=trunc(str(e), 200))
    raise VisionError(f"count: both attempts failed: {last!r}")
