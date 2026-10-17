"""Expected languages per shop: Gemini's auto language detection misreads short or accented English as Spanish
("¿Eh? ¿Cuántos años tienes?"), AIRA answers a sentence the owner never said and drifts into Spanish.

Fix: the shop doc says which languages it usually speaks (``languages``, overridable by ``config/runtime``).
A language outside that list never switches the session unless three consecutive full sentences come in
it; until then the utterance is flagged "unclear", the model gets a system note and the caption is marked.
"""
import pytest
from live_fakes import FakeBackend, wait_until

from aira_live import remote_config as rc
from aira_live.contracts import TranscriptMsg, parse_server_msg
from aira_live.i18n import follow_lang
from aira_live.i18n.detect import LangStep, judge_lang, latin_lang
from aira_live.i18n.expected import DEFAULT_LANGUAGES, expected_languages, shop_languages
from aira_live.live_bridge import LiveBridge, LiveEvent
from aira_live.prompt import language_rule, render_prompt, unclear_note
from aira_live.session import Session

SPANISH = ("¿Eh? ¿Cuántos años tienes?", "Quiero dos litros de leche", "Hola, buenos días señor")


@pytest.fixture(autouse=True)
def _clean_rc():
    rc._reset()
    yield
    rc._reset()


# ---------- Latin-language guess ----------
@pytest.mark.parametrize("text, lang", [
    ("¿Eh? ¿Cuántos años tienes?", "es"),
    ("Quiero dos litros de leche", "es"),
    ("Você tem leite hoje?", "pt"),
    ("Combien de lait avez-vous?", "fr"),
    ("Quanto costa il latte?", "it"),
    ("Wie viel Milch haben Sie?", "de"),
    ("Saya mau dua susu", "id"),
    ("Tôi muốn hai hộp sữa chua", "vi"),
    ("Magkano po ang gatas?", "fil"),
])
def test_latin_lang_guesses_common_latin_languages(text, lang):
    assert latin_lang(text) == lang


@pytest.mark.parametrize("text", [
    "how much is the curd today", "two curd please", "Aavin milk one litre", "ok", "20", "", "yes please",
    "what is my total sale", "give me the milk",
])
def test_latin_lang_leaves_english_alone(text):
    assert latin_lang(text) is None


def test_latin_lang_never_raises():
    for text in (None, 42, "🙂"):
        assert latin_lang(text) is None  # type: ignore[arg-type]


# ---------- judge_lang ----------
def _run(start: str, *utterances: str, expected=("ta", "en")) -> LangStep:
    step = LangStep(lang=start, pending=None)
    for u in utterances:
        step = judge_lang(u, step.lang, step.pending, expected=expected, streak=step.streak)
    return step


def test_a_spanish_looking_sentence_in_an_english_session_is_unclear_and_keeps_english():
    step = _run("en", SPANISH[0])
    assert step.lang == "en" and step.unclear is True and step.heard == "es" and step.streak == 1


def test_three_consecutive_spanish_sentences_switch_to_spanish():
    assert _run("en", SPANISH[0], SPANISH[1]).lang == "en"
    step = _run("en", *SPANISH)
    assert step.lang == "es" and step.unclear is False and step.streak == 0


def test_a_short_english_reply_between_spanish_sentences_breaks_the_streak():
    step = _run("en", SPANISH[0], "ok", SPANISH[1], SPANISH[2])
    assert step.lang == "en" and step.unclear is True


def test_tamil_and_english_switching_still_works_with_an_expected_list():
    assert _run("en", "ரெண்டு தயிர் கொடுங்க").lang == "ta"
    assert _run("ta", "how much is the curd today").lang == "en"
    assert _run("ta", "Amo").lang == "ta"
    assert _run("ta", "good morning", "nice weather").lang == "en"
    assert _run("en", "ரெண்டு தயிர் கொடுங்க").unclear is False


def test_hindi_outside_the_expected_list_is_unclear_until_it_persists():
    step = _run("ta", "दो दही दो")
    assert step.lang == "ta" and step.unclear and step.heard == "hi"
    assert _run("ta", "दो दही दो", "दो दही दो", "दो दही दो").lang == "hi"
    assert _run("ta", "दो दही दो", expected=("ta", "hi", "en")).lang == "hi"


def test_without_an_expected_list_any_language_switches_as_before():
    assert follow_lang("¿Cuántos años tienes?", "en") == ("es", None)
    assert follow_lang("Tôi muốn hai hộp sữa chua", "en") == ("vi", None)
    assert follow_lang("how much is the curd today", "ta") == ("en", None)


# ---------- shop languages ----------
async def test_shop_languages_come_from_the_shop_doc_then_the_runtime_override_then_the_default(fake_db, monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "localhost:1")  # tests run in mock mode: read the (fake) doc
    assert await shop_languages("sunrise_dairy") == list(DEFAULT_LANGUAGES)
    fake_db.store["shops/sunrise_dairy"] = {"name": "Sunrise Dairy", "languages": ["ta", "en", "hi"]}
    assert await shop_languages("sunrise_dairy") == ["ta", "en", "hi"]
    rc._cache.update({"languages": ["en"]})
    monkeypatch.setattr(rc, "_stale", lambda now=None: False)
    assert await shop_languages("sunrise_dairy") == ["en"]
    assert expected_languages({"languages": ["ta"]}) == ["en"]


def test_expected_languages_ignores_junk_and_normalises_codes():
    assert expected_languages({"languages": ["TA", " en-IN ", 3, "", None]}) == ["ta", "en"]
    assert expected_languages({"languages": "ta"}) == list(DEFAULT_LANGUAGES)
    assert expected_languages({}) == list(DEFAULT_LANGUAGES)


def test_runtime_config_keeps_a_languages_list():
    assert rc.parse_doc({"languages": ["ta", "en"]}) == {"languages": ["ta", "en"]}
    assert rc.parse_doc({"languages": "ta"}) == {}
    assert rc.languages() is None
    rc._cache.update({"languages": ["hi"]})
    assert rc.languages() == ["hi"]


# ---------- prompt ----------
def test_prompt_names_the_expected_languages():
    rule = language_rule("en", ["ta", "en"])
    assert "This shop usually speaks Tamil and English." in rule
    assert "probably misheard" in rule and "ask to repeat in the current language" in rule.lower()
    assert "clearly keeps speaking it" in rule
    assert "Tamil, Hindi and English" in language_rule("ta", ["ta", "hi", "en"])
    assert "usually speaks" not in language_rule("en")  # no list: no expectation
    assert "Tamil and English" in render_prompt({"name": "Sunrise Dairy", "languages": ["ta", "en"]}, [], [], "en", "short")


def test_unclear_note_names_both_languages_and_tells_the_model_not_to_answer():
    note = unclear_note("es", "en")
    assert note.startswith("[SYSTEM NOTE]")
    assert "looks like Spanish" in note and "repeat in English" in note and "do not answer it" in note


# ---------- contract ----------
def test_transcript_message_can_carry_the_unclear_flag():
    msg = TranscriptMsg(role="user", text="¿Cuántos años tienes?", final=True, lang="en", unclear=True)
    assert parse_server_msg(msg.model_dump_json(by_alias=True)).unclear is True
    assert TranscriptMsg(role="user", text="hi", final=True).unclear is None


# ---------- session + bridge ----------
def test_session_judges_against_its_languages():
    async def _send(_):
        pass

    s = Session("u1", "sunrise_dairy", "en", "short", _send, languages=["ta", "en"])
    assert s.judge(SPANISH[0]).unclear is True and s.lang == "en"
    assert s.follow_lang("how much is the curd today") == "en"
    assert s.judge("ரெண்டு தயிர் கொடுங்க").lang == "ta" and s.lang == "ta"


async def test_bridge_flags_unclear_transcripts_sends_the_note_and_keeps_english(mk_session):
    s, rec = mk_session(lang="en")
    s.languages = ["ta", "en"]
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    await bridge.start()
    try:
        b.push(LiveEvent("input_transcript", text=SPANISH[0], final=True))
        await wait_until(lambda: len(b.sent_text) == 1)
        assert "looks like Spanish" in b.sent_text[0] and "repeat in English" in b.sent_text[0]
        finals = [m for m in rec.msgs if m.t == "transcript" and m.final]
        assert finals[-1].unclear is True and finals[-1].lang == "en" and s.lang == "en"

        b.push(LiveEvent("input_transcript", text="how much is the curd today", final=True))
        await wait_until(lambda: len([m for m in rec.msgs if m.t == "transcript" and m.final]) == 2)
        assert len(b.sent_text) == 1  # a clear English sentence sends nothing
        assert [m for m in rec.msgs if m.t == "transcript" and m.final][-1].unclear is None

        for text in SPANISH:
            b.push(LiveEvent("input_transcript", text=text, final=True))
        await wait_until(lambda: s.lang == "es")
        assert len(b.sent_text) == 3  # notes for the first two Spanish sentences, none once the switch happens
    finally:
        await bridge.close()
