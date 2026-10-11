"""(b) Gemini Robotics-ER 2 (``model_for("count")`` / ``model_for("point")``) on a synthetic image.

The image is drawn in the test: N blue circles on a white canvas (plus one red square for the pointing
test), with a banner saying it is synthetic. Nothing here is a claim about real shelves or real packs; it
only checks that the pointing model returns one point per drawn item, where the item was drawn.
"""
from __future__ import annotations

import math

import pytest

from aira_live.models import model_for
from aira_live.vision.count import COUNT_PROMPT, COUNT_SCHEMA, assess, count_in_frame
from aira_live.vision.gemini import generate_json, image_part
from aira_live.vision.point import point_to

N = 6
COUNT_TOLERANCE = 1      # |count - N| allowed
POINT_RADIUS = 120       # 0..1000 units: a model point this close to a drawn centre is "on" it


def _dist(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _matched(points: list[tuple[int, int]], centres: list[tuple[int, int]]) -> int:
    """Greedy one-to-one matching: how many drawn centres have a model point within POINT_RADIUS."""
    free = list(points)
    hits = 0
    for c in centres:
        best = min(free, key=lambda p: _dist(p, c), default=None)
        if best is not None and _dist(best, c) <= POINT_RADIUS:
            free.remove(best)
            hits += 1
    return hits


async def test_count_in_frame_sees_n_drawn_items(synth, lat):
    img, drawn = synth.draw_items(N)
    frame = synth.to_frame(img, "count")
    with lat.measure("er2.count_in_frame"):
        result = await count_in_frame(frame, "blue circle")
    print(f"[er2] count_in_frame -> {result}")
    assert result.count is not None, f"no count (hint {result.aim_hint!r}); ER 2 pointed at nothing usable"
    assert abs(result.count - N) <= COUNT_TOLERANCE, f"drew {N}, counted {result.count}"


async def test_raw_points_land_on_the_drawn_centres(synth, lat):
    img, drawn = synth.draw_items(N)
    centres = [d.yx for d in drawn]
    with lat.measure("er2.points_raw"):
        data = await generate_json("count", [image_part(synth.to_jpeg_b64(img)), COUNT_PROMPT.format(label="blue circle")],
                                   COUNT_SCHEMA, thinking="high", timeout_s=30.0)
    model = data.get("_model")
    print(f"[er2] model that answered: {model}")
    assert model == model_for("count"), f"answered by the fallback {model!r}, not {model_for('count')!r}"
    items = data.get("items") or data.get("points") or []
    points = [(round(it["point"][0]), round(it["point"][1])) for it in items
              if isinstance(it, dict) and isinstance(it.get("point"), list) and len(it["point"]) == 2]
    print(f"[er2] drawn centres [y,x]: {centres}")
    print(f"[er2] model points   [y,x]: {points}")
    assert abs(len(points) - N) <= COUNT_TOLERANCE, f"drew {N} items, got {len(points)} points"
    hits = _matched(points, centres)
    assert hits >= N - COUNT_TOLERANCE, f"only {hits} of {N} drawn centres have a point within {POINT_RADIUS}"
    assessed = assess(items)
    assert assessed.count == len(points)


async def test_point_to_finds_the_odd_item(synth, lat):
    img, drawn = synth.draw_items(N, odd_one="red square")
    target = next(d for d in drawn if d.label == "red square")
    frame = synth.to_frame(img, "find")
    with lat.measure("er2.point_to"):
        result = await point_to(frame, "red square")
    print(f"[er2] point_to -> {result}; drawn at {target.yx}")
    assert result is not None, "point_to returned None for an item that is drawn in the image"
    assert _dist(result.point, target.yx) <= POINT_RADIUS, f"pointed at {result.point}, drawn at {target.yx}"
    blue = [d.yx for d in drawn if d.label != "red square"]
    nearest_blue = min(_dist(result.point, b) for b in blue)
    assert nearest_blue > _dist(result.point, target.yx), "the point is closer to a blue circle than to the red square"


@pytest.mark.parametrize("n", [1, 3])
async def test_count_small_n(synth, lat, n):
    img, _ = synth.draw_items(n)
    with lat.measure(f"er2.count_in_frame.n{n}"):
        result = await count_in_frame(synth.to_frame(img, "count"), "blue circle")
    print(f"[er2] n={n} -> {result}")
    assert result.count == n, f"drew {n}, counted {result.count}"
