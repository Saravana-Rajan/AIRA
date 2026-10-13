"""Object counts AIRA speaks: checked on every turn (also video-only turns), hedged estimates allowed, unhedged
counts need a tool result; the LLM verifier fails closed only for money / unsupported counts; both prompt
variants (ER2 on / Live-only) agree with themselves about counts from the video."""
import re

import pytest

from aira_live import safety
from aira_live.prompt import RULES, RULES_LIVE_ONLY, render_prompt

SHOP = {"name": "Sunrise Dairy"}
CURD = {"sku": "sakthi_curd_1l", "name": "curd", "brand": "Sakthi", "unit": "pouch", "packSizeL": 1, "price": 60}


# --- detection ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("There are 7 bottles.", [(7, False)]),
    ("I can see 12 curd packets on the shelf.", [(12, False)]),
    ("There are seven Aavin milk pouches.", [(7, False)]),
    ("About 7 bottles in the current view.", [(7, True)]),
    ("I think there are 5 cartons.", [(5, True)]),
    ("Roughly 20 cups.", [(20, True)]),
    ("சுமார் 6 பாக்கெட்டுகள் உள்ளன.", [(6, True)]),
    ("6 பாக்கெட் உள்ளது.", [(6, False)]),
    ("लगभग 8 बोतल हैं।", [(8, True)]),
    ("8 बोतल हैं।", [(8, False)]),
    ("There are 7 bottles. About 3 boxes too.", [(7, False), (3, True)]),
])
def test_object_counts_are_found_with_their_hedge(text, expected):
    assert safety.object_counts(text) == expected


@pytest.mark.parametrize("text", [
    "Wait 2 minutes, it will be ready at 5.",
    "Take 3 steps forward, then 1 metre to the left.",
    "Your delivery comes on 12 October 2026.",
    "Call 9876543210 for the vendor.",
    "Sakthi curd is ₹60 per pouch.",
    "It costs 60 rupees a packet.",
    "Aavin milk, 500 ml pouch.",
    "The 2nd packet from the left.",
    "One moment please.",
    "That is one curd packet.",           # identifying a single object is a description, not a count
    "Step 3 of 7. Hold it closer.",
])
def test_ordinary_numbers_are_not_object_counts(text):
    assert safety.object_counts(text) == []


# --- the turn check -----------------------------------------------------------------------------------------
def test_video_only_unhedged_count_is_flagged():
    v = safety.check_turn("There are 7 bottles of milk.", [], [])
    assert v.ok is False and v.unexpected == [7]


def test_video_only_hedged_estimate_passes():
    assert safety.check_turn("About 7 bottles, in the current view.", [], []).ok
    assert safety.check_turn("I think there are 4 packets.", [], []).ok


def test_tool_supported_count_passes_and_approx_needs_about():
    exact = {"kind": "count", "count": 12, "approx": False, "exact": True, "say_en": "I can see 12 curd packets."}
    assert safety.check_turn("I can see 12 curd packets.", [exact], [exact]).ok
    approx = {"kind": "count", "count": 12, "approx": True, "exact": False, "say_en": "About 12 curd packets."}
    assert safety.check_turn("About 12 curd packets.", [approx], [approx]).ok
    v = safety.check_turn("There are 12 curd packets.", [approx], [approx])   # approx said as exact
    assert v.ok is False and v.unexpected == [12]
    unverified = {"kind": "count", "count": 9, "verified": False, "exact": True}
    assert not safety.check_turn("9 bottles.", [unverified], [unverified]).ok
    assert safety.check_turn("Around 9 bottles.", [unverified], [unverified]).ok


def test_a_recent_tool_count_supports_a_later_no_tool_turn():
    recent = {"kind": "count", "count": 6, "approx": False, "exact": True}
    assert safety.check_turn("Yes, 6 bottles.", [], [recent]).ok


def test_hedged_count_contradicting_this_turns_camera_count_is_flagged():
    exact = {"kind": "count", "count": 12, "approx": False, "exact": True}
    v = safety.check_turn("About 20 packets.", [exact], [exact])
    assert v.ok is False and v.unexpected == [20]


def test_hedged_video_estimate_in_a_turn_with_a_non_count_tool_passes():
    price = {"sku": "sakthi_curd_1l", "price": 60, "say_en": "Sakthi curd ₹60."}
    assert safety.check_turn("Curd is ₹60; about 7 packets in view.", [price], [price]).ok
    assert safety.check_turn("Curd is ₹600.", [price], [price]).unexpected == [600]


def test_times_steps_prices_dates_are_not_flagged_on_no_tool_turns():
    for text in ("Wait 2 minutes.", "Take 3 steps forward.", "It is 12 October.", "Open at 5.", "1 metre ahead."):
        assert safety.check_turn(text, [], []).ok, text


def test_states_object_count():
    assert safety.states_object_count("There are 7 bottles.")
    assert safety.states_object_count("About 7 bottles.")
    assert not safety.states_object_count("Take 3 steps.")


# --- verifier outage: fail closed only for critical claims -------------------------------------------------
async def _boom(*a, **k):
    raise TimeoutError


async def test_llm_verify_outage_fails_closed_for_money(monkeypatch):
    monkeypatch.setattr(safety, "generate_json", _boom)
    v = await safety.llm_verify("மொத்தம் அறுநூறு ரூபாய்", [{"total": 60}])
    assert v.ok is False and v.unverified is True


async def test_llm_verify_outage_fails_closed_for_unsupported_count(monkeypatch):
    monkeypatch.setattr(safety, "generate_json", _boom)
    v = await safety.llm_verify("7 பாக்கெட் தயிர் இருக்கு, இரண்டு லிட்டர்", [{"qty": 2}])
    assert v.ok is False and v.unverified is True


async def test_llm_verify_outage_passes_harmless_descriptions(monkeypatch):
    monkeypatch.setattr(safety, "generate_json", _boom)
    assert (await safety.llm_verify("இரண்டு லிட்டர் பால், மேல் அலமாரியில்", [{"qty": 2}])).ok
    assert (await safety.llm_verify("சுமார் 7 பாக்கெட், இரண்டு லிட்டர்", [{"qty": 2}])).ok


def test_unverified_correction_text_asks_to_retract_not_a_named_number():
    t = safety.correction_text([], "en", unverified=True)
    assert t.startswith("Correction needed:") and "could not be verified" in t


def test_is_critical():
    assert safety.is_critical("Total ₹600.", [])
    assert safety.is_critical("மொத்தம் அறுநூறு ரூபாய்", [])
    assert safety.is_critical("There are 7 bottles.", [])
    assert not safety.is_critical("About 7 bottles.", [])
    assert not safety.is_critical("There are 7 bottles.", [{"count": 7, "exact": True}])
    assert not safety.is_critical("The milk is on the top shelf.", [])


# --- prompts ------------------------------------------------------------------------------------------------
def _numbered(rules):
    return [int(re.match(r"(\d+)\. ", r).group(1)) for r in rules]


def test_both_rule_sets_are_numbered_one_to_twenty():
    assert _numbered(RULES) == list(range(1, 21))
    assert _numbered(RULES_LIVE_ONLY) == list(range(1, 21))


def test_live_only_prompt_does_not_both_permit_and_forbid_video_counts():
    off = render_prompt(SHOP, [CURD], [], "en", "short", er2=False)
    low = off.lower()
    assert "about n, in the current view" in low                       # video counts are permitted as estimates
    assert "never state a count" not in low                            # ... and nothing forbids them
    assert "never guess counts" not in low
    assert "kind=count" not in low and "call camera_ask" not in low    # the camera tools do not exist here
    assert "describe_mode with" not in low and "call walk_to" not in low
    assert "ignore every rule above" not in low                        # no contradicting appendix
    assert "never use it as a quantity for stock_add, a sale or a delivery" in low
    assert "21." not in off


def test_er2_prompt_says_a_visible_count_is_not_stock_and_approx_never_for_stock():
    on = render_prompt(SHOP, [CURD], [], "en", "short")
    low = on.lower()
    assert "a visible count is never the stock" in low and "stock_query" in low
    assert "verified false" in low and "scope" in low
    assert "never use it as a quantity for stock_add, a delivery or a sale" in low
    assert "never state a count, a price read from a label" in low
    assert "about n, in the current view" not in low


# --- bridge: the turn-end check also runs on video-only turns ---------------------------------------------
async def _video_turn(mk_session, text, tool_result=None):
    import asyncio

    from live_fakes import FakeBackend, wait_until

    from aira_live.live_bridge import LiveBridge, LiveEvent

    s, rec = mk_session()
    b = FakeBackend()
    bridge = LiveBridge(s, lambda: b, "prompt", [])
    await bridge.start()
    if tool_result is not None:
        s.tool_results.append({"tool": "camera_ask", "call_id": "c1", "result": tool_result})
    b.push(LiveEvent("output_transcript", text=text, final=True))
    b.push(LiveEvent("turn_complete"))
    b.push(LiveEvent("audio", audio=b"next"))
    await wait_until(lambda: [m for m in rec.msgs if m.t == "audio"])
    for _ in range(30):
        await asyncio.sleep(0)
    cues = [m.cue for m in rec.msgs if m.t == "cue"]
    sent = list(b.sent_text)
    await bridge.close()
    return cues, sent


async def test_bridge_flags_an_unhedged_count_from_the_video(mk_session):
    cues, sent = await _video_turn(mk_session, "There are 7 bottles of milk.")
    assert "warn" in cues and sent == [safety.correction_text([7], "en")]


async def test_bridge_lets_a_hedged_video_estimate_pass(mk_session):
    cues, sent = await _video_turn(mk_session, "About 7 bottles, in the current view.")
    assert "warn" not in cues and sent == []


async def test_bridge_lets_a_tool_count_pass_and_skips_plain_descriptions(mk_session):
    res = {"kind": "count", "count": 7, "approx": False, "exact": True, "say_en": "I can see 7 bottles."}
    cues, sent = await _video_turn(mk_session, "I can see 7 bottles.", res)
    assert "warn" not in cues and sent == []
    cues, sent = await _video_turn(mk_session, "Milk is on the top shelf, 3 steps ahead.")
    assert "warn" not in cues and sent == []
