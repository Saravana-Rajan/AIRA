"""One line per notice, the reply-language lock and the transcription config (live_bridge).

Evidence from the owner's dashboard: "Hello" was transcribed as "Hola", AIRA answered in Spanish, and every
ambient line appeared twice (our English caption, then the model's Spanish re-speak). No network here: the
in-memory ``FakeBackend`` plays Live.
"""
import asyncio
from types import SimpleNamespace

import pytest
from live_fakes import FakeBackend, wait_until

from aira_live import live_bridge, prompt
from aira_live.live_bridge import NOTICE_PREFIX, LiveBridge, LiveEvent, translate


def _bridge(s, b):
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    bridge.BACKOFF_BASE_S = 0
    return bridge


def _captions(rec) -> list[tuple[str, bool]]:
    return [(m.text, m.final) for m in rec.msgs if m.t == "transcript" and m.role == "aira"]


async def settle() -> None:
    for _ in range(30):
        await asyncio.sleep(0)


@pytest.fixture
def diag_log(monkeypatch):
    lines: list[tuple[str, dict]] = []
    monkeypatch.setattr(live_bridge, "diag", lambda ev, /, **f: lines.append((ev, f)))
    return lines


# ---------- 1. one line per notice ----------
async def test_a_notice_is_a_say_exactly_note_and_the_models_re_speak_is_not_captioned_again(mk_session, diag_log):
    s, rec = mk_session(lang="en")
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    await bridge.speak_notice("Keyboard to your right, within arm's reach.")
    assert b.sent_text == [f"{NOTICE_PREFIX}, in English: «Keyboard to your right, within arm's reach.»"]
    # The model says it: the audio plays, the caption (already shown by the server) is not shown a second time.
    b.push(LiveEvent("audio", audio=b"\x01"))
    b.push(LiveEvent("output_transcript", text="Keyboard to your", final=False))
    b.push(LiveEvent("output_transcript", text=" right, within arm's reach.", final=False))
    b.push(LiveEvent("output_transcript", text="Keyboard to your right, within arm's reach.", final=True))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: any(m.t == "audio" for m in rec.msgs))
    await settle()
    assert _captions(rec) == []
    assert any(e == "notice_echo" for e, _ in diag_log)
    # The next, ordinary answer is captioned as before.
    b.push(LiveEvent("output_transcript", text="Yes, we have", final=False))
    b.push(LiveEvent("output_transcript", text="Yes, we have curd.", final=True))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: ("Yes, we have curd.", True) in _captions(rec))
    assert _captions(rec) == [("Yes, we have curd.", True)]
    await bridge.close()


async def test_a_notice_said_in_two_sentences_or_slightly_reworded_is_still_one_caption(mk_session):
    s, rec = mk_session(lang="en")
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    await bridge.speak_notice("That's Aavin Milk, 500 ml. 30 rupees.")
    b.push(LiveEvent("output_transcript", text="That's Aavin Milk, 500 ml.", final=True))
    b.push(LiveEvent("output_transcript", text="It is 30 rupees.", final=True))
    b.push(LiveEvent("turn_complete"))
    await settle()
    await settle()
    assert _captions(rec) == []
    # An unrelated answer in the same window is still captioned.
    b.push(LiveEvent("output_transcript", text="The shop opens at seven in the morning.", final=True))
    await wait_until(lambda: _captions(rec))
    assert _captions(rec) == [("The shop opens at seven in the morning.", True)]
    await bridge.close()


async def test_a_pending_notice_expires_so_later_answers_are_captioned(mk_session, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(live_bridge, "_monotonic", lambda: now[0])
    s, rec = mk_session(lang="en")
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    await bridge.speak_notice("Hold it a little closer.")
    now[0] += live_bridge.NOTICE_ECHO_S + 1
    b.push(LiveEvent("output_transcript", text="Hold it a little closer.", final=True))
    await wait_until(lambda: _captions(rec))
    assert _captions(rec) == [("Hold it a little closer.", True)]
    await bridge.close()


# ---------- 2. reply-language lock ----------
async def test_a_reply_in_another_language_is_corrected_and_its_caption_hidden(mk_session, diag_log):
    s, rec = mk_session(lang="en")
    s.languages = ["ta", "en"]
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    b.push(LiveEvent("output_transcript", text="Teclado justo enfrente,", final=False))
    b.push(LiveEvent("output_transcript", text=" al alcance de la mano.", final=False))
    b.push(LiveEvent("output_transcript", text="Teclado justo enfrente, al alcance de la mano.", final=True))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: b.sent_text)
    await settle()
    assert b.sent_text == ["[SYSTEM NOTE] Reply only in English; the owner speaks English."]
    assert _captions(rec) == []
    lock = [f for e, f in diag_log if e == "lang_lock"]
    assert lock and lock[0]["heard"] == "es" and lock[0]["expected"] == "en"
    # One of the shop's languages is never corrected, whatever the session language.
    b.push(LiveEvent("output_transcript", text="பால் இருக்கு, முப்பது ரூபாய்.", final=True))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: _captions(rec))
    assert _captions(rec) == [("பால் இருக்கு, முப்பது ரூபாய்.", True)]
    assert len(b.sent_text) == 1
    await bridge.close()


async def test_the_wrong_language_is_caught_in_the_partials_before_the_final(mk_session):
    """Captions are held until a few words are in, so a Spanish answer never flashes on the phone."""
    s, rec = mk_session(lang="en")
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    b.push(LiveEvent("output_transcript", text="Hola, ¿cómo puedo", final=False))
    b.push(LiveEvent("output_transcript", text=" ayudarte hoy, señor?", final=False))
    b.push(LiveEvent("output_transcript", text=" Tengo leche y", final=False))
    b.push(LiveEvent("output_transcript", text="Hola, ¿cómo puedo ayudarte hoy, señor? Tengo leche y cuajada.", final=True))
    await wait_until(lambda: b.sent_text)
    await settle()
    assert _captions(rec) == []
    assert b.sent_text == ["[SYSTEM NOTE] Reply only in English; the owner speaks English."]
    # English partials stream once enough words are in, and the final follows.
    b.push(LiveEvent("turn_complete"))
    b.push(LiveEvent("output_transcript", text="We have milk", final=False))
    b.push(LiveEvent("output_transcript", text=" and curd today.", final=False))
    b.push(LiveEvent("output_transcript", text="We have milk and curd today.", final=True))
    await wait_until(lambda: ("We have milk and curd today.", True) in _captions(rec))
    assert _captions(rec) == [("We have milk and curd today.", False), ("We have milk and curd today.", True)]
    await bridge.close()


async def test_a_spanish_re_speak_of_a_notice_gets_the_correction_too(mk_session):
    s, rec = mk_session(lang="en")
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    await bridge.speak_notice("Keyboard to your right, within arm's reach.")
    b.push(LiveEvent("output_transcript", text="Teclado justo enfrente, al alcance de la mano.", final=True))
    b.push(LiveEvent("turn_complete"))
    await wait_until(lambda: len(b.sent_text) == 2)
    assert b.sent_text[1] == "[SYSTEM NOTE] Reply only in English; the owner speaks English."
    assert _captions(rec) == []
    await bridge.close()


async def test_the_correction_is_sent_once_per_window(mk_session, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(live_bridge, "_monotonic", lambda: now[0])
    s, rec = mk_session(lang="en")
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    for _ in range(2):
        b.push(LiveEvent("output_transcript", text="Hola señor, tengo leche y cuajada para usted.", final=True))
        b.push(LiveEvent("turn_complete"))
    await settle()
    await settle()
    assert len(b.sent_text) == 1 and _captions(rec) == []
    now[0] += live_bridge.LANG_LOCK_GAP_S + 1
    b.push(LiveEvent("output_transcript", text="Hola señor, tengo leche y cuajada para usted.", final=True))
    await wait_until(lambda: len(b.sent_text) == 2)
    await bridge.close()


def test_the_prompt_keeps_the_session_language_and_never_switches_on_a_short_word():
    rule = prompt.language_rule("en", ["ta", "en"])
    assert "Stay in English" in rule
    assert "NEVER switch" in rule and "single short or unclear word" in rule
    assert "Hola" in rule
    ta = prompt.language_rule("ta", ["ta", "en"])
    assert "Stay in Tamil" in ta
    text = prompt.render_prompt({"name": "Sunrise Dairy"}, [], [], "en", "short")
    assert "[SYSTEM NOTE] Say exactly" in text  # rule 6 and 12 name the new notice form


# ---------- 3. transcription config ----------
def test_transcription_config_uses_language_codes_vocabulary_and_diarization():
    from google.genai import types

    cfg = live_bridge.transcription_config(types, languages=["ta", "en", "xx"], vocabulary=["Aavin", "rupees"])
    assert cfg.language_codes == ["ta-IN", "en-IN", "xx"]
    assert cfg.custom_vocabulary == ["Aavin", "rupees"] and cfg.adaptation_phrases == ["Aavin", "rupees"]
    assert cfg.diarization is True
    assert cfg.language_hints is None  # deprecated in google-genai in favour of language_codes


def test_transcription_config_skips_fields_an_older_sdk_lacks():
    class Old:  # an SDK (or a test stub) whose config knows none of the new fields
        def __init__(self, **kw):
            self.kw = kw

    cfg = live_bridge.transcription_config(SimpleNamespace(AudioTranscriptionConfig=Old),
                                           languages=["ta"], vocabulary=["Aavin"])
    assert cfg.kw == {}

    class Hints:
        def __init__(self, language_codes):
            self.language_codes = language_codes

    class HintsOnly:  # an SDK with the older language_hints field but no language_codes
        model_fields = {"language_hints": None, "diarization": None}

        def __init__(self, **kw):
            self.kw = kw

    cfg = live_bridge.transcription_config(SimpleNamespace(AudioTranscriptionConfig=HintsOnly, LanguageHints=Hints),
                                           languages=["ta", "en"], vocabulary=["Aavin"])
    assert cfg.kw["language_hints"].language_codes == ["ta-IN", "en-IN"] and cfg.kw["diarization"] is True
    assert "custom_vocabulary" not in cfg.kw


def test_catalog_vocabulary_names_the_shop_brands_products_aliases_suppliers_and_money_words():
    vocab = prompt.catalog_vocabulary(
        {"name": "Sunrise Dairy"},
        [{"sku": "aavin_milk_500ml", "brand": "Aavin", "name": "Milk", "aliases": {"ta": ["ஆவின் பால்"]}},
         {"sku": "sakthi_curd_1l", "brand": "Sakthi", "name": "Sakthi Curd"}],
        [{"name": "Sakthi Dairy Distributors", "channel": "whatsapp"}])
    for word in ("Sunrise Dairy", "Aavin", "Aavin Milk", "ஆவின் பால்", "Sakthi", "Sakthi Curd",
                 "Sakthi Dairy Distributors", "rupees", "AIRA"):
        assert word in vocab
    assert len(set(vocab)) == len(vocab)
    big = prompt.catalog_vocabulary({}, [{"sku": f"p{i}", "name": f"Product {i}"} for i in range(300)], [])
    assert len(big) <= prompt.MAX_VOCABULARY


async def test_build_system_prompt_remembers_the_vocabulary_for_the_live_session(fake_db, mk_session):
    fake_db.store["shops/sunrise_dairy"] = {"name": "Sunrise Dairy", "languages": ["ta", "en"]}
    fake_db.store["shops/sunrise_dairy/products/aavin_milk_500ml"] = {"brand": "Aavin", "name": "Milk", "price": 30}
    fake_db.store["shops/sunrise_dairy/suppliers/sakthi"] = {"name": "Sakthi Dairy"}
    await prompt.build_system_prompt("sunrise_dairy", "en", "short")
    vocab = prompt.last_vocabulary("sunrise_dairy")
    assert "Aavin Milk" in vocab and "Sakthi Dairy" in vocab and "Sunrise Dairy" in vocab
    from google.genai import types

    s, _ = mk_session()
    s.languages = ["ta", "en"]
    cfg = live_bridge.input_transcription_config(types, s)
    assert cfg.language_codes == ["ta-IN", "en-IN"] and "Aavin Milk" in cfg.custom_vocabulary
    assert cfg.diarization is True
    s.languages = None  # no shop list: the session language is the only hint
    assert live_bridge.input_transcription_config(types, s).language_codes == ["en-IN"]


# ---------- diarization labels ----------
def test_translate_carries_the_diarization_speaker_label_when_the_sdk_has_one():
    e = SimpleNamespace(interrupted=False, output_transcription=None, content=None,
                        live_session_resumption_update=None, go_away=None, turn_complete=False,
                        input_transcription=SimpleNamespace(text="one curd", finished=True, speaker_label="speaker_2"))
    [ev] = translate(e)
    assert ev.kind == "input_transcript" and ev.speaker == "speaker_2"
    e.input_transcription = SimpleNamespace(text="one curd", finished=True)  # an older SDK: no label
    assert translate(e)[0].speaker is None


async def test_the_bridge_tells_the_monitor_who_spoke(mk_session, monkeypatch):
    s, rec = mk_session()
    noted = []
    monkeypatch.setattr(live_bridge.monitor, "note_speaker", lambda sid, label: noted.append((sid, label)))
    b = FakeBackend()
    bridge = _bridge(s, b)
    await bridge.start()
    b.push(LiveEvent("input_transcript", text="one curd", final=True, speaker="speaker_2"))
    b.push(LiveEvent("input_transcript", text="no label", final=True))
    await wait_until(lambda: len([m for m in rec.msgs if m.t == "transcript"]) == 2)
    assert noted == [(s.session_id, "speaker_2")]
    await bridge.close()
