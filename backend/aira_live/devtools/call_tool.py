"""Run one Live tool from the shell, the way a Gemini Live call runs it (the real-mode tool harness).

Usage:
    uv run python -m aira_live.devtools.call_tool <tool> --args '<json>' [--image a.jpg ...] [--purpose count]
        [--shop sunrise_dairy] [--lang en] [--confirm yes|no] [--session-id S] [--call-id C]

- The tool runs through ``tool_runtime.wrap_tool`` with a harness Session, so ``get_session()``,
  ``ids.current_idem_key()``, ``request_frames`` and ``confirm.propose`` behave as in a Live call. Each run gets a
  fresh tool-call id; ``--session-id`` + ``--call-id`` pin the idempotency key, to replay a call and check that
  its commit happens once.
- Prints every ServerMsg the tool sends (one JSON per line), then the tool's return dict (one line).
- ``--confirm yes|no`` answers a ``needs_confirm`` result through ``confirm_action``, as if the owner said it,
  and prints that result as the last line.
- Images are loaded as frames of ``--purpose`` before the tool runs (for ``frames.latest``); the default purpose
  is the tool's usual one (``count`` for ``delivery_count``, ...), else ``ask``. When the tool asks the phone for
  frames (``request_frame``), the harness answers like the phone would: it adds the images again with fresh
  timestamps and the requested purpose. With no ``--image`` the request times out, as with a covered camera.
- Mode (printed on stderr): mock without ``GEMINI_API_KEY`` or with ``AIRA_MOCK=1``. In REAL mode the tools call
  Gemini and read and write Firestore database ``aira`` (env ``GCP_PROJECT`` + gcloud ADC or
  ``GOOGLE_APPLICATION_CREDENTIALS``) for ``--shop``. Commits are real: answer ``--confirm yes`` with care.
"""
import argparse
import asyncio
import base64
import itertools
import json
import sys
import time
import typing
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace

from pydantic import BaseModel

from aira_live import config
from aira_live.contracts import ConfirmAnswer, FramePurpose, Lang, RequestFrameMsg, dump_server_msg
from aira_live.frames import Frame
from aira_live.session import Session
from aira_live.tool_runtime import wrap_tool
from aira_live.tools import TOOLS_BY_NAME
from aira_live.tools.core import confirm_action

ToolFn = Callable[..., Awaitable[dict]]

# The frame purpose each camera tool usually asks for, used for --image when --purpose is not given.
DEFAULT_PURPOSE: dict[str, str] = {
    "delivery_count": "count", "product_find": "point", "product_identify": "identify",
    "invoice_check": "invoice", "supplier_pay": "cash", "sale_pay": "cash",
}


def load_tools() -> dict[str, ToolFn]:
    """The Live tools by name: the same registry ``main.py`` hands to Gemini Live."""
    return dict(TOOLS_BY_NAME)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m aira_live.devtools.call_tool",
                                 description="Run one AIRA Live tool the way a Gemini Live call runs it.")
    ap.add_argument("tool", help="Live tool name, e.g. stock_query")
    ap.add_argument("--args", default="{}", help="tool arguments as a JSON object")
    ap.add_argument("--image", action="append", default=[], help="a JPEG to feed as a camera frame (repeatable)")
    ap.add_argument("--purpose", choices=typing.get_args(FramePurpose),
                    help="frame purpose of the --image frames (default: the tool's usual one, else ask)")
    ap.add_argument("--shop", default=config.load().demo_shop_id)
    ap.add_argument("--lang", default="en", choices=typing.get_args(Lang))
    ap.add_argument("--confirm", choices=typing.get_args(ConfirmAnswer),
                    help="answer a needs_confirm result through confirm_action, as if the owner said it")
    ap.add_argument("--session-id", help="fixed session id (default: random), to replay an idempotency key")
    ap.add_argument("--call-id", help="fixed Live tool-call id (default: cli-<random>)")
    return ap


def _print_json(result: object) -> None:
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)


def _mode_line(shop: str) -> str:
    s = config.load()
    if s.mock:
        return f"call_tool: mock mode (no GEMINI_API_KEY, or AIRA_MOCK=1); shop {shop}"
    return (f"call_tool: REAL mode: tools call Gemini and read/write Firestore database "
            f"'{s.firestore_database}' for shop {shop}")


async def _call(tool: ToolFn, args: dict, ns: argparse.Namespace) -> None:
    jpegs = [base64.b64encode(Path(p).read_bytes()).decode() for p in ns.image]
    frame_ids = itertools.count()
    last_ts = 0.0

    def add_frames(n: int, frame_purpose: str) -> None:
        nonlocal last_ts
        for jpeg in itertools.islice(itertools.cycle(jpegs), n):
            last_ts = max(time.time() * 1000, last_ts + 1)  # strictly newer than every frame already stored
            s.frames.add(Frame(id=f"img{next(frame_ids)}", jpeg_b64=jpeg, w=0, h=0, ts=last_ts,
                               purpose=frame_purpose))

    async def send(msg: BaseModel) -> None:
        print(dump_server_msg(msg), flush=True)
        if isinstance(msg, RequestFrameMsg) and jpegs and not msg.stop:
            add_frames(msg.burst.count if msg.burst else 1, msg.purpose)

    s = Session(uid="devtools", shop_id=ns.shop, lang=ns.lang, verbosity="short", send=send)
    if ns.session_id:
        s.session_id = ns.session_id
    add_frames(len(jpegs), ns.purpose or DEFAULT_PURPOSE.get(ns.tool, "ask"))

    call_id = ns.call_id or f"cli-{uuid.uuid4().hex[:8]}"
    result = await wrap_tool(tool, s)(**args, tool_context=SimpleNamespace(function_call_id=call_id))
    _print_json(result)
    if not ns.confirm:
        return
    if not (isinstance(result, dict) and result.get("needs_confirm") and result.get("confirm_id")):
        print("call_tool: nothing to confirm (the result has no needs_confirm)", file=sys.stderr)
        return
    answer = await wrap_tool(confirm_action, s)(confirm_id=result["confirm_id"], answer=ns.confirm,
                                                tool_context=SimpleNamespace(function_call_id=f"{call_id}-confirm"))
    _print_json(answer)


async def run(argv: list[str]) -> int:
    """Run the harness; returns the exit code (0 ok, 2 bad input). A tool that raises propagates."""
    try:
        ns = build_parser().parse_args(argv)
    except SystemExit as exc:  # --help, or a bad flag (argparse already said why)
        return exc.code if isinstance(exc.code, int) else 2
    try:
        args = json.loads(ns.args)
    except ValueError as e:
        print(f"call_tool: --args is not valid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(args, dict):
        print("call_tool: --args must be a JSON object", file=sys.stderr)
        return 2
    tools = load_tools()
    if ns.tool not in tools:
        print(f"call_tool: unknown tool {ns.tool}; available: {', '.join(sorted(tools))}", file=sys.stderr)
        return 2
    missing = [p for p in ns.image if not Path(p).is_file()]
    if missing:
        print(f"call_tool: no such image: {', '.join(missing)}", file=sys.stderr)
        return 2
    print(_mode_line(ns.shop), file=sys.stderr, flush=True)
    await _call(tools[ns.tool], args, ns)
    return 0


def main(argv: list[str] | None = None) -> None:
    """CLI entry: returns quietly on success, exits with the code otherwise."""
    rc = asyncio.run(run(sys.argv[1:] if argv is None else argv))
    if rc:
        raise SystemExit(rc)


if __name__ == "__main__":
    main()
