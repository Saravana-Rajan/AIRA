"""Runtime config (replaces Remote Config): Firestore doc config/runtime in database "aira", cached 60 s.

Uses the real ``aira_live.config`` / ``aira_live.models`` and the shared ``fake_db`` fixture (tests/conftest.py).
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from aira_live import models
from aira_live import remote_config as rc

DOC = "config/runtime"


class _Boom:
    """A Firestore client whose every call fails (outage / permission error)."""

    def collection(self, name):
        raise RuntimeError("firestore down")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    rc._reset()
    for role in ("POINT", "COUNT", "LIVE", "READ", "VERIFY"):
        monkeypatch.delenv(f"AIRA_MODEL_{role}", raising=False)
    yield
    rc._reset()


@pytest.fixture
def store(fake_db, monkeypatch) -> dict:
    """The fake Firestore's backing dict, with Firestore reads enabled."""
    monkeypatch.setattr(rc, "_enabled", lambda: True)
    reads: list[str] = []
    real_doc_ref = rc._doc_ref

    def counting_doc_ref():
        ref = real_doc_ref()
        reads.append(ref.path)
        return ref

    monkeypatch.setattr(rc, "_doc_ref", counting_doc_ref)
    fake_db.store["__reads__"] = reads  # type: ignore[assignment]
    return fake_db.store


def _reads(store: dict) -> list[str]:
    return store["__reads__"]


# ---------- parsing ----------

def test_params_are_the_runtime_fields():
    assert rc.PARAMS == ("pointing_model", "count_model", "live_model", "whatsapp_enabled")


def test_parse_doc_keeps_known_non_empty_fields():
    doc = {"pointing_model": "  m1 ", "count_model": "", "live_model": None,
           "whatsapp_enabled": False, "unrelated": "x"}
    assert rc.parse_doc(doc) == {"pointing_model": "m1", "whatsapp_enabled": False}


def test_parse_doc_handles_none():
    assert rc.parse_doc(None) == {}


# ---------- flag ----------

@pytest.mark.parametrize("raw,expected", [
    (False, False), (True, True), ("false", False), ("FALSE", False), ("0", False), ("off", False),
    ("no", False), ("true", True), ("1", True), ("on", True), ("yes", True), (0, False), (1, True),
])
def test_flag_parsing(raw, expected):
    rc._cache.update({"whatsapp_enabled": raw})
    assert rc.flag("whatsapp_enabled", not expected) is expected


def test_flag_missing_or_garbage_uses_default():
    assert rc.flag("whatsapp_enabled", True) is True
    rc._cache.update({"whatsapp_enabled": "maybe"})
    assert rc.flag("whatsapp_enabled", True) is True
    assert rc.flag("whatsapp_enabled", False) is False


# ---------- model overrides ----------

def test_override_for_role_maps_roles_to_fields():
    rc._cache.update({"pointing_model": models.FALLBACKS["point"],
                      "count_model": models.FALLBACKS["count"],
                      "live_model": models.DEFAULTS["live"]})
    assert rc.override_for_role("point") == models.FALLBACKS["point"]
    assert rc.override_for_role("count") == models.FALLBACKS["count"]
    assert rc.override_for_role("live") == models.DEFAULTS["live"]
    assert rc.override_for_role("read") is None


def test_unknown_model_override_ignored():
    rc._cache.update({"pointing_model": "gemini-typo-9000"})
    assert rc.override_for_role("point") is None
    assert rc.get_override("point") is None


def test_no_override_when_doc_empty():
    assert rc.override_for_role("point") is None


def test_get_override_accepts_role_or_field():
    rc._cache.update({"pointing_model": models.FALLBACKS["point"], "whatsapp_enabled": False})
    assert rc.get_override("point") == models.FALLBACKS["point"]
    assert rc.get_override("pointing_model") == models.FALLBACKS["point"]
    assert rc.get_override("whatsapp_enabled") is False
    assert rc.get_override("nothing") is None


def test_unknown_model_warns_once(caplog):
    rc._cache.update({"pointing_model": "gemini-typo-9000"})
    with caplog.at_level("WARNING", logger=rc.__name__):
        for _ in range(5):
            assert rc.override_for_role("point") is None
    assert sum("gemini-typo-9000" in r.getMessage() for r in caplog.records) == 1


# ---------- refresh from Firestore ----------

async def test_refresh_reads_config_runtime(store):
    store[DOC] = {"pointing_model": models.FALLBACKS["point"], "whatsapp_enabled": "false"}
    values = await rc.refresh(force=True)
    assert _reads(store) == [DOC]
    assert values == {"pointing_model": models.FALLBACKS["point"], "whatsapp_enabled": "false"}
    assert rc.override_for_role("point") == models.FALLBACKS["point"]
    assert rc.flag("whatsapp_enabled", True) is False


async def test_refresh_is_cached_for_ttl(store, monkeypatch):
    assert rc.TTL_S == 60.0
    clock = [1000.0]
    monkeypatch.setattr(rc.time, "monotonic", lambda: clock[0])
    store[DOC] = {"whatsapp_enabled": False}
    await rc.refresh()
    store[DOC] = {"whatsapp_enabled": True}
    clock[0] += rc.TTL_S - 1
    await rc.refresh()
    assert len(_reads(store)) == 1 and rc.flag("whatsapp_enabled", True) is False
    clock[0] += 2
    await rc.refresh()
    assert len(_reads(store)) == 2 and rc.flag("whatsapp_enabled", False) is True


async def test_force_refresh_ignores_ttl(store):
    store[DOC] = {"whatsapp_enabled": False}
    await rc.refresh()
    await rc.refresh(force=True)
    assert len(_reads(store)) == 2


async def test_missing_doc_falls_back_to_defaults(store):
    store[DOC] = {"pointing_model": models.FALLBACKS["point"], "whatsapp_enabled": False}
    await rc.refresh(force=True)
    del store[DOC]
    assert await rc.refresh(force=True) == {}
    assert rc.override_for_role("point") is None
    assert rc.flag("whatsapp_enabled", True) is True


async def test_read_error_keeps_last_good_values_and_retries_sooner(store, fake_db, monkeypatch, caplog):
    clock = [1000.0]
    monkeypatch.setattr(rc.time, "monotonic", lambda: clock[0])
    store[DOC] = {"pointing_model": models.FALLBACKS["point"]}
    await rc.refresh(force=True)
    from aira_live import db as db_mod

    monkeypatch.setattr(db_mod, "db", lambda: _Boom())
    with caplog.at_level("WARNING", logger=rc.__name__):
        assert await rc.refresh(force=True) == {"pointing_model": models.FALLBACKS["point"]}
    assert any("runtime config refresh failed" in r.getMessage() for r in caplog.records)
    assert rc.override_for_role("point") == models.FALLBACKS["point"]
    # after a failure the next attempt is due after RETRY_S, not the full TTL
    clock[0] += rc.RETRY_S - 1
    assert rc._stale() is False
    clock[0] += 2
    assert rc._stale() is True


async def test_disabled_skips_firestore(monkeypatch):
    from aira_live import db as db_mod

    def boom():
        raise AssertionError("db must not be touched when disabled")

    monkeypatch.setattr(db_mod, "db", boom)
    monkeypatch.setattr(rc, "_enabled", lambda: False)
    assert await rc.refresh(force=True) == {}


def test_enabled_needs_project_and_no_mock(monkeypatch):
    """Uses the real ``config.load()``: mock = AIRA_MOCK=1 or no GEMINI_API_KEY."""
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    monkeypatch.delenv("AIRA_MOCK", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GCP_PROJECT", "p")
    assert rc._enabled() is True
    monkeypatch.setenv("AIRA_MOCK", "1")
    assert rc._enabled() is False
    monkeypatch.delenv("AIRA_MOCK")
    monkeypatch.delenv("GEMINI_API_KEY")
    assert rc._enabled() is False  # no key -> mock mode
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GCP_PROJECT")
    assert rc._enabled() is False
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "localhost:8085")
    monkeypatch.setenv("AIRA_MOCK", "1")
    assert rc._enabled() is True


def test_doc_ref_points_at_config_runtime(fake_db):
    assert rc._doc_ref().path == DOC


# ---------- background refresh ----------

async def test_access_inside_event_loop_triggers_background_refresh(store):
    store[DOC] = {"whatsapp_enabled": False}
    assert rc.flag("whatsapp_enabled", True) is True  # stale cache: default now, refresh scheduled
    assert rc._inflight is not None
    rc.flag("whatsapp_enabled", True)  # a second access does not schedule a second read
    await rc._inflight
    assert rc.flag("whatsapp_enabled", True) is False
    assert len(_reads(store)) == 1


def test_access_without_event_loop_does_not_fail():
    assert rc.flag("whatsapp_enabled", True) is True
    assert rc._inflight is None


async def test_run_refresher_polls(store, monkeypatch):
    store[DOC] = {"whatsapp_enabled": False}
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(rc.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await rc.run_refresher()
    assert sleeps == [rc.TTL_S, rc.TTL_S]
    assert len(_reads(store)) == 2


# ---------- emergency switch writer (ops CLI) ----------

async def test_set_value_writes_validated_model(store):
    out = await rc.set_value("pointing_model", models.FALLBACKS["point"])
    assert store[DOC]["pointing_model"] == models.FALLBACKS["point"]
    assert "updated_at" in store[DOC]
    assert out["pointing_model"] == models.FALLBACKS["point"]
    assert rc.override_for_role("point") == models.FALLBACKS["point"]


async def test_set_value_fallback_alias_uses_models_fallbacks(store):
    await rc.set_value("pointing_model", "fallback")
    await rc.set_value("count_model", "fallback")
    assert store[DOC]["pointing_model"] == models.FALLBACKS["point"]
    assert store[DOC]["count_model"] == models.FALLBACKS["count"]
    with pytest.raises(ValueError):
        await rc.set_value("live_model", "fallback")  # live has no fallback model


async def test_set_value_default_alias_clears(store):
    await rc.set_value("pointing_model", models.FALLBACKS["point"])
    await rc.set_value("pointing_model", "default")
    assert store[DOC]["pointing_model"] is None
    assert rc.override_for_role("point") is None


async def test_set_value_parses_bool_flag(store):
    await rc.set_value("whatsapp_enabled", "off")
    assert store[DOC]["whatsapp_enabled"] is False


async def test_set_value_rejects_unknown_field_and_model(store):
    with pytest.raises(ValueError):
        await rc.set_value("budget", "1")
    with pytest.raises(ValueError):
        await rc.set_value("pointing_model", "gemini-typo-9000")
    with pytest.raises(ValueError):
        await rc.set_value("whatsapp_enabled", "maybe")
    assert DOC not in store


async def test_clear_value_removes_override(store):
    await rc.set_value("pointing_model", models.FALLBACKS["point"])
    await rc.set_value("pointing_model", "")
    assert store[DOC]["pointing_model"] is None
    assert rc.override_for_role("point") is None


def test_cli_rejects_bad_usage(capsys):
    assert rc._main([]) == 2
    assert rc._main(["set", "pointing_model"]) == 2
    assert "usage" in capsys.readouterr().err


def test_cli_show_and_set(store, capsys):
    store[DOC] = {"whatsapp_enabled": False}
    assert rc._main(["show"]) == 0
    assert "'whatsapp_enabled': False" in capsys.readouterr().out
    assert rc._main(["set", "pointing_model", "gemini-typo-9000"]) == 1
    assert "error" in capsys.readouterr().err
    assert rc._main(["set", "pointing_model", "fallback"]) == 0
    assert store[DOC]["pointing_model"] == models.FALLBACKS["point"]


# ---------- model priority (env > config/runtime > default) ----------

_WIRED = "remote_config" in inspect.getsource(models.model_for)


@pytest.mark.skipif(not _WIRED, reason="models.model_for does not consult remote_config yet (s1 owns models.py)")
def test_model_for_priority_env_then_runtime_then_default(monkeypatch):
    assert models.model_for("point") == models.DEFAULTS["point"]
    rc._cache.update({"pointing_model": models.FALLBACKS["point"]})
    assert models.model_for("point") == models.FALLBACKS["point"]
    rc._cache.update({"pointing_model": "gemini-typo-9000"})
    assert models.model_for("point") == models.DEFAULTS["point"]
    rc._cache.update({"pointing_model": models.FALLBACKS["point"]})
    monkeypatch.setenv("AIRA_MODEL_POINT", models.DEFAULTS["point"])
    assert models.model_for("point") == models.DEFAULTS["point"]
