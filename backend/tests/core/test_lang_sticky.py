"""The session language is sticky: it changes only when the speaker clearly speaks a full sentence in another
language, never on a short reply or on a Tamil/Hindi word that Gemini's transcript rendered in Latin letters.

Symptom fixed here: an owner keeps speaking Tamil, the input transcript shows "Amo" (ஆமா) and AIRA flips to
English for the rest of the conversation.
"""
import pytest
from live_fakes import FakeBackend, wait_until

from aira_live.i18n import follow_lang
from aira_live.i18n.detect import ROMANISED_INDIC
from aira_live.live_bridge import LiveBridge, LiveEvent
from aira_live.prompt import render_prompt
from aira_live.session import Session


def _run(start: str, *utterances: str) -> str:
    lang, pending = start, None
    for u in utterances:
        lang, pending = follow_lang(u, lang, pending)
    return lang


# ---------- pure function ----------
def test_tamil_session_survives_romanised_words_short_replies_and_numbers():
    assert _run("ta", "Amo") == "ta"
    assert _run("ta", "ok") == "ta"
    assert _run("ta", "20") == "ta"
    assert _run("ta", "aama 20 litre") == "ta"
    assert _run("ta", "Amo", "ok", "20", "aama 20 litre") == "ta"


@pytest.mark.parametrize("word", ["aama", "ama", "amo", "illa", "illai", "venum", "vendam", "paal", "thayir",
                                  "sari", "seri", "romba", "haan", "nahi", "theek", "accha", "dudh", "dahi"])
def test_romanised_tamil_and_hindi_words_count_as_the_current_indic_language(word):
    assert word in ROMANISED_INDIC
    assert _run("ta", f"{word} {word} {word}") == "ta"
    assert _run("hi", f"{word} {word} {word}") == "hi"


def test_a_full_english_sentence_switches_to_english():
    assert _run("ta", "how much is the curd today") == "en"
    assert _run("ta", "what is my total sale") == "en"  # exactly three letter-words


def test_a_japanese_sentence_switches_to_japanese():
    assert _run("ta", "ヨーグルトを二つください") == "ja"
    assert _run("en", "ヨーグルトを二つください") == "ja"


def test_two_short_english_words_do_not_switch():
    assert _run("ta", "thank you") == "ta"
    assert _run("ta", "good morning") == "ta"
    assert _run("ta", "yes please") == "ta"
    assert _run("ta", "no") == "ta"


def test_two_consecutive_english_sentences_switch():
    # Neither utterance alone is a clear sentence (fewer than three new-language words), together they are.
    assert _run("ta", "good morning") == "ta"
    assert _run("ta", "good morning", "nice weather") == "en"
    # ...but a Tamil word in between resets the streak.
    assert _run("ta", "good morning", "aama", "nice weather") == "ta"


def test_a_full_tamil_sentence_switches_back_from_english():
    assert _run("en", "ரெண்டு தயிர் கொடுங்க") == "ta"
    assert _run("en", "ஆமா") == "en"  # one word is not a sentence
    assert _run("en", "ஆமா", "சரி") == "en"  # two one-word replies are not either


def test_product_names_and_units_never_count_as_a_language_change():
    assert _run("ta", "Aavin milk one litre") == "ta"
    assert _run("ta", "Sakthi curd 500 ml") == "ta"
    assert _run("ta", "Arun ice cream") == "ta"


def test_follow_lang_never_raises():
    for text in (None, 42, "", "   ", "🙂"):
        lang, pending = follow_lang(text, "ta", "en")  # type: ignore[arg-type]
        assert lang == "ta" and pending is None


# ---------- session ----------
def test_session_follow_lang_keeps_the_streak_between_turns():
    async def _send(_):
        pass

    s = Session("u1", "sunrise_dairy", "ta", "short", _send)
    assert s.follow_lang("Amo") == "ta"
    assert s.follow_lang("good morning") == "ta"
    assert s.follow_lang("nice weather") == "en"
    assert s.lang == "en"


# ---------- prompt ----------
def test_prompt_says_latin_letter_tamil_is_still_tamil_and_never_switches_mid_reply():
    low = render_prompt({"name": "Sunrise Dairy"}, [], [], "ta", "short").lower()
    assert "latin letters" in low and "still tamil" in low
    assert "full sentence in another language" in low
    assert "never switch" in low and "mid-reply" in low
    assert "switch immediately" not in low


# ---------- bridge ----------
async def test_bridge_keeps_tamil_through_romanised_transcripts(mk_session):
    s, rec = mk_session(lang="ta")
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    await bridge.start()
    try:
        for i, text in enumerate(("Amo", "ok", "20", "aama 20 litre"), start=1):
            b.push(LiveEvent("input_transcript", text=text, final=True))
            await wait_until(lambda: len([m for m in rec.msgs if m.t == "transcript" and m.final]) == i)
            assert s.lang == "ta"
        finals = [m for m in rec.msgs if m.t == "transcript" and m.final]
        assert all(m.lang == "ta" for m in finals)
        b.push(LiveEvent("input_transcript", text="how much is the curd", final=True))
        await wait_until(lambda: s.lang == "en")
    finally:
        await bridge.close()
