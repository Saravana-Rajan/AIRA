"""(a) Gemini Live (``model_for("live")``) through the app's own ``LiveBridge`` + ``AdkLiveBackend``.

One session, the app's real system prompt (``render_prompt`` for a Tamil-speaking owner of Sunrise Dairy).
A Tamil text turn must be answered in Tamil script and an English text turn in English: the language
follows the speaker. First-audio latency is recorded per turn.
"""
from __future__ import annotations

import asyncio
import time

from live_fakes import wait_until

from aira_live.live_bridge import AdkLiveBackend, LiveBridge
from aira_live.prompt import render_prompt

TAMIL_TURN = "வணக்கம். கடை திறந்துவிட்டதா? ஒரே வரியில் சொல்லுங்கள்."
ENGLISH_TURN = "Hello. Is the shop open now? Answer in one short English sentence."

SHOP = {"name": "Sunrise Dairy"}
PRODUCTS = [
    {"sku": "aavin_milk_500ml", "name": "Aavin Milk", "packSizeL": 0.5, "price": 28},
    {"sku": "sakthi_curd_200g", "name": "Sakthi Curd", "packSizeG": 200, "price": 20},
]
FOLLOW_SPEAKER = "If the speaker talks to you in English, answer in English; if in Tamil, answer in Tamil script."

FIRST_AUDIO_TIMEOUT_S = 45.0
QUIET_S = 2.5     # the turn is over when nothing new arrived for this long
TURN_MAX_S = 60.0


def _is_tamil(ch: str) -> bool:
    return "஀" <= ch <= "௿"


def _script_counts(text: str) -> tuple[int, int]:
    tamil = sum(1 for ch in text if _is_tamil(ch))
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    return tamil, latin


def _aira_text(msgs: list, start: int) -> str:
    """What AIRA said since ``start``: the final transcript sentences, or the joined fragments."""
    outs = [m for m in msgs[start:] if m.t == "transcript" and m.role == "aira"]
    finals = [m.text for m in outs if m.final]
    return " ".join(finals) if finals else "".join(m.text for m in outs)


async def _turn(bridge: LiveBridge, rec, text: str, lat, name: str) -> str:
    start = len(rec.msgs)
    t0 = time.perf_counter()
    await bridge.send_text(text)
    await wait_until(lambda: any(m.t == "audio" for m in rec.msgs[start:]), timeout=FIRST_AUDIO_TIMEOUT_S)
    lat.record(f"live.first_audio.{name}", time.perf_counter() - t0)
    # Wait for the answer to finish: the message stream goes quiet.
    deadline = time.perf_counter() + TURN_MAX_S
    seen = len(rec.msgs)
    while time.perf_counter() < deadline:
        await asyncio.sleep(QUIET_S)
        if len(rec.msgs) == seen:
            break
        seen = len(rec.msgs)
    lat.record(f"live.turn_total.{name}", time.perf_counter() - t0)
    errors = [m for m in rec.msgs[start:] if m.t == "error"]
    assert not errors, f"Live sent an error during the {name} turn: {errors}"
    said = _aira_text(rec.msgs, start)
    print(f"[live] {name} turn -> AIRA said: {said!r}")
    return said


async def test_live_answers_tamil_in_tamil_and_english_in_english(mk_session, lat):
    s, rec = mk_session(lang="ta")
    prompt = render_prompt(SHOP, PRODUCTS, [], lang="ta", verbosity="short") + "\n" + FOLLOW_SPEAKER
    bridge = LiveBridge(s, lambda: AdkLiveBackend(s), prompt, [])
    with lat.measure("live.connect"):
        await bridge.start()
    try:
        tamil_said = await _turn(bridge, rec, TAMIL_TURN, lat, "tamil")
        english_said = await _turn(bridge, rec, ENGLISH_TURN, lat, "english")
    finally:
        await bridge.close()

    ta_tamil, _ = _script_counts(tamil_said)
    assert tamil_said.strip(), "no output transcript for the Tamil turn"
    assert ta_tamil >= 3, f"Tamil turn was not answered in Tamil script: {tamil_said!r}"

    en_tamil, en_latin = _script_counts(english_said)
    assert english_said.strip(), "no output transcript for the English turn"
    assert en_latin >= 3, f"English turn has no English words: {english_said!r}"
    assert en_tamil == 0, f"English turn was answered in Tamil script: {english_said!r}"
