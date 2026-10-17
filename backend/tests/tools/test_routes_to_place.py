"""tools.routes.route_to: walk to a shop-map fixed point (the light switch board) with the route follower."""
from pathlib import Path

import pytest

from aira_live import shop_map
from aira_live.session import current_session
from aira_live.tools import light, routes, walk

ROOT = Path(__file__).resolve().parents[3]
DEMO_MAP = ROOT / "shared" / "catalog" / "demo_cricket_map.json"


@pytest.fixture
def session(mk_session):
    s, rec = mk_session(lang="en")
    token = current_session.set(s)
    shop_map.clear_cache()
    yield s
    current_session.reset(token)
    walk._ACTIVE.clear()
    shop_map.clear_cache()


@pytest.fixture
def started(monkeypatch):
    calls = []

    async def fake_start(session, target, run=True, **kw):
        calls.append(target)

    monkeypatch.setattr(walk, "start", fake_start)
    return calls


def test_find_point_matches_the_switch_board_by_id_or_name():
    m = shop_map.load_file(DEMO_MAP)
    zone, point = routes.find_point(m, light.ROUTE_PLACE)
    assert point["id"] == "light_switch_board" and zone.fixed_points
    assert routes.find_point(m, "light switch board")[1] is point
    assert routes.find_point(m, "fire exit") is None


async def test_off_unless_walk_routes_and_a_map(session, started, monkeypatch):
    monkeypatch.delenv("AIRA_ROUTES", raising=False)
    monkeypatch.setenv("AIRA_WALK", "1")
    assert await routes.route_to(session, "switch_board") is None
    monkeypatch.setenv("AIRA_ROUTES", "1")
    monkeypatch.setenv("AIRA_ROUTES_MAP", str(ROOT / "no_such_map.json"))
    assert await routes.route_to(session, "switch_board") is None
    assert started == []


async def test_walks_to_the_switch_board_zone_with_the_switch_labels(session, started, monkeypatch):
    monkeypatch.setenv("AIRA_WALK", "1")
    monkeypatch.setenv("AIRA_ROUTES", "1")
    monkeypatch.setenv("AIRA_ROUTES_MAP", str(DEMO_MAP))
    out = await routes.route_to(session, light.ROUTE_PLACE)
    m = shop_map.load_file(DEMO_MAP)
    zone, point = routes.find_point(m, "switch_board")
    assert started == [zone.name]
    assert out["place"] == point["name"] and out["switch_labels"] == point["switches"]
    assert "step by step" in out["say_en"]


async def test_light_switch_find_delegates_to_the_route_when_one_exists(session, started, monkeypatch):
    monkeypatch.setenv("AIRA_WALK", "1")
    monkeypatch.setenv("AIRA_ROUTES", "1")
    monkeypatch.setenv("AIRA_LOWLIGHT", "1")
    monkeypatch.setenv("AIRA_ROUTES_MAP", str(DEMO_MAP))
    out = await light.light_switch_find()
    assert out["routed"] is True and out["then"] == "light_switch_find" and len(started) == 1
