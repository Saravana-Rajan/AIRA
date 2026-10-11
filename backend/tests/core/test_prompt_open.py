"""AIRA answers anything (owner request: "whatever the person asks, it can answer; don't restrict him").

The prompt makes AIRA a general assistant first, the shop tools extra abilities; current facts go through
web_search (Google Search grounding on the read model). The hard safety rules stay.
"""
import re

from aira_live.prompt import render_prompt

CURD = {"sku": "sakthi_curd_1l", "name": "curd", "brand": "Sakthi", "unit": "pouch", "packSizeL": 1, "price": 60}


def _prompt() -> str:
    return render_prompt({"name": "Sunrise Dairy"}, [CURD], [], "ta", "short")


def test_prompt_has_no_refusal_wording():
    low = _prompt().lower()
    for phrase in ("only help with the shop", "only about the shop", "only answer questions about", "refuse",
                   "politely decline", "not related to the shop", "off-topic", "stay on topic"):
        assert phrase not in low, phrase


def test_prompt_is_a_general_assistant_first_with_the_open_answer_rule():
    p = _prompt()
    low = p.lower()
    assert "helpful general assistant" in low
    assert re.search(r"answer any question", low)
    for topic in ("general knowledge", "maths", "recipes", "health", "news", "weather", "small talk"):
        assert topic in low, topic
    assert "doctor or pharmacist" in low
    assert "web_search" in p and ("current" in low or "real-time" in low)
    assert "speaker's language" in low


def test_prompt_keeps_the_hard_rules():
    p = _prompt()
    low = p.lower()
    assert "read it back" in low and "confirm_action" in p                      # confirm before commit
    assert "never say a number" in low                                          # money/stock through tools
    assert "camera_ask" in p and "never answer from imagination" in low         # vision facts only from tools
    assert "never say a currency note is real or fake" in low                   # counterfeit
