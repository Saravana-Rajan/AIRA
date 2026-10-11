"""web_search: Google Search grounding on the read model through generate_content, behind a fake client."""
from types import SimpleNamespace

import pytest

from aira_live.tools import TOOLS_BY_NAME
from aira_live.tools import search as search_mod
from aira_live.tools.search import web_search
from aira_live.vision import gemini


class FakeModels:
    def __init__(self, text="Chennai is 34°C and sunny today.", chunks=None, raise_exc=None):
        self.calls = []
        self._text, self._chunks, self._raise = text, chunks, raise_exc

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._raise:
            raise self._raise
        web = [SimpleNamespace(web=SimpleNamespace(title=t, uri=f"https://{t}")) for t in (self._chunks or [])]
        gm = SimpleNamespace(grounding_chunks=web) if self._chunks is not None else None
        return SimpleNamespace(text=self._text, candidates=[SimpleNamespace(grounding_metadata=gm)])


class FakeClient:
    def __init__(self, models):
        self.aio = SimpleNamespace(models=models)


@pytest.fixture
def fake_models():
    models = FakeModels(chunks=["weather.com", "imd.gov.in", "weather.com"])
    gemini.set_client(FakeClient(models))
    yield models
    gemini.set_client(None)


def test_web_search_is_a_registered_live_tool():
    assert TOOLS_BY_NAME["web_search"] is web_search


async def test_web_search_uses_the_read_model_with_google_search_grounding(fake_models, monkeypatch):
    monkeypatch.setattr(search_mod, "model_for", lambda role: {"read": "read-model"}[role])
    out = await web_search("weather in Chennai today")
    call = fake_models.calls[0]
    assert call["model"] == "read-model"
    assert any("weather in Chennai today" in str(c) for c in call["contents"])
    tools = call["config"].tools
    assert len(tools) == 1 and tools[0].google_search is not None
    assert getattr(call["config"], "response_mime_type", None) in (None, "text/plain")
    assert out["ok"] is True
    assert out["answer"] == "Chennai is 34°C and sunny today." and out["say"] == out["answer"]
    assert out["sources"] == ["weather.com", "imd.gov.in"]  # de-duplicated, in order


async def test_web_search_without_grounding_metadata_still_answers():
    models = FakeModels(text="Paris.", chunks=None)
    gemini.set_client(FakeClient(models))
    try:
        out = await web_search("capital of France")
    finally:
        gemini.set_client(None)
    assert out["ok"] is True and out["answer"] == "Paris." and out["sources"] == []


async def test_web_search_failure_never_raises():
    gemini.set_client(FakeClient(FakeModels(raise_exc=RuntimeError("quota"))))
    try:
        out = await web_search("anything")
    finally:
        gemini.set_client(None)
    assert out["ok"] is False and out["answer"] == "" and out["sources"] == [] and out["say"]


async def test_web_search_rejects_an_empty_query():
    out = await web_search("   ")
    assert out["ok"] is False and out["say"]
