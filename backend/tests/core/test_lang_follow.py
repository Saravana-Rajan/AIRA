"""AIRA follows the speaker's language turn by turn: no default and no fixed language anywhere.

- ``i18n.detect_lang`` is pure and script-based (JAPAC: Indic, Thai, CJK, Latin).
- ``i18n.t`` gives the English line for a language without a catalog, never silence.
- The system prompt tells Live to answer in the language of the last utterance and never to ask.
- ``session.lang`` follows every user turn (Live transcripts and typed text) and the phone hears it on the
  transcript message (``lang``) so its labels can follow too.
"""
import pytest
from live_fakes import FakeBackend, wait_until

from aira_live.contracts import TranscriptMsg, dump_server_msg
from aira_live.i18n import LANGS, detect_lang, t
from aira_live.live_bridge import LiveBridge, LiveEvent
from aira_live.prompt import render_prompt


# ---------- detection ----------
@pytest.mark.parametrize("text, lang", [
    ("ரெண்டு தயிர் கொடுங்க", "ta"),
    ("दो दही दो", "hi"),
    ("two curd please", "en"),
    ("Tôi muốn hai hộp sữa chua", "en"),           # Latin script: the model answers in Vietnamese anyway
    ("Saya mau dua yogurt", "en"),
    ("ヨーグルトを二つください", "ja"),
    ("请给我两个酸奶", "zh"),
    ("酸奶ください", "ja"),                          # Han plus kana is Japanese
    ("요거트 두 개 주세요", "ko"),
    ("ขอโยเกิร์ตสองถ้วย", "th"),
    ("రెండు పెరుగు", "te"),
    ("ಎರಡು ಮೊಸರು", "kn"),
    ("രണ്ട് തൈര്", "ml"),
    ("দুটি দই", "bn"),
    ("બે દહીં", "gu"),
    ("ਦੋ ਦਹੀਂ", "pa"),
    ("ଦୁଇଟି ଦହି", "or"),
    ("ရှိတယ်", "my"),
    ("Sakthi curd ரெண்டு", "ta"),                    # mixed Tamil-English: the Indic script wins
    ("ok ரெண்டு Sakthi curd one litre", "ta"),       # product names in Latin never flip a Tamil sentence to English
])
def test_detect_lang_by_script(text, lang):
    assert detect_lang(text) == lang


def test_detect_lang_keeps_the_previous_language_for_digits_and_empty_text():
    assert detect_lang("", "ta") == "ta"
    assert detect_lang("60", "hi") == "hi"
    assert detect_lang("   ", "ja") == "ja"
    assert detect_lang("...", "ko") == "ko"
    assert detect_lang("") == "en"


def test_detect_lang_never_raises_and_only_returns_lowercase_codes():
    for text in (None, 42, b"bytes", "mixed 🙂 symbols"):
        code = detect_lang(text)  # type: ignore[arg-type]
        assert isinstance(code, str) and code == code.lower() and 2 <= len(code) <= 3


# ---------- fixed lines in a language without a catalog ----------
def test_i18n_gives_the_english_line_for_any_language_without_a_catalog():
    assert LANGS == ("ta", "hi", "en")
    for lang in ("ja", "ko", "zh", "th", "vi", "id", "fil", "te", "kn", "ml", "bn", "gu", "pa"):
        assert t("confirm.cancelled", lang) == t("confirm.cancelled", "en")
        assert t("mock.echo", lang, text="x").endswith("x")


# ---------- prompt ----------
def test_prompt_tells_live_to_follow_the_speaker_and_never_pin_a_language():
    p = render_prompt({"name": "Sunrise Dairy"}, [], [], "ta", "short")
    low = p.lower()
    assert "stay in tamil, the session language" in low
    assert "full sentence in another language" in low  # sticky: a short reply never flips the language
    assert "never switch language because of a single short or unclear word" in low  # "Hola" is not Spanish
    assert "never ask which language" in low
    assert "tamil-english" in low
    assert "default language" not in low and "always speak" not in low
    # the hello language is only the hint for the first line, before anyone has spoken
    assert "until the speaker has spoken, use tamil" in low
    # fixed English lines (tool 'say' texts, notices) are spoken in the session language, never dropped
    assert "say it in the session language" in low


def test_prompt_first_line_hint_covers_japac_and_falls_back_to_english():
    assert "until the speaker has spoken, use japanese" in render_prompt({}, [], [], "ja", "short").lower()
    assert "until the speaker has spoken, use english" in render_prompt({}, [], [], "xx", "short").lower()


# ---------- wire ----------
def test_transcript_message_carries_the_detected_language_only_when_set():
    assert '"lang":"ta"' in dump_server_msg(TranscriptMsg(role="user", text="ரெண்டு", final=True, lang="ta"))
    assert "lang" not in dump_server_msg(TranscriptMsg(role="user", text="two", final=False))


async def test_session_lang_follows_every_final_user_turn(mk_session):
    s, rec = mk_session(lang="en")
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    await bridge.start()
    try:
        b.push(LiveEvent("input_transcript", text="ரெண்டு ", final=False))
        b.push(LiveEvent("input_transcript", text="ரெண்டு தயிர் கொடுங்க", final=True))  # a full sentence
        await wait_until(lambda: any(m.t == "transcript" and m.final for m in rec.msgs))
        assert s.lang == "ta"
        finals = [m for m in rec.msgs if m.t == "transcript" and m.final]
        assert finals[-1].lang == "ta"
        partials = [m for m in rec.msgs if m.t == "transcript" and not m.final]
        assert partials and partials[0].lang is None  # only a finished utterance is detected

        b.push(LiveEvent("input_transcript", text="ヨーグルトを二つ", final=True))
        await wait_until(lambda: s.lang == "ja")
        b.push(LiveEvent("input_transcript", text="how much is the curd", final=True))
        await wait_until(lambda: s.lang == "en")
        b.push(LiveEvent("input_transcript", text="60", final=True))  # digits only: the language stays
        await wait_until(lambda: len([m for m in rec.msgs if m.t == "transcript" and m.final]) == 4)
        assert s.lang == "en"
    finally:
        await bridge.close()


async def test_notice_is_spoken_in_the_language_the_speaker_last_used(mk_session):
    s, _ = mk_session(lang="ja")
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    await bridge.start()
    try:
        await bridge.speak_notice(t("jobs.expired_alert", s.lang, items="curd"))
        sent = b.sent_text[-1]
        # the note names the session language, so a Japanese session hears the alert in Japanese
        assert "Expired: curd" in sent and "in Japanese" in sent and sent.startswith("[SYSTEM NOTE] Say exactly")
    finally:
        await bridge.close()



def test_mock_mode_follows_typed_text_and_tells_the_phone(monkeypatch):
    from fastapi.testclient import TestClient

    from aira_live.main import app

    monkeypatch.setenv("AIRA_MOCK", "1")
    monkeypatch.setenv("AIRA_ALLOW_NO_TOKEN", "1")
    hello = {"t": "hello", "uid": "u1", "shopId": "sunrise_dairy", "lang": "en", "verbosity": "short",
             "appVersion": "0.1.0"}
    with TestClient(app).websocket_connect("/ws", headers={"Origin": "https://aira-shop.web.app"}) as ws:
        ws.send_json(hello)
        ws.receive_json()
        ws.send_json({"t": "text", "text": "ரெண்டு தயிர் கொடுங்க"})
        reply = ws.receive_json()
        assert reply["lang"] == "ta" and reply["text"].startswith("(mock) நீங்கள்")
        ws.send_json({"t": "text", "text": "ヨーグルト"})
        reply = ws.receive_json()
        assert reply["lang"] == "ja" and reply["text"] == "(mock) you said: ヨーグルト"  # English line, never silence
        ws.send_json({"t": "text", "text": "how much is the curd"})
        assert ws.receive_json()["lang"] == "en"
