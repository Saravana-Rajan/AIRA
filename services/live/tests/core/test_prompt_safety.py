"""Shop-aware system prompt + the money/stock number safety check (saravana/01 Task 3).

`fake_db` (tests/conftest.py) puts an in-memory Firestore behind `aira_live.db.db()`. No network.
"""
import json
from pathlib import Path

from aira_live import safety
from aira_live.prompt import (
    build_system_prompt,
    catalog_facts,
    last_catalog_facts,
    render_prompt,
)

CATALOG = Path(__file__).resolve().parents[4] / "content" / "catalog" / "murugan_dairy.json"

CURD = {"sku": "sakthi_curd_1l", "name": "curd", "brand": "Sakthi", "unit": "pouch", "packSizeL": 1, "price": 60}


# --- prompt -----------------------------------------------------------------------------------------------
def test_render_prompt_has_shop_products_suppliers_language_and_rules():
    p = render_prompt({"name": "Murugan Dairy"}, [CURD], [{"name": "Sakthi vendor", "channel": "whatsapp"}], "ta", "short")
    assert "Murugan Dairy" in p and "sakthi_curd_1l" in p and "₹60" in p and "Sakthi vendor via whatsapp" in p
    assert "Tamil" in p and "Tamil-English" in p
    low = p.lower()
    assert "read it back" in low and "confirm_action" in p
    assert "never say a number" in low and "not sure" in low
    assert "count_needed" in p and '"check touch <label>"' in p
    assert "credit" not in low


async def test_build_system_prompt_reads_firestore(fake_db):
    shop = fake_db.collection("shops").document("murugan")
    await shop.set({"name": "Murugan Dairy"})
    await shop.collection("products").document("aavin_milk_500ml").set(
        {"name": "milk", "brand": "Aavin", "unit": "pouch", "packSizeL": 0.5, "price": 30})
    await shop.collection("suppliers").document("sup1").set({"name": "Selvam (milk vendor)", "channel": "whatsapp"})
    p = await build_system_prompt("murugan", "en", "short")
    assert "Murugan Dairy" in p and "aavin_milk_500ml" in p and "0.5 L pouch" in p and "Selvam (milk vendor)" in p
    assert "English" in p


def test_render_prompt_uses_catalog_pack_aliases_and_never_leaks_phone_numbers():
    milk = {"sku": "aavin_milk_500ml", "name": "Aavin Milk 500 ml", "brand": "Aavin", "pack": "500 ml pouch",
            "unit": "L", "packSizeL": 0.5, "price": 44,
            "aliases": {"ta": ["அரை லிட்டர் பால்"], "en": ["half litre milk"]}}
    vendor = {"id": "aavin_vendor", "name": "Aavin vendor", "channel": "whatsapp", "phone": "+919800000000",
              "products": ["aavin_milk_500ml"], "aliases": {"ta": ["பால்காரர்"], "en": ["milkman"]}}
    p = render_prompt({"name": "Murugan Dairy"}, [milk], [vendor], "ta", "detail")
    assert "Aavin Aavin" not in p and "500 ml pouch" in p and "₹44 per L" in p
    assert "அரை லிட்டர் பால்" in p and "பால்காரர்" in p and "half litre milk" not in p
    assert "Aavin vendor via whatsapp" in p and "aavin_milk_500ml" in p
    assert "9800000000" not in p and "+91" not in p
    assert "Hindi" not in p


def test_render_prompt_hindi_and_missing_price():
    p = render_prompt({"name": "Murugan Dairy"}, [{"sku": "x_box", "name": "ice cream", "unit": "box"}], [], "hi", "short")
    assert "Hindi" in p and "Hindi-English" in p and "₹None" not in p and "x_box" in p


def test_render_prompt_with_the_real_catalog_lists_every_sku_and_no_phone_details():
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    p = render_prompt(cat["shop"], cat["products"], cat["suppliers"], "ta", "short")
    for prod in cat["products"]:
        assert f"- {prod['sku']}:" in p
    for sup in cat["suppliers"]:
        assert f"- {sup['name']} via {sup['channel']}" in p
    assert "phoneEnv" not in p and "SUPPLIER_" not in p and "+91" not in p
    assert "udhaar" not in p.lower() and "cash or upi only" in p.lower()


def test_prompt_prices_are_allowed_numbers_via_catalog_facts():
    facts = catalog_facts([CURD, {"sku": "x_box", "name": "ice cream", "unit": "box"}])
    assert safety.check_numbers("Sakthi curd is ₹60 per litre.", [facts]).ok
    assert safety.check_numbers("Sakthi curd is ₹600 per litre.", [facts]).unexpected == [600]
    assert safety.check_numbers("Sakthi curd is ₹60.", []).unexpected == [60]  # facts must be passed in
    # sku ids are keys, so the digits inside them ("1l", "500ml") never become allowed numbers
    milk = {"sku": "aavin_milk_500ml", "name": "milk", "unit": "L", "packSizeL": 0.5, "price": 44}
    assert safety.check_numbers("500 litres left.", [catalog_facts([milk])]).unexpected == [500]


async def test_build_system_prompt_remembers_the_catalog_facts_it_rendered(fake_db):
    assert last_catalog_facts("nowhere") == {}
    shop = fake_db.collection("shops").document("murugan_dairy")
    await shop.set({"name": "Murugan Dairy"})
    await shop.collection("products").document("aavin_milk_500ml").set(
        {"name": "milk", "brand": "Aavin", "unit": "L", "packSizeL": 0.5, "price": 44})
    await build_system_prompt("murugan_dairy", "en", "short")
    facts = last_catalog_facts("murugan_dairy")
    assert facts == {"catalog": {"aavin_milk_500ml": {"price": 44, "packSizeL": 0.5}}}
    assert safety.check_numbers("Aavin milk is ₹44 per litre, 0.5 litre pouch.", [facts]).ok


# --- deterministic number check ---------------------------------------------------------------------------
def test_flags_money_number_not_in_tool_results():
    v = safety.check_numbers("Total is ₹600.", [{"total": 60, "say": "Total ₹60."}])
    assert v.ok is False and v.unexpected == [600] and v.needs_llm is False


def test_accepts_numbers_from_results_including_say_text_and_quantities():
    results = [{"items": [{"sku": "sakthi_curd_1l", "qty": 2}], "total": 120, "say": "2 litres curd, ₹120."}]
    assert safety.check_numbers("2 litres curd, total 120 rupees.", results).ok


def test_ignores_numbers_without_money_or_stock_context():
    assert safety.check_numbers("Step 3 of 7. Hold it closer.", [{"say": "ok"}]).ok


def test_unit_letters_inside_words_are_not_units():
    # "until"/"total" end in "l", "hours" ends in "rs", "packed" starts with "pack": none of these is a unit.
    v = safety.check_numbers("Open until 9. Your total 7 items are packed. Back in 2 hours 5 minutes.", [{"say": "ok"}])
    assert v.ok and v.unexpected == []


def test_tamil_and_devanagari_digits_are_normalised():
    assert safety.check_numbers("மொத்தம் ₹௬௦", [{"total": 60}]).ok
    assert not safety.check_numbers("कुल ₹६००", [{"total": 60}]).ok


def test_english_number_words_are_parsed():
    assert safety.check_numbers("Total sixty rupees.", [{"total": 60}]).ok
    assert safety.check_numbers("Total six hundred rupees.", [{"total": 60}]).unexpected == [600]


def test_indian_grouping_and_decimals():
    assert safety.check_numbers("That is ₹1,20,000.", [{"total": 120000}]).ok
    assert safety.check_numbers("Half: 0.5 litre.", [{"packSizeL": 0.5}]).ok
    assert safety.check_numbers("2.5 litres left.", [{"onHand": 2}]).unexpected == [2.5]


def test_tamil_number_words_need_the_llm():
    v = safety.check_numbers("மொத்தம் அறுநூறு ரூபாய்", [{"total": 60}])
    assert v.ok is True and v.needs_llm is True


def test_digits_plus_unmatched_tamil_number_words_still_need_the_llm():
    v = safety.check_numbers("2 லிட்டர் தயிர், மொத்தம் நூற்றிருபது ரூபாய்", [{"qty": 2, "total": 120}])
    assert v.ok is True and v.needs_llm is True


# --- LLM verifier ------------------------------------------------------------------------------------------
async def test_llm_verify_uses_verify_role(monkeypatch):
    seen = {}

    async def fake_generate_json(role, parts, schema_hint, thinking="low", timeout_s=6.0):
        seen["role"] = role
        return {"consistent": False, "wrong_numbers": [600]}

    monkeypatch.setattr(safety, "generate_json", fake_generate_json)
    v = await safety.llm_verify("மொத்தம் அறுநூறு ரூபாய்", [{"total": 60}])
    assert seen["role"] == "verify" and v.ok is False and v.unexpected == [600]


async def test_llm_verify_failure_is_treated_as_ok(monkeypatch):
    async def boom(*a, **k):
        raise TimeoutError

    monkeypatch.setattr(safety, "generate_json", boom)
    assert (await safety.llm_verify("அறுநூறு ரூபாய்", [{"total": 60}])).ok


async def test_llm_verify_ignores_garbage_and_numbers_that_are_in_the_results(monkeypatch):
    async def says_60_is_wrong(*a, **k):
        return {"consistent": False, "wrong_numbers": ["60", "abc", None]}

    monkeypatch.setattr(safety, "generate_json", says_60_is_wrong)
    assert (await safety.llm_verify("அறுபது ரூபாய்", [{"total": 60}])).ok

    async def not_a_dict(*a, **k):
        return ["nope"]

    monkeypatch.setattr(safety, "generate_json", not_a_dict)
    assert (await safety.llm_verify("அறுபது ரூபாய்", [{"total": 60}])).ok


def test_correction_text_names_the_wrong_number():
    assert "600" in safety.correction_text([600], "en")
    assert "600" in safety.correction_text([600], "ta")
    assert safety.correction_text([], "en") != "safety.correction"
