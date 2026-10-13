"""AIRA_ER2=0 is a Live-only test mode: no Robotics-ER tools, and a prompt rule that has Live answer from video."""
from aira_live import config
from aira_live.prompt import RULES, RULES_LIVE_ONLY, render_prompt
from aira_live.tools import ER_TOOLS, TOOL_FUNCS, active_tools

SHOP = {"name": "Sunrise Dairy"}


def test_er2_is_on_by_default(monkeypatch):
    monkeypatch.delenv("AIRA_ER2", raising=False)
    assert config.load().er2 is True
    monkeypatch.setenv("AIRA_ER2", "0")
    assert config.load().er2 is False


def test_active_tools_keeps_everything_with_er2_and_drops_the_er_tools_without():
    assert [f.__name__ for f in active_tools(True)] == [f.__name__ for f in TOOL_FUNCS]
    names = {f.__name__ for f in active_tools(False)}
    assert not names & ER_TOOLS
    assert {"sale_start", "price_query", "stock_query", "web_search"} <= names
    assert ER_TOOLS <= {f.__name__ for f in TOOL_FUNCS}  # every name in the set is a real tool


def test_the_live_only_rules_replace_the_camera_rules_only_when_er2_is_off():
    on = render_prompt(SHOP, [], [], "en", "short")
    off = render_prompt(SHOP, [], [], "en", "short", er2=False)
    assert all(r in on for r in RULES) and not any(r in on for r in set(RULES_LIVE_ONLY) - set(RULES))
    assert all(r in off for r in RULES_LIVE_ONLY) and not any(r in off for r in set(RULES) - set(RULES_LIVE_ONLY))
    assert "about N, in the current view" in off and "never read money amounts or prices off the video" in off.lower()
