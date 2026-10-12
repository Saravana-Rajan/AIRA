"""Proactive audio is on by default in the Live run config and can be switched off by env."""
from google.adk.agents.run_config import RunConfig
from google.genai import types

from aira_live.live_bridge import live_extras


def test_proactive_audio_on_by_default(monkeypatch):
    monkeypatch.delenv("AIRA_PROACTIVE_AUDIO", raising=False)
    extras = live_extras(types)
    assert extras["proactivity"].proactive_audio is True
    RunConfig(response_modalities=["AUDIO"], **extras)  # ADK accepts it


def test_proactive_audio_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("AIRA_PROACTIVE_AUDIO", "0")
    assert live_extras(types) == {}
