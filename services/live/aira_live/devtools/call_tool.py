"""Run one Live tool without the phone.

Usage:
    uv run python -m aira_live.devtools.call_tool <tool> --args '<json>' [--image a.jpg ...] [--purpose count]
        [--shop murugan_dairy] [--lang en]

Prints every ServerMsg the tool sends (one JSON per line), then the tool's return dict.

Images are loaded as frames of ``--purpose`` before the tool runs (for ``frames.latest``). When the tool asks the
phone for frames (``request_frame``), the harness answers like the phone would: it adds the images again with
fresh timestamps and the requested purpose. With no ``--image`` the request times out, as with a covered camera.
"""
import argparse
import asyncio
import base64
import itertools
import json
import sys
import time
import typing
from pathlib import Path

from pydantic import BaseModel

from aira_live import config
from aira_live.contracts import FramePurpose, Lang, RequestFrameMsg, dump_server_msg
from aira_live.frames import Frame
from aira_live.session import Session, current_session
from aira_live.tools import TOOLS_BY_NAME


async def _run(name: str, args: dict, images: list[str], purpose: str, shop: str, lang: str) -> dict:
    jpegs = [base64.b64encode(Path(p).read_bytes()).decode() for p in images]
    ids = itertools.count()
    last_ts = 0.0

    def add_frames(n: int, frame_purpose: str) -> None:
        nonlocal last_ts
        for jpeg in itertools.islice(itertools.cycle(jpegs), n):
            last_ts = max(time.time() * 1000, last_ts + 1)  # strictly newer than every frame already stored
            s.frames.add(Frame(id=f"img{next(ids)}", jpeg_b64=jpeg, w=0, h=0, ts=last_ts, purpose=frame_purpose))

    async def send(msg: BaseModel) -> None:
        print(dump_server_msg(msg), flush=True)
        if isinstance(msg, RequestFrameMsg) and jpegs and not msg.stop:
            add_frames(msg.burst.count if msg.burst else 1, msg.purpose)

    s = Session(uid="devtools", shop_id=shop, lang=lang, verbosity="short", send=send)
    current_session.set(s)
    add_frames(len(jpegs), purpose)
    return await TOOLS_BY_NAME[name](**args)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m aira_live.devtools.call_tool", description="Run one AIRA Live tool.")
    ap.add_argument("tool", choices=sorted(TOOLS_BY_NAME))
    ap.add_argument("--args", default="{}", help="tool arguments as a JSON object")
    ap.add_argument("--image", action="append", default=[], help="a JPEG to feed as a camera frame (repeatable)")
    ap.add_argument("--purpose", default="ask", choices=typing.get_args(FramePurpose))
    ap.add_argument("--shop", default=config.load().demo_shop_id)
    ap.add_argument("--lang", default="en", choices=typing.get_args(Lang))
    ns = ap.parse_args(argv)
    try:
        args = json.loads(ns.args)
    except ValueError as e:
        ap.error(f"--args is not valid JSON: {e}")
    if not isinstance(args, dict):
        ap.error("--args must be a JSON object")
    result = asyncio.run(_run(ns.tool, args, ns.image, ns.purpose, ns.shop, ns.lang))
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
