import pytest

from aira_live.models import model_for
from aira_live.vision.count import COUNT_PROMPT, COUNT_SCHEMA, MAX_ITEMS, assess, count_in_frame
from aira_live.vision.gemini import VisionError


def grid(n, start=150, step=120):
    pts = []
    for i in range(n):
        pts.append({"point": [start + (i // 5) * step, start + (i % 5) * step], "label": "pack", "cut": False})
    return pts


def test_clean_grid_counts_with_full_confidence():
    r = assess(grid(12))
    assert r.count == 12 and r.confidence == 1.0 and r.needs_retake is False and r.aim_hint == ""


def test_dedupes_double_points():
    items = grid(4) + [{"point": [152, 151], "label": "pack"}]   # same pack pointed twice
    assert assess(items).count == 4


def test_ignores_invalid_points():
    items = grid(3) + [{"point": [1200, 5]}, {"point": [1]}, {"point": "a"}, "junk", {"label": "no point"},
                       {"point": [True, 400]}, {"point": [-1, 400]}, {"point": None}, None]
    assert assess(items).count == 3


def test_non_list_reply_is_treated_as_nothing_seen():
    for junk in (None, 5, "packs", {"point": [100, 100]}):
        r = assess(junk)
        assert r.count is None and r.needs_retake is True and r.aim_hint == "vision.aim.show_items"


def test_edge_cut_items_ask_to_move_back():
    items = grid(5) + [{"point": [10, 500], "label": "pack", "cut": True}]
    r = assess(items)
    assert r.count == 6 and r.needs_retake is True and r.aim_hint == "vision.aim.move_back"
    assert r.confidence < 1.0


def test_point_near_frame_edge_counts_as_cut_even_without_flag():
    items = grid(4) + [{"point": [500, 990], "label": "pack"}]
    r = assess(items)
    assert r.count == 5 and r.needs_retake is True and r.aim_hint == "vision.aim.move_back"


def test_cut_flag_as_string_false_is_not_cut():
    items = grid(3)
    items[0]["cut"] = "false"
    r = assess(items)
    assert r.needs_retake is False and r.aim_hint == ""


def test_overlapping_cluster_asks_to_spread_out():
    items = [{"point": [500, 500 + i * 28], "label": "pack"} for i in range(6)]
    r = assess(items)
    assert r.needs_retake is True and r.aim_hint == "vision.aim.spread_out"
    assert r.count == 6 and r.confidence < 1.0


def test_hidden_item_asks_to_spread_out_not_move_back():
    items = grid(12)
    items[6]["hidden"] = True                    # a centre pack partly behind another one
    r = assess(items)
    assert r.count == 12 and r.needs_retake is True and r.aim_hint == "vision.aim.spread_out"
    assert r.confidence < 1.0


def test_old_cut_flag_away_from_the_edge_is_treated_as_overlap():
    items = grid(12)
    items[6]["cut"] = True                       # centre point: the frame edge cannot be the cause
    r = assess(items)
    assert r.count == 12 and r.needs_retake is True and r.aim_hint == "vision.aim.spread_out"


def test_edge_flag_asks_to_move_back_even_away_from_the_margin():
    items = grid(5) + [{"point": [500, 900], "label": "pack", "edge": True}]
    r = assess(items)
    assert r.count == 6 and r.needs_retake is True and r.aim_hint == "vision.aim.move_back"


def test_edge_cut_wins_over_hidden_items():
    items = grid(5) + [{"point": [10, 500], "label": "pack", "edge": True}]
    items[2]["hidden"] = True
    assert assess(items).aim_hint == "vision.aim.move_back"


def test_hidden_flag_as_string_false_is_not_hidden():
    items = grid(3)
    items[0]["hidden"] = "false"
    items[1]["edge"] = "False"
    r = assess(items)
    assert r.needs_retake is False and r.aim_hint == ""


def well_spaced(n):
    """n points on a 10-wide grid, 90 apart and well inside the frame edge."""
    return [{"point": [100 + (i // 10) * 90, 100 + (i % 10) * 90], "label": "pack"} for i in range(n)]


def test_reply_at_the_item_cap_never_gives_a_count():
    r = assess(well_spaced(MAX_ITEMS))           # the model stopped at the cap: more may be there
    assert r.count is None and r.needs_retake is True and r.aim_hint == "vision.aim.hold_closer"
    assert r.confidence == 0.0


def test_reply_over_the_item_cap_never_gives_a_count():
    r = assess(well_spaced(MAX_ITEMS + 5))
    assert r.count is None and r.needs_retake is True and r.aim_hint == "vision.aim.hold_closer"


def test_reply_just_under_the_item_cap_is_counted():
    r = assess(well_spaced(MAX_ITEMS - 1))
    assert r.count == MAX_ITEMS - 1 and r.needs_retake is False and r.aim_hint == ""


def test_prompt_is_the_official_er_pointing_form_with_the_cap():
    assert MAX_ITEMS == 80
    assert f"no more than {MAX_ITEMS} items" in COUNT_PROMPT
    p = COUNT_PROMPT.format(label="milk pouch")
    assert "Point to each milk pouch" in p and "one point per item" in p
    assert "label each" in p.lower() and "product name" in p and "0-1000" in p
    for dropped in ("box_2d", '"edge"', '"hidden"', '"cut"'):
        assert dropped not in COUNT_PROMPT and dropped not in COUNT_SCHEMA


def test_points_closer_than_25_are_one_item():
    from aira_live.vision.count import DEDUPE_RADIUS

    assert DEDUPE_RADIUS == 25
    items = [{"point": [500, 300], "label": "pack"}, {"point": [500, 322], "label": "pack"},
             {"point": [500, 600], "label": "pack"}]
    assert assess(items).count == 2


def test_named_product_counts_only_matching_labels():
    items = [{"point": [300, 100], "label": "Hatsun Curd 500g"}, {"point": [300, 300], "label": "hatsun curd"},
             {"point": [300, 500], "label": "Aavin Curd"}, {"point": [300, 700], "label": "Arokya milk"}]
    r = assess(items, product="Hatsun Curd")
    assert r.count == 2 and r.needs_retake is False


def test_unreadable_labels_with_a_named_product_are_not_sure():
    items = [{"point": [300, 100], "label": "Hatsun Curd"}, {"point": [300, 300], "label": "unknown"},
             {"point": [300, 500], "label": "Hatsun curd"}]
    r = assess(items, product="Hatsun Curd")
    assert r.count == 2 and r.needs_retake is True and r.confidence < 0.5


async def test_count_in_frame_uses_the_count_call_and_reports_the_model(monkeypatch, frame):
    from aira_live.vision import count as vcount

    seen = {}

    async def fake(parts, thinking="medium", timeout_s=25.0):
        seen.update(thinking=thinking, timeout_s=timeout_s, prompt=parts[-1])
        return {"items": [{"point": [300, 300], "label": "curd"}, {"point": [300, 600], "label": "curd"}],
                "_model": "er2"}

    monkeypatch.setattr(vcount, "generate_count_json", fake)
    r = await count_in_frame(frame, "curd packet")
    assert seen["thinking"] == "medium" and seen["timeout_s"] == vcount.COUNT_TIMEOUT_S >= 20
    assert "Point to each curd packet" in seen["prompt"]
    assert r.count == 2 and r.model == "er2"


async def test_a_truncated_count_reply_is_never_a_count(monkeypatch, frame):
    from aira_live.vision import count as vcount
    from aira_live.vision.gemini import TruncatedReply

    async def cut(parts, thinking="medium", timeout_s=25.0):
        raise TruncatedReply("MAX_TOKENS")

    monkeypatch.setattr(vcount, "generate_count_json", cut)
    r = await count_in_frame(frame, "curd packet")
    assert r.count is None and r.needs_retake is True


async def test_count_in_frame_at_the_cap_never_invents_a_count(fake_genai, frame):
    import json

    fake_genai(json.dumps(well_spaced(MAX_ITEMS)))
    r = await count_in_frame(frame, "Aavin milk 500 ml pouch")
    assert r.count is None and r.needs_retake is True and r.aim_hint == "vision.aim.hold_closer"


def test_nothing_visible_returns_none():
    r = assess([])
    assert r.count is None and r.needs_retake is True and r.aim_hint == "vision.aim.show_items"
    assert r.confidence == 0.0


async def test_count_in_frame_uses_er2_count_role(fake_genai, frame):
    client = fake_genai('[{"point": [200, 200], "label": "Sakthi curd 1 L pouch"}, '
                        '{"point": [200, 400], "label": "Sakthi curd 1 L pouch"}]')
    r = await count_in_frame(frame, "Sakthi curd 1 L pouch")
    assert r.count == 2
    call = client.aio.models.calls[0]
    assert call["model"] == model_for("count")
    assert any("Sakthi curd 1 L pouch" in str(p) for p in call["contents"])


async def test_count_in_frame_accepts_wrapped_dict(fake_genai, frame):
    fake_genai('{"points": [{"point": [300, 300]}, {"point": [300, 600]}, {"point": [600, 300]}]}')
    assert (await count_in_frame(frame, "Aavin milk 500 ml pouch")).count == 3


async def test_count_in_frame_accepts_a_single_point_object(fake_genai, frame):
    fake_genai('{"point": [500, 500], "label": "Arun ice cream box"}')
    r = await count_in_frame(frame, "Arun ice cream box")
    assert r.count == 1 and r.needs_retake is False


async def test_count_in_frame_empty_reply_never_invents_a_count(fake_genai, frame):
    fake_genai('{"items": []}')
    r = await count_in_frame(frame, "Aavin milk 500 ml pouch")
    assert r.count is None and r.aim_hint == "vision.aim.show_items"


async def test_count_in_frame_reply_without_points_never_invents_a_count(fake_genai, frame):
    fake_genai('{"count": 12}')   # a bare number with nothing to point at is not evidence
    r = await count_in_frame(frame, "Sakthi curd 1 L pouch")
    assert r.count is None and r.needs_retake is True


async def test_count_in_frame_raises_when_every_model_fails(fake_genai, frame):
    fake_genai("x", "y", "z")
    with pytest.raises(VisionError):
        await count_in_frame(frame, "Aavin milk 1 L pouch")
