"""shared/scripts/describe_shop_photos.py: view-text drafts for the shop map from photos. Offline: a fake model."""
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from aira_live import models, shop_map

ROOT = Path(__file__).resolve().parents[3]
DEMO_MAP = ROOT / "shared" / "catalog" / "demo_cricket_map.json"

_spec = importlib.util.spec_from_file_location("describe_shop_photos",
                                               ROOT / "shared" / "scripts" / "describe_shop_photos.py")
dsp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dsp)

FAKE_JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes\xff\xd9"


class FakeClient:
    """A google-genai look-alike: ``client.models.generate_content(...)`` returns a canned reply text."""

    def __init__(self, reply: str = '{"view": "a wooden counter with a glass top, shelves behind it"}'):
        self.reply = reply
        self.calls: list[dict] = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents})
        return SimpleNamespace(text=self.reply)


def _load_demo() -> dict:
    return json.loads(DEMO_MAP.read_text(encoding="utf-8"))


@pytest.fixture
def shop(tmp_path):
    """A tmp copy of the demo map with one empty view and photos for a few views."""
    data = _load_demo()
    for z in data["zones"]:
        if z["id"] == "counter":
            z["views"]["left"] = ""              # missing text: to be written
        if z["id"] == "entrance":
            z["views"]["front"] = "human text about the counter"
    map_path = tmp_path / "demo_cricket_map.json"
    map_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    photos = tmp_path / "photos"
    photos.mkdir()
    for name in ("counter_left.jpg", "entrance_front.jpg", "bat_wall_front.png", "notes.txt", "kit_wall_up.jpg"):
        (photos / name).write_bytes(FAKE_JPEG)
    return SimpleNamespace(map_path=map_path, photos=photos, data=data)


def _zone(data: dict, zid: str) -> dict:
    return next(z for z in data["zones"] if z["id"] == zid)


# ---------- the model role ----------
def test_describe_role_exists_in_models():
    assert models.DEFAULTS["describe"] == "gemini-3.8-flash"
    assert "describe" in models.Role.__args__
    assert models.model_for("describe") == "gemini-3.8-flash"


# ---------- photo names ----------
@pytest.mark.parametrize("name,expected", [
    ("entrance_front.jpg", ("entrance", "front")),
    ("bat_wall_left.jpeg", ("bat_wall", "left")),
    ("Ball_Shelf_BACK.PNG", ("ball_shelf", "back")),
    ("some/dir/kit_wall_right.png", ("kit_wall", "right")),
    ("kit_wall_up.jpg", None),            # not a facing
    ("entrance.jpg", None),               # no facing
    ("entrance_front.gif", None),         # bad extension
    ("notes.txt", None),
    ("entrance_front", None),
])
def test_parse_photo_name(name, expected):
    assert dsp.parse_photo_name(name) == expected


def test_find_photos_uses_folder_and_zone_photo_lists(shop, caplog):
    found = dsp.find_photos(shop.data, shop.photos)
    assert set(found) == {("counter", "left"), ("entrance", "front"), ("bat_wall", "front")}
    assert found[("bat_wall", "front")] == shop.photos / "bat_wall_front.png"
    only = dsp.find_photos(shop.data, shop.photos, only_zone="counter")
    assert set(only) == {("counter", "left")}
    assert dsp.find_photos(shop.data, shop.photos / "missing") == {}


# ---------- apply_drafts ----------
def test_apply_drafts_writes_missing_text_and_marks_draft():
    data = _load_demo()
    _zone(data, "counter")["views"]["left"] = ""
    del _zone(data, "bat_wall")["views"]["back"]
    out, actions = dsp.apply_drafts(data, {("counter", "left"): "  the bat wall  far end ", ("bat_wall", "back"): "x"})
    assert sorted(actions) == [("bat_wall", "back", "written"), ("counter", "left", "written")]
    assert _zone(out, "counter")["views"]["left"] == "the bat wall far end"
    assert _zone(out, "counter")["draft_views"] == ["left"]
    assert _zone(out, "bat_wall")["draft_views"] == ["back"]
    assert out["draft"] is True
    assert _zone(data, "counter")["views"]["left"] == ""          # input untouched
    assert "draft_views" not in _zone(data, "counter")
    shop_map.validate(out)
    shop_map.from_dict(out)


def test_apply_drafts_keeps_human_text_without_overwrite():
    data = _load_demo()
    before = _zone(data, "entrance")["views"]["front"]
    out, actions = dsp.apply_drafts(data, {("entrance", "front"): "machine text"})
    assert actions == [("entrance", "front", "kept")]
    assert _zone(out, "entrance")["views"]["front"] == before
    assert "draft_views" not in _zone(out, "entrance")
    assert out["draft"] is False


def test_apply_drafts_refreshes_machine_draft():
    data = _load_demo()
    _zone(data, "entrance")["draft_views"] = ["front"]
    data["draft"] = True
    out, actions = dsp.apply_drafts(data, {("entrance", "front"): "new machine text"})
    assert actions == [("entrance", "front", "overwritten")]
    assert _zone(out, "entrance")["views"]["front"] == "new machine text"
    assert _zone(out, "entrance")["draft_views"] == ["front"]
    assert out["draft"] is True


def test_apply_drafts_overwrite_replaces_human_text():
    data = _load_demo()
    out, actions = dsp.apply_drafts(data, {("entrance", "front"): "forced"}, overwrite=True)
    assert actions == [("entrance", "front", "overwritten")]
    assert _zone(out, "entrance")["views"]["front"] == "forced"
    assert _zone(out, "entrance")["draft_views"] == ["front"]
    assert out["draft"] is True


def test_apply_drafts_draft_flag_cleared_when_no_zone_has_drafts():
    data = _load_demo()
    data["draft"] = True
    _zone(data, "entrance")["draft_views"] = []
    out, actions = dsp.apply_drafts(data, {})
    assert actions == []
    assert out["draft"] is False


def test_apply_drafts_ignores_empty_unknown_and_caps_length():
    data = _load_demo()
    _zone(data, "counter")["views"]["left"] = ""
    long = "x" * 400
    out, actions = dsp.apply_drafts(data, {
        ("counter", "left"): long, ("counter", "front"): "   ", ("nowhere", "front"): "t", ("counter", "up"): "t"})
    assert actions == [("counter", "left", "written")]
    assert len(_zone(out, "counter")["views"]["left"]) == dsp.MAX_VIEW_CHARS
    shop_map.validate(out)


def test_apply_drafts_draft_views_ordered_and_unique():
    data = _load_demo()
    _zone(data, "counter")["draft_views"] = ["back"]
    out, _ = dsp.apply_drafts(data, {("counter", "front"): "a", ("counter", "back"): "b"}, overwrite=True)
    assert _zone(out, "counter")["draft_views"] == ["front", "back"]


# ---------- describe_photo with a fake client ----------
def test_describe_photo_calls_model_with_image_and_prompt():
    client = FakeClient('```json\n{"view": "  rows of bats in racks,  handles up. "}\n```')
    text = dsp.describe_photo(client, "gemini-3.8-flash", FAKE_JPEG, "image/jpeg", "bat wall")
    assert text == "rows of bats in racks, handles up."
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["model"] == "gemini-3.8-flash"
    part, prompt = call["contents"]
    assert part.inline_data.data == FAKE_JPEG
    assert part.inline_data.mime_type == "image/jpeg"
    assert "bat wall" in prompt and '"view"' in prompt


@pytest.mark.parametrize("reply", ["", "   ", "not json at all", '{"other": 1}', '{"view": 7}', "[1, 2]",
                                   '{"view": ""}'])
def test_describe_photo_bad_reply_gives_empty(reply):
    assert dsp.describe_photo(FakeClient(reply), "m", FAKE_JPEG, "image/jpeg", "z") == ""


# ---------- main() end to end ----------
def test_main_writes_map_with_fake_client(shop, monkeypatch):
    from aira_live.vision import gemini

    client = FakeClient('{"view": "the bat wall seen from its far end, a mirror at the end"}')
    gemini.set_client(client)
    try:
        rc = dsp.main(["--map", str(shop.map_path), "--photos", str(shop.photos)])
    finally:
        gemini.set_client(None)
    assert rc == 0
    assert {c["model"] for c in client.calls} == {"gemini-3.8-flash"}
    assert len(client.calls) == 3
    written = json.loads(shop.map_path.read_text(encoding="utf-8"))
    counter = _zone(written, "counter")
    assert counter["views"]["left"] == "the bat wall seen from its far end, a mirror at the end"
    assert counter["draft_views"] == ["left"]
    assert _zone(written, "entrance")["views"]["front"] == "human text about the counter"   # kept
    assert "draft_views" not in _zone(written, "entrance")
    assert _zone(written, "bat_wall")["views"]["front"] == "rows of bats standing in racks, handles up"  # kept
    assert written["draft"] is True
    loaded = shop_map.load_file(shop.map_path)
    assert loaded.draft is True
    assert loaded.zones["counter"].views["left"].startswith("the bat wall")
    text = shop.map_path.read_text(encoding="utf-8")
    assert text.endswith("}\n") and "\\u" not in text and '  "zones"' in text


def test_main_injected_describe_and_overwrite(shop):
    seen = []

    def describe(image_bytes, mime, zone_name):
        seen.append((mime, zone_name))
        return f"machine view of the {zone_name}"

    rc = dsp.main(["--map", str(shop.map_path), "--photos", str(shop.photos), "--overwrite"], describe=describe)
    assert rc == 0
    assert sorted(seen) == [("image/jpeg", "counter"), ("image/jpeg", "entrance"), ("image/png", "bat wall")]
    written = json.loads(shop.map_path.read_text(encoding="utf-8"))
    assert _zone(written, "entrance")["views"]["front"] == "machine view of the entrance"
    assert _zone(written, "entrance")["draft_views"] == ["front"]
    assert _zone(written, "bat_wall")["views"]["front"] == "machine view of the bat wall"
    assert _zone(written, "counter")["views"]["left"] == "machine view of the counter"
    assert written["draft"] is True
    shop_map.load_file(shop.map_path)


def test_main_dry_run_writes_nothing(shop, caplog):
    before = shop.map_path.read_text(encoding="utf-8")
    with caplog.at_level(logging.INFO):
        rc = dsp.main(["--map", str(shop.map_path), "--photos", str(shop.photos), "--dry-run"],
                      describe=lambda b, m, z: "would be written")
    assert rc == 0
    assert shop.map_path.read_text(encoding="utf-8") == before
    assert "dry run" in caplog.text and "would be written" in caplog.text


def test_main_zone_filter(shop):
    seen = []
    rc = dsp.main(["--map", str(shop.map_path), "--photos", str(shop.photos), "--zone", "counter"],
                  describe=lambda b, m, z: seen.append(z) or "counter draft")
    assert rc == 0 and seen == ["counter"]
    written = json.loads(shop.map_path.read_text(encoding="utf-8"))
    assert _zone(written, "counter")["views"]["left"] == "counter draft"
    assert dsp.main(["--map", str(shop.map_path), "--photos", str(shop.photos), "--zone", "nope"],
                    describe=lambda b, m, z: "x") == 1


def test_main_bad_model_reply_skips_view_and_never_writes_garbage(shop, caplog):
    from aira_live.vision import gemini

    before = shop.map_path.read_text(encoding="utf-8")
    gemini.set_client(FakeClient("Sorry, I cannot see an image here."))
    try:
        with caplog.at_level(logging.WARNING):
            rc = dsp.main(["--map", str(shop.map_path), "--photos", str(shop.photos)])
    finally:
        gemini.set_client(None)
    assert rc == 0
    assert shop.map_path.read_text(encoding="utf-8") == before
    assert "no usable view text" in caplog.text
    assert "counter/left" in caplog.text


def test_main_model_error_on_one_photo_keeps_the_others(shop, caplog):
    def describe(image_bytes, mime, zone_name):
        if zone_name == "counter":
            raise RuntimeError("API down")
        return "ok text"

    with caplog.at_level(logging.WARNING):
        rc = dsp.main(["--map", str(shop.map_path), "--photos", str(shop.photos), "--overwrite"], describe=describe)
    assert rc == 0
    assert "model call failed" in caplog.text
    written = json.loads(shop.map_path.read_text(encoding="utf-8"))
    assert _zone(written, "counter")["views"]["left"] == ""
    assert _zone(written, "entrance")["views"]["front"] == "ok text"


def test_main_no_photos_or_bad_map(tmp_path, shop):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert dsp.main(["--map", str(shop.map_path), "--photos", str(empty)], describe=lambda b, m, z: "x") == 2
    bad = tmp_path / "bad_map.json"
    bad.write_text('{"shop": {}}', encoding="utf-8")
    assert dsp.main(["--map", str(bad), "--photos", str(shop.photos)], describe=lambda b, m, z: "x") == 1
    assert dsp.main(["--map", str(tmp_path / "none.json"), "--photos", str(shop.photos)],
                    describe=lambda b, m, z: "x") == 1
