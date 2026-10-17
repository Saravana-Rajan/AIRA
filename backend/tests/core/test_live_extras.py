"""Proactive audio in the Live run config.

Gemini 3.8 Live has proactive audio and affective dialogue on by default, and its developer guide says not to
pass the legacy ``proactivity`` / ``enable_affective_dialog`` fields. Older Live models (an AIRA_MODEL_LIVE
override) still need proactivity asked for explicitly; AIRA_PROACTIVE_AUDIO=0 turns that request off.
"""
from google.adk.agents.run_config import RunConfig
from google.genai import types

from aira_live.live_bridge import live_extras


def test_gemini_3_8_live_gets_no_legacy_proactivity_field(monkeypatch):
    monkeypatch.delenv("AIRA_PROACTIVE_AUDIO", raising=False)
    monkeypatch.delenv("AIRA_MODEL_LIVE", raising=False)  # the default live model: gemini-3.8-live
    assert live_extras(types) == {}
    assert live_extras(types, model="gemini-3.8-live") == {}
    assert live_extras(types, model="gemini-3.8-live-extended-thinking") == {}


def test_an_older_live_model_still_asks_for_proactive_audio(monkeypatch):
    monkeypatch.delenv("AIRA_PROACTIVE_AUDIO", raising=False)
    extras = live_extras(types, model="gemini-live-2.5-flash-native-audio")
    assert extras["proactivity"].proactive_audio is True
    RunConfig(response_modalities=["AUDIO"], **extras)  # ADK accepts it


def test_proactive_audio_request_can_be_switched_off_for_older_models(monkeypatch):
    monkeypatch.setenv("AIRA_PROACTIVE_AUDIO", "0")
    assert live_extras(types, model="gemini-live-2.5-flash-native-audio") == {}
