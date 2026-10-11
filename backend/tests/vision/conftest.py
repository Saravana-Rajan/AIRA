"""Shared vision test fixtures: a scripted fake Gemini client and a tiny camera frame.

``aira_live.vision.gemini`` is imported inside the fixture, not at module level, so a problem in that one
module never stops the other vision tests in this folder from being collected.
"""
import asyncio
import base64
from types import SimpleNamespace

import pytest

from aira_live.frames import Frame

TINY_JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\xff\xd9").decode()


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeAioModels:
    """Scripted replies: str -> reply text, float -> hang that long (simulates a timeout), Exception -> raise."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def generate_content(self, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, float):
            await asyncio.sleep(item)
            return FakeResp("{}")
        return FakeResp(item)


class FakeClient:
    def __init__(self, script):
        self.aio = SimpleNamespace(models=FakeAioModels(script))


@pytest.fixture
def fake_genai(monkeypatch):
    from aira_live.vision import gemini

    for role in ("POINT", "COUNT", "READ", "VERIFY", "LIVE"):
        monkeypatch.delenv(f"AIRA_MODEL_{role}", raising=False)

    def make(*script):
        client = FakeClient(script)
        gemini.set_client(client)
        return client

    yield make
    gemini.set_client(None)


@pytest.fixture
def frame():
    return Frame(id="f1", jpeg_b64=TINY_JPEG_B64, w=640, h=480, ts=1.0, purpose="count")
