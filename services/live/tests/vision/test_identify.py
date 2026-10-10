import json
from pathlib import Path

import pytest

from aira_live.models import fallback_for, model_for
from aira_live.vision.gemini import VisionError
from aira_live.vision.identify import (ProductRef, confusion_partners, decide, identify_product, next_hint)

CATALOG = Path(__file__).resolve().parents[4] / "content" / "catalog" / "murugan_dairy.json"

CANDS = [
    ProductRef(sku="aavin_milk_500ml", name="Aavin milk", brand="Aavin", pack="500 ml pouch"),
    ProductRef(sku="aavin_milk_1l", name="Aavin milk", brand="Aavin", pack="1 L pouch"),
    ProductRef(sku="sakthi_curd_500ml", name="Sakthi curd", brand="Sakthi", pack="500 ml pouch"),
    ProductRef(sku="sakthi_curd_1l", name="Sakthi curd", brand="Sakthi", pack="1 L pouch"),
    ProductRef(sku="arun_icecream_box", name="Arun ice cream", brand="Arun", pack="family box"),
]

# Full dairy range (plan addendum 2026-10-10).
FULL = [
    *CANDS[:4],
    ProductRef(sku="aavin_buttermilk_200ml", name="Aavin buttermilk", brand="Aavin", pack="200 ml pouch"),
    ProductRef(sku="aavin_ghee_200ml", name="Aavin ghee", brand="Aavin", pack="200 ml jar"),
    ProductRef(sku="aavin_ghee_500ml", name="Aavin ghee", brand="Aavin", pack="500 ml jar"),
    ProductRef(sku="aavin_paneer_200g", name="Aavin paneer", brand="Aavin", pack="200 g pack"),
    ProductRef(sku="aavin_butter_100g", name="Aavin butter", brand="Aavin", pack="100 g pack"),
    CANDS[4],
]


def test_confusion_partners_are_same_brand_other_pack():
    assert confusion_partners("sakthi_curd_1l", CANDS) == ["sakthi_curd_500ml"]
    assert confusion_partners("arun_icecream_box", CANDS) == []


def test_confusion_partners_full_range_pairs():
    assert confusion_partners("aavin_milk_500ml", FULL) == ["aavin_milk_1l", "aavin_buttermilk_200ml"]
    assert confusion_partners("aavin_buttermilk_200ml", FULL) == ["aavin_milk_500ml"]
    assert confusion_partners("aavin_ghee_500ml", FULL) == ["aavin_ghee_200ml"]
    assert confusion_partners("aavin_paneer_200g", FULL) == ["aavin_butter_100g"]
    assert confusion_partners("aavin_butter_100g", FULL) == ["aavin_paneer_200g"]
    assert confusion_partners("aavin_milk_1l", FULL) == ["aavin_milk_500ml"]
    assert confusion_partners("arun_icecream_box", FULL) == []
    assert confusion_partners("not_a_sku", FULL) == []


def test_confident_label_visible_is_accepted():
    r = decide({"sku": "sakthi_curd_1l", "confidence": 0.93, "alternatives": [], "label_visible": True}, CANDS)
    assert r.sku == "sakthi_curd_1l" and r.confidence == 0.93


def test_unknown_sku_is_rejected():
    r = decide({"sku": "amul_butter", "confidence": 0.99}, CANDS)
    assert r.sku is None and r.confidence == 0.0


def test_low_confidence_abstains():
    r = decide({"sku": "arun_icecream_box", "confidence": 0.4}, CANDS)
    assert r.sku is None and r.alternatives[0] == "arun_icecream_box"
    assert next_hint(r, CANDS) == "vision.aim.hold_closer"


def test_confusion_pair_needs_label():
    r = decide({"sku": "aavin_milk_1l", "confidence": 0.72, "alternatives": ["aavin_milk_500ml"]}, CANDS)
    assert r.sku is None and r.alternatives[:2] == ["aavin_milk_1l", "aavin_milk_500ml"]
    assert next_hint(r, CANDS) == "vision.aim.show_label"


def test_label_not_visible_blocks_even_high_confidence():
    r = decide({"sku": "sakthi_curd_500ml", "confidence": 0.95, "label_visible": False}, CANDS)
    assert r.sku is None
    assert next_hint(r, CANDS) == "vision.aim.show_label"


def test_no_partner_accepts_at_moderate_confidence():
    r = decide({"sku": "arun_icecream_box", "confidence": 0.7, "label_visible": False}, CANDS)
    assert r.sku == "arun_icecream_box"
    assert next_hint(r, CANDS) == ""


def test_full_range_confusion_pairs_need_label():
    for sku in ("aavin_buttermilk_200ml", "aavin_ghee_200ml", "aavin_paneer_200g", "aavin_butter_100g"):
        r = decide({"sku": sku, "confidence": 0.97, "label_visible": False}, FULL)
        assert r.sku is None, sku
        assert next_hint(r, FULL) == "vision.aim.show_label", sku
    r = decide({"sku": "aavin_paneer_200g", "confidence": 0.9, "label_visible": True}, FULL)
    assert r.sku == "aavin_paneer_200g"


def test_missing_label_visible_is_treated_as_not_visible_for_pairs():
    r = decide({"sku": "aavin_milk_1l", "confidence": 0.95}, CANDS)
    assert r.sku is None and next_hint(r, CANDS) == "vision.aim.show_label"


def test_malformed_model_fields_never_crash_or_invent():
    r = decide({"sku": ["aavin_milk_1l"], "confidence": "high", "alternatives": "aavin_milk_1l"}, CANDS)
    assert r.sku is None and r.confidence == 0.0 and r.alternatives == []
    r = decide({"sku": "Sakthi_Curd_1L ", "confidence": "0.9", "label_visible": "true"}, CANDS)
    assert r.sku == "sakthi_curd_1l" and r.confidence == 0.9
    r = decide({"sku": "sakthi_curd_1l", "confidence": 0.95, "label_visible": "false"}, CANDS)
    assert r.sku is None
    r = decide({"sku": "arun_icecream_box", "confidence": float("nan")}, CANDS)
    assert r.sku is None and r.confidence == 0.0
    r = decide({"sku": None, "confidence": 0.9, "alternatives": ["amul_butter", "arun_icecream_box"]}, CANDS)
    assert r.sku is None and r.alternatives == ["arun_icecream_box"]
    assert next_hint(r, CANDS) == "vision.aim.hold_closer"


def test_list_reply_uses_first_item():
    r = decide({"items": [{"sku": "arun_icecream_box", "confidence": 0.9, "label_visible": True}]}, CANDS)
    assert r.sku == "arun_icecream_box"
    assert decide({"items": []}, CANDS).sku is None


async def test_identify_product_uses_point_role_and_lists_candidates(fake_genai, frame):
    client = fake_genai('{"sku": "aavin_milk_500ml", "confidence": 0.9, "alternatives": [], "label_visible": true}')
    r = await identify_product(frame, CANDS)
    assert r.sku == "aavin_milk_500ml"
    call = client.aio.models.calls[0]
    assert call["model"] == model_for("point")
    assert "sakthi_curd_1l" in " ".join(str(p) for p in call["contents"])


async def test_identify_product_without_candidates_makes_no_call(fake_genai, frame):
    client = fake_genai()
    r = await identify_product(frame, [])
    assert r.sku is None and client.aio.models.calls == []


def _catalog_refs() -> list[ProductRef]:
    if not CATALOG.exists():
        pytest.skip("content/catalog/murugan_dairy.json not present")
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    return [ProductRef(sku=p["sku"], name=p["name"], brand=p["brand"], pack=p["pack"]) for p in data["products"]]


def test_real_catalog_confusion_pairs():
    """Catalog names carry the size ("Aavin Milk 500 ml"), so size variants must be found from the SKU family."""
    cat = _catalog_refs()
    assert {c.sku for c in cat} >= {
        "aavin_milk_500ml", "aavin_milk_1l", "sakthi_curd_500ml", "sakthi_curd_1l", "aavin_buttermilk_200ml",
        "aavin_ghee_200ml", "aavin_ghee_500ml", "aavin_paneer_200g", "aavin_butter_100g", "arun_icecream_box"}
    assert set(confusion_partners("aavin_milk_500ml", cat)) == {"aavin_milk_1l", "aavin_buttermilk_200ml"}
    assert confusion_partners("sakthi_curd_1l", cat) == ["sakthi_curd_500ml"]
    assert confusion_partners("aavin_ghee_200ml", cat) == ["aavin_ghee_500ml"]
    assert confusion_partners("aavin_paneer_200g", cat) == ["aavin_butter_100g"]
    assert confusion_partners("arun_icecream_box", cat) == []
    # Ghee is not a look-alike of milk just because both are Aavin.
    assert "aavin_ghee_200ml" not in confusion_partners("aavin_milk_500ml", cat)


def test_non_dict_reply_is_no_product():
    for bad in (None, [], "aavin_milk_1l", 42, {"items": ["aavin_milk_1l"]}):
        r = decide(bad, CANDS)  # type: ignore[arg-type]
        assert r.sku is None and r.confidence == 0.0 and r.alternatives == []


def test_model_tag_in_reply_is_ignored():
    r = decide({"sku": "arun_icecream_box", "confidence": 0.9, "_model": "gemini-3.8-flash"}, CANDS)
    assert r.sku == "arun_icecream_box"


async def test_identify_product_list_reply_end_to_end(fake_genai, frame):
    fake_genai('[{"sku": "sakthi_curd_1l", "confidence": 0.91, "alternatives": [], "label_visible": true}]')
    r = await identify_product(frame, CANDS)
    assert r.sku == "sakthi_curd_1l"


async def test_identify_product_falls_back_to_flash(fake_genai, frame):
    client = fake_genai(RuntimeError("er2 down"), RuntimeError("er2 down"),
                        '{"sku": "arun_icecream_box", "confidence": 0.8, "label_visible": true}')
    r = await identify_product(frame, CANDS)
    assert r.sku == "arun_icecream_box"
    assert [c["model"] for c in client.aio.models.calls] == [model_for("point"), model_for("point"),
                                                              fallback_for("point")]


async def test_identify_product_raises_vision_error_when_all_fail(fake_genai, frame):
    fake_genai(RuntimeError("a"), RuntimeError("b"), RuntimeError("c"))
    with pytest.raises(VisionError):
        await identify_product(frame, CANDS)
