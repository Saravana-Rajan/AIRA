"""The walk's own eyes: a second, navigation-only Gemini Live session (AIRA_WALK_NAV_SESSION, on with AIRA_WALK).

Why a second session. Measured on the real Live API (gemini-3.8-live, 2026-10-14, AIRA's own prompt, tool and
voice settings): in the owner's conversation session a [NAV CHECK] sent while AIRA answered cut her answer every
time, and one in flight while the owner spoke was usually lost. So that session can only check in quiet moments,
and the walk went blind for as long as he talked. A separate Live session that gets the same 1 fps frames and the
same checks answered 29 of 29 checks (0.7-1.8 s) while the owner spoke and AIRA answered, never produced audio,
and never interrupted the conversation.

This session never hears the microphone and never speaks: it gets the camera frames the conversation session gets
and one text check at a time, and answers each with the ``nav_report`` tool, whose response is always SILENT.
It lives only while a walk runs (tools.walk opens and closes it). If it cannot open or drops, the walk falls back
to checking through the conversation session in quiet moments (tools.walk.NavChecker).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aira_live.diag import diag

log = logging.getLogger(__name__)

# The navigation session gets AIRA's own walking prompt (no catalogue) and then this. Measured (real Live,
# 8 checks each): with this short note alone Live saw the fridge in 2 of 32 reports ("none / uncertain"); with
# AIRA's walking prompt in front of it, in most of them ("ahead, clear_in_view").
NAV_SYSTEM = (
    "THIS SESSION: you are only the camera checker of a walking aid for a blind shop owner. You receive his "
    "chest camera as live video. You never speak and nobody hears you. When a message starts with "
    "[NAV CHECK n], look at the newest camera frame and call nav_report exactly once with check_id=n. Report only what you can see; never guess a "
    "side, a size or a clear path: when unsure, path is uncertain. Do nothing else."
)
OPEN_TIMEOUT_S = 8.0
MIN_FRAMES = 2   # frames the session must have before its first check

OnReport = Callable[[dict], Awaitable[None]]


def system_prompt(shop_name: str = "the shop", zones_text: str | None = None) -> str:
    """AIRA's walking prompt, the checker note and, with routes on, the map's zone list (``ShopMap.prompt_text``,
    already cut to its character cap: a small part of the 32k-token compression window)."""
    from aira_live.prompt import render_prompt

    walk_prompt = render_prompt({"name": shop_name}, [], [], "en", "short", er2=False, walk=True)
    text = f"{walk_prompt}\n{NAV_SYSTEM}"
    if zones_text:
        text += ("\n" + zones_text + "\nWith every nav_report also give current_zone (one of the zone ids above, or "
                 "unknown), facing (which of that zone's views the picture matches), confidence and reflection.")
    return text


def _config(types: Any, nav_tool: Any, system: str | None = None) -> Any:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],   # Live answers in audio; this session's audio is never played
        system_instruction=types.Content(parts=[types.Part(text=system or system_prompt())]),
        tools=[types.Tool(function_declarations=[nav_tool])],
        # video sessions end after ~2 min without compression; a walk can last 5
        context_window_compression=types.ContextWindowCompressionConfig(
            trigger_tokens=32_000, sliding_window=types.SlidingWindow(target_tokens=16_000)),
    )


def _nav_declaration(types: Any) -> Any:
    from google.adk.tools import FunctionTool

    from aira_live.tools.walk import nav_report

    decl = FunctionTool(nav_report)._get_declaration()
    decl.behavior = types.Behavior.NON_BLOCKING
    return decl


async def _default_connect(system: str | None = None) -> tuple[Any, contextlib.AsyncExitStack]:
    """Open a Live session on the live model with the shared Gemini client. Returns (session, exit stack)."""
    from google.genai import types

    from aira_live.models import model_for
    from aira_live.vision import gemini

    stack = contextlib.AsyncExitStack()
    cfg = _config(types, _nav_declaration(types), system)
    live = await stack.enter_async_context(gemini.client().aio.live.connect(model=model_for("live"), config=cfg))
    return live, stack


class NavSession:
    """One navigation-only Live connection for one walk. ``alive`` is False until ``start`` succeeds and after
    it drops or closes; callers fall back to the conversation session then. Never raises."""

    def __init__(self, session_id: str, on_report: OnReport,
                 connect: Callable[[], Awaitable[tuple[Any, Any]]] | None = None, system: str | None = None):
        self.session_id = session_id
        self._on_report = on_report
        self.system = system                      # a system instruction override (routes: with the zone list)
        self._connect = connect or (lambda: _default_connect(system))
        self._live: Any = None
        self._stack: Any = None
        self._task: asyncio.Task | None = None
        self.alive = False
        self.frames = 0
        self.checks = 0

    @property
    def ready(self) -> bool:
        """Open and holding a picture: a check sent with the very first frame came back empty on real Live."""
        return self.alive and self.frames >= MIN_FRAMES

    async def start(self) -> bool:
        try:
            self._live, self._stack = await asyncio.wait_for(self._connect(), OPEN_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - no nav session: the walk checks through the conversation
            diag("nav_session", session=self.session_id, event="open_failed", error=type(exc).__name__)
            log.warning("nav session failed to open for %s", self.session_id, exc_info=True)
            return False
        self.alive = True
        self._task = asyncio.create_task(self._receive(), name=f"nav-{self.session_id}")
        diag("nav_session", session=self.session_id, event="open")
        return True

    async def send_frame(self, jpeg: bytes) -> bool:
        if not self.alive:
            return False
        from google.genai import types

        try:
            await self._live.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg"))
        except Exception as exc:  # noqa: BLE001
            await self._lost(exc)
            return False
        self.frames += 1
        return True

    async def request(self, text: str) -> bool:
        """Send one [NAV CHECK n] text. True when it went (the answer arrives through ``on_report``)."""
        if not self.alive:
            return False
        try:
            await self._live.send_realtime_input(text=text)
        except Exception as exc:  # noqa: BLE001
            await self._lost(exc)
            return False
        self.checks += 1
        return True

    async def _receive(self) -> None:
        from google.genai import types

        try:
            while self.alive:
                async for msg in self._live.receive():   # one turn per pass: loop for the next
                    call = getattr(msg, "tool_call", None)
                    for fc in (call.function_calls if call and call.function_calls else []):
                        if fc.name == "nav_report":
                            try:
                                await self._on_report(dict(fc.args or {}))
                            except Exception:  # noqa: BLE001 - a bad report never ends the session
                                log.warning("nav report handling failed for %s", self.session_id, exc_info=True)
                        await self._live.send_tool_response(function_responses=[types.FunctionResponse(
                            id=fc.id, name=fc.name, response={"status": "ok", "retryable": False, "message": ""},
                            scheduling=types.FunctionResponseScheduling.SILENT)])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await self._lost(exc)

    async def _lost(self, exc: BaseException) -> None:
        if not self.alive:
            return
        self.alive = False
        diag("nav_session", session=self.session_id, event="lost", error=type(exc).__name__)
        log.warning("nav session lost for %s: %s", self.session_id, exc)

    async def close(self) -> None:
        was = self.alive
        self.alive = False
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
        if self._stack is not None:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
        self._stack = self._live = None
        if was:
            diag("nav_session", session=self.session_id, event="closed", frames=self.frames, checks=self.checks)
