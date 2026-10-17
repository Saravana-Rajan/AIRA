"""The shop map behind step-by-step routes (aira_live.shop_map): schema, loading, aliases, planning, the flag."""
import copy
import json
from pathlib import Path

import pytest

from aira_live import config, shop_map

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "shared" / "catalog" / "demo_cricket_map.json"


@pytest.fixture
def data() -> dict:
    return json.loads(DEMO.read_text(encoding="utf-8"))


@pytest.fixture
def demo() -> shop_map.ShopMap:
    return shop_map.load_file(DEMO)


# ---------- flag ----------
def test_aira_routes_is_off_by_default(monkeypatch):
    monkeypatch.delenv("AIRA_ROUTES", raising=False)
    monkeypatch.delenv("AIRA_ROUTES_MAP", raising=False)
    s = config.load()
    assert s.routes is False and s.routes_map is None
    monkeypatch.setenv("AIRA_ROUTES", "1")
    monkeypatch.setenv("AIRA_ROUTES_MAP", "/tmp/x_map.json")
    s = config.load()
    assert s.routes is True and s.routes_map == "/tmp/x_map.json"


def test_env_example_documents_the_flag_commented_out():
    text = (ROOT / "shared" / ".env.example").read_text(encoding="utf-8")
    assert "# AIRA_ROUTES=1" in text
    assert "\nAIRA_ROUTES=1" not in text


# ---------- schema and loading ----------
def test_demo_map_validates_and_loads(demo, data):
    shop_map.validate(data)
    assert demo.shop_id == "demo_cricket" and not demo.draft
    assert set(demo.zones) == {"entrance", "counter", "bat_wall", "kit_wall", "ball_shelf"}
    board = next(p for p in demo.zones["entrance"].fixed_points if p["id"] == "light_switch_board")
    assert any("shop light" in s for s in board["switches"])
    assert any(h.type == "mirror" and h.zone == "bat_wall" for h in demo.hazards)
    assert all(r.legs[-1].type == "checkpoint" for r in demo.routes)


@pytest.mark.parametrize("break_it, message", [
    (lambda d: d["zones"][0].pop("aliases"), "aliases"),
    (lambda d: d["routes"][0].__setitem__("to", "garage"), "unknown zone"),
    (lambda d: d["routes"][0]["legs"].append({"type": "walk", "steps": 2}), "last leg"),
    (lambda d: d["routes"][0]["legs"].insert(0, {"type": "turn", "dir": "backwards"}), "dir"),
    (lambda d: d["routes"][0]["legs"].insert(0, {"type": "walk", "steps": 0}), "steps"),
    (lambda d: d["hazards"][1]["reflects"].append("roof"), "unknown zone"),
    (lambda d: d["zones"].append(copy.deepcopy(d["zones"][0])), "duplicate"),
    (lambda d: d["zones"][0].__setitem__("views", {"front": "x", "up": "y"}), "views"),
    (lambda d: d.__setitem__("extra", 1), "extra"),
])
def test_invalid_maps_raise_map_error(data, break_it, message):
    break_it(data)
    with pytest.raises(shop_map.MapError) as exc:
        shop_map.from_dict(data)
    assert message in str(exc.value)


def test_missing_or_broken_file_turns_the_feature_off_with_a_log_line(tmp_path, caplog):
    shop_map.clear_cache()
    with caplog.at_level("WARNING"):
        assert shop_map.load_for("nobody", str(tmp_path / "none_map.json"), use_cache=False) is None
    assert any("routes off" in r.message and "nobody" in r.message for r in caplog.records)
    bad = tmp_path / "bad_map.json"
    bad.write_text("{not json", encoding="utf-8")
    caplog.clear()
    with caplog.at_level("WARNING"):
        assert shop_map.load_for("nobody", str(bad), use_cache=False) is None
    assert any("not JSON" in r.message for r in caplog.records)
    invalid = tmp_path / "invalid_map.json"
    invalid.write_text(json.dumps({"note": "x", "shop": {"id": "a", "name": "A"}, "zones": [], "routes": []}),
                       encoding="utf-8")
    assert shop_map.load_for("nobody", str(invalid), use_cache=False) is None


def test_load_for_finds_the_repo_map_by_shop_id_and_caches(caplog):
    shop_map.clear_cache()
    with caplog.at_level("INFO"):
        m = shop_map.load_for("demo_cricket")
    assert m is not None and m.path == str(DEMO)
    assert any("routes: shop map" in r.message for r in caplog.records)
    assert shop_map.load_for("demo_cricket") is m
    assert shop_map.map_path("demo_cricket") == DEMO
    assert shop_map.map_path("demo_cricket", "/x/y.json") == Path("/x/y.json")
    shop_map.clear_cache()


# ---------- aliases: any language ----------
@pytest.mark.parametrize("text, zone", [
    ("counter", "counter"), ("the billing counter", "counter"), ("take me to the bat wall", "bat_wall"),
    ("bats", "bat_wall"), ("gloves", "kit_wall"), ("batting gloves", "kit_wall"), ("front door", "entrance"),
    ("கவுண்டர்", "counter"), ("மட்டை சுவர்", "bat_wall"), ("என்னை கவுண்டர் க்கு கூட்டிட்டு போ", "counter"),
    ("काउंटर", "counter"), ("दस्ताने", "kit_wall"), ("ball shelf", "ball_shelf"), ("kit_wall", "kit_wall"),
])
def test_resolve_matches_aliases_in_any_language(demo, text, zone):
    assert demo.resolve(text) == zone


@pytest.mark.parametrize("text", ["fridge", "the toilet", "", "   ", "milk", "குளியலறை", "छत"])
def test_resolve_unknown_alias_is_none(demo, text):
    assert demo.resolve(text) is None


# ---------- planning ----------
def test_plan_direct_route(demo):
    legs = demo.plan("entrance", "counter")
    assert legs is not None
    assert [l.type for l in legs] == ["walk", "checkpoint"] and legs[0].steps == 8 and legs[1].zone == "counter"
    assert demo.start_facing("entrance", "counter") == "front"


def test_plan_chains_routes_when_no_direct_one_exists(demo):
    legs = demo.plan("entrance", "ball_shelf")                       # entrance -> counter -> ball shelf
    assert legs is not None
    assert [l.type for l in legs] == ["walk", "checkpoint", "turn", "walk", "turn", "walk", "checkpoint"]
    assert legs[1].zone == "counter" and legs[-1].zone == "ball_shelf"
    assert demo.start_facing("entrance", "ball_shelf") == "front"


def test_plan_none_when_no_path_or_unknown_zone(demo):
    assert demo.plan("ball_shelf", "entrance") is None                # no route leaves the ball shelf
    assert demo.plan("entrance", "garage") is None
    assert demo.plan("garage", "entrance") is None


def test_plan_same_zone_is_an_arrival_checkpoint(demo):
    (leg,) = demo.plan("counter", "counter")
    assert leg.type == "checkpoint" and leg.zone == "counter" and "counter" in leg.say


def test_mirror_reflects(demo):
    assert demo.mirror_reflects("counter") and demo.mirror_reflects("entrance")
    assert not demo.mirror_reflects("bat_wall") and not demo.mirror_reflects("ball_shelf")


# ---------- the prompt text ----------
def test_prompt_text_lists_zones_views_and_the_mirror_within_the_cap(demo):
    text = demo.prompt_text()
    assert text.startswith("ZONES of this shop")
    for zid in demo.zones:
        assert f"- {zid}:" in text
    assert "front:" in text and "MIRROR" in text and "reflection=true" in text
    assert len(text) <= shop_map.ZONE_PROMPT_MAX_CHARS
    short = demo.prompt_text(max_chars=120)
    assert len(short) <= 120 and short.startswith("ZONES") and short.count("\n") < text.count("\n")

