"""Server core: config, model router, db, ids, i18n, frames, session and the notify registry."""
import asyncio
import json
from pathlib import Path

import pytest

from aira_live import config, ids, models, sessions
from aira_live import db as db_mod
from aira_live.contracts import RequestFrameMsg
from aira_live.frames import Frame, FrameStore
from aira_live.i18n import t
from aira_live.session import Session, current_session, get_session

I18N_DIR = Path(__file__).resolve().parents[2] / "aira_live" / "i18n"
LANGS = ("en", "ta", "hi")


def mk(i, ts, purpose="count"):
    return Frame(id=f"f{i}", jpeg_b64="x", w=10, h=10, ts=ts, purpose=purpose)


def mk_session(sent=None):
    sent = [] if sent is None else sent

    async def send(m):
        sent.append(m)

    s = Session(uid="u1", shop_id="murugan_dairy", lang="en", verbosity="short", send=send)
    return s, sent


# ---------- config ----------
def test_settings_mock_without_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AIRA_DEMO_SHOP", raising=False)
    s = config.load()
    assert s.mock is True and s.max_frame_b64 == 1_500_000 and s.demo_shop_id == "murugan_dairy"


def test_settings_real_key_and_mock_switch(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("AIRA_MOCK", raising=False)
    assert config.load().mock is False
    monkeypatch.setenv("AIRA_MOCK", "1")
    assert config.load().mock is True


def test_settings_minimal_setup_env(monkeypatch):
    for k in ("FIRESTORE_DATABASE", "AIRA_ALLOW_NO_TOKEN", "AIRA_JOB_TOKEN", "AIRA_DEMO_SHOP"):
        monkeypatch.delenv(k, raising=False)
    s = config.load()
    assert s.firestore_database == "aira" and s.allow_no_token is False and s.job_token is None
    monkeypatch.setenv("FIRESTORE_DATABASE", "aira-test")
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    monkeypatch.setenv("AIRA_JOB_TOKEN", "secret")
    monkeypatch.setenv("AIRA_DEMO_SHOP", "other_shop")
    s = config.load()
    assert (s.firestore_database, s.allow_no_token, s.job_token, s.demo_shop_id) == (
        "aira-test", True, "secret", "other_shop")


# ---------- models ----------
def test_model_router_defaults_fallback_and_override(monkeypatch):
    for role in ("LIVE", "POINT", "COUNT", "READ", "VERIFY"):
        monkeypatch.delenv(f"AIRA_MODEL_{role}", raising=False)
    assert models.model_for("live") == "gemini-3.8-live"
    assert models.model_for("point") == "gemini-robotics-er-2-preview"
    assert models.model_for("count") == "gemini-robotics-er-2-preview"
    assert models.model_for("read") == "gemini-3.8-flash"
    # Build overrides §7: verify is a different model generation than read A.
    assert models.model_for("verify") == "gemini-3.6-flash"
    assert models.fallback_for("point") == "gemini-3.8-flash"
    assert models.fallback_for("count") == "gemini-3.8-flash"
    assert models.fallback_for("read") is None
    monkeypatch.setenv("AIRA_MODEL_POINT", "gemini-3.8-flash")
    assert models.model_for("point") == "gemini-3.8-flash"


# ---------- db ----------
def test_db_uses_aira_database_and_is_singleton(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "localhost:8085")
    monkeypatch.setenv("GCP_PROJECT", "demo-aira")
    monkeypatch.delenv("FIRESTORE_DATABASE", raising=False)
    monkeypatch.setattr(db_mod, "_client", None)
    c = db_mod.db()
    assert c._database == "aira" and c.project == "demo-aira"
    assert db_mod.db() is c


def test_db_database_from_env(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "localhost:8085")
    monkeypatch.setenv("GCP_PROJECT", "demo-aira")
    monkeypatch.setenv("FIRESTORE_DATABASE", "aira-test")
    monkeypatch.setattr(db_mod, "_client", None)
    assert db_mod.db()._database == "aira-test"
    monkeypatch.setenv("FIRESTORE_DATABASE", "")
    monkeypatch.setattr(db_mod, "_client", None)
    assert db_mod.db()._database == "aira"  # never "(default)"


# ---------- ids ----------
def test_idem_key_is_deterministic_and_distinct():
    a = ids.idem_key("s1", "call-1")
    assert a == ids.idem_key("s1", "call-1")
    assert a != ids.idem_key("s1", "call-2") and len(a) == 32
    assert a != ids.idem_key("s2", "call-1")


# ---------- i18n ----------
def test_i18n_format_fallback_and_unknown():
    assert t("mock.echo", "ta", text="hi").endswith("hi")
    assert t("confirm.cancelled", "en") == "Okay, cancelled."
    assert t("does.not.exist", "hi") == "does.not.exist"


def test_i18n_unknown_lang_falls_back_to_english():
    assert t("confirm.cancelled", "fr") == "Okay, cancelled."


def test_i18n_missing_placeholder_never_raises():
    assert t("mock.echo", "en", other="x") == "(mock) you said: {text}"


HINT_KEYS = ["vision.aim.move_back", "vision.aim.spread_out", "vision.aim.show_items", "vision.aim.show_label",
             "vision.aim.hold_closer", "vision.aim.not_visible", "cash.spread_notes"]
BUMP_KEYS = ["bump.stop", "bump.left_right", "bump.side.left", "bump.side.right"]


def _catalog(lang):
    return json.loads((I18N_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def test_spoken_hint_keys_exist_in_every_language():
    for lang in LANGS:
        catalog = _catalog(lang)
        missing = [k for k in HINT_KEYS + BUMP_KEYS if not catalog.get(k)]
        assert missing == [], f"{lang} missing {missing}"
        assert t("vision.aim.not_visible", lang) != "vision.aim.not_visible"


def test_every_language_has_the_same_keys_and_placeholders():
    import string
    en = _catalog("en")

    def fields(s):
        return sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)

    for lang in ("ta", "hi"):
        other = _catalog(lang)
        assert sorted(other) == sorted(en), f"{lang} keys differ from en"
        for k, v in en.items():
            assert fields(other[k]) == fields(v), f"{lang}:{k} placeholders differ"
        assert all(other[k] != en[k] for k in HINT_KEYS + BUMP_KEYS), f"{lang} has untranslated hints"


def test_bump_guard_phrases():
    assert t("bump.stop", "en") == "Stop!"
    assert t("bump.left_right", "en", side=t("bump.side.left", "en")) == "Obstacle on your left."
    for lang in LANGS:
        side = t("bump.side.right", lang)
        assert side in t("bump.left_right", lang, side=side)


# ---------- frames ----------
def test_ring_buffer_and_purpose_filter():
    s = FrameStore(maxlen=3)
    for i in range(5):
        s.add(mk(i, i))
    assert [f.id for f in s.latest(3)] == ["f2", "f3", "f4"]
    s2 = FrameStore()
    s2.add(mk(1, 1, "cash")); s2.add(mk(2, 2, "count")); s2.add(mk(3, 3, "cash"))
    assert [f.id for f in s2.latest(5, purpose="cash")] == ["f1", "f3"]
    assert [f.id for f in s2.latest()] == ["f3"]


async def test_wait_for_new_frames_and_timeout():
    s = FrameStore()

    async def producer():
        await asyncio.sleep(0.05); s.add(mk(1, 10, "invoice")); s.add(mk(2, 11, "invoice"))

    task = asyncio.create_task(producer())
    got = await s.wait_for(count=2, purpose="invoice", after_ts=5, timeout_s=1)
    assert [f.id for f in got] == ["f1", "f2"]
    await task
    assert await FrameStore().wait_for(count=1, purpose="cash", after_ts=0, timeout_s=0.05) == []


async def test_wait_for_returns_frames_already_stored_and_ignores_old_ones():
    s = FrameStore()
    s.add(mk(1, 1, "cash")); s.add(mk(2, 20, "cash")); s.add(mk(3, 21, "count"))
    got = await s.wait_for(count=1, purpose="cash", after_ts=10, timeout_s=0.5)
    assert [f.id for f in got] == ["f2"]


# ---------- session ----------
async def test_request_frames_sends_request_and_waits_for_new_frames():
    s, sent = mk_session()
    s.frames.add(mk(0, 1_000, "cash"))  # an older frame must not satisfy the request

    async def client():
        await asyncio.sleep(0.02)
        assert isinstance(sent[-1], RequestFrameMsg)
        s.frames.add(mk(1, 1_001, "cash")); s.frames.add(mk(2, 1_002, "cash"))

    task = asyncio.create_task(client())
    got = await s.request_frames("cash", count=2, interval_ms=300, timeout_s=1)
    await task
    req = sent[0]
    assert req.purpose == "cash" and req.burst.count == 2 and req.burst.interval_ms == 300
    assert [f.id for f in got] == ["f1", "f2"]


async def test_request_single_frame_has_no_burst_and_times_out_empty():
    s, sent = mk_session()
    assert await s.request_frames("point", timeout_s=0.05) == []
    assert sent[0].purpose == "point" and sent[0].burst is None


def test_session_ids_are_unique():
    a, _ = mk_session()
    b, _ = mk_session()
    assert a.session_id != b.session_id and a.frames is not b.frames


# ---------- sessions.notify ----------
async def test_session_context_and_notify():
    s, sent = mk_session()
    current_session.set(s)
    assert get_session() is s
    sessions.register(s)
    try:
        n = await sessions.notify("murugan_dairy", "Sakthi vendor confirmed: delivery at 6 pm", cue="ack")
        assert n == 1 and sent[0].text.startswith("Sakthi vendor") and sent[1].cue == "ack"
        assert sent[0].role == "aira" and sent[0].final is True
        assert await sessions.notify("other-shop", "x") == 0
    finally:
        sessions.unregister(s)
    assert await sessions.notify("murugan_dairy", "x") == 0


async def test_notify_skips_a_broken_session():
    good, sent = mk_session()

    async def boom(m):
        raise ConnectionError("socket closed")

    bad = Session(uid="u2", shop_id="murugan_dairy", lang="en", verbosity="short", send=boom)
    sessions.register(bad); sessions.register(good)
    try:
        assert await sessions.notify("murugan_dairy", "Low stock", cue="warn") == 1
        assert sent[-1].cue == "warn"
    finally:
        sessions.unregister(bad); sessions.unregister(good)
