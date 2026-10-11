"""System prompt for the Live voice agent: shop context + language rule + safety rules.

There is no default and no fixed language. Live answers in the language of the speaker's last utterance and
switches when he switches; the hello language only picks the words of the very first line.
"""
from __future__ import annotations

from aira_live.db import db

# Names for the first-line hint (hello.lang, usually the phone's locale). Anything else reads as English.
LANG_NAME = {
    "ta": "Tamil", "hi": "Hindi", "en": "English", "te": "Telugu", "kn": "Kannada", "ml": "Malayalam",
    "bn": "Bengali", "gu": "Gujarati", "pa": "Punjabi", "or": "Odia", "mr": "Marathi", "si": "Sinhala",
    "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian",
    "ms": "Malay", "fil": "Filipino", "tl": "Filipino", "my": "Burmese", "km": "Khmer", "lo": "Lao",
}


def language_rule(lang: str) -> str:
    """The one language rule: follow the speaker. ``lang`` is only the hint for the first line."""
    name = LANG_NAME.get(lang, "English")
    mix = f"{name}-English" if name != "English" else "Tamil-English"
    # Gemini's transcript sometimes writes an Indian-language word in Latin letters ("Amo" for ஆமா).
    latin = (f"{name} words that the transcript shows in Latin letters are still {name}, not English."
             if name != "English" else
             "Indian-language words that the transcript shows in Latin letters (amo, aama, illa, venum, paal, "
             "haan, nahi) are still that language, not English.")
    return (
        "LANGUAGE: Always reply in the language the speaker used in their last utterance. Keep the current "
        "language unless the speaker clearly speaks a full sentence in another language; a short reply, a "
        f"number, 'ok' or a product name never changes it. {latin} "
        f"Never switch language mid-reply. Mixed {mix} is fine and natural; so is any language mixed with "
        f"English. Never ask which language. Until the speaker has spoken, use {name}. "
        "When a tool's 'say' text, a notice or a correction is written in another language, say it in the "
        "speaker's language."
    )


def _num(x) -> str:
    return f"{x:g}" if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x)


def _pack(p: dict) -> str:
    if p.get("pack"):
        return str(p["pack"])
    unit = p.get("unit", "pack")
    if p.get("packSizeL"):
        return f"{_num(p['packSizeL'])} L" if unit == "L" else f"{_num(p['packSizeL'])} L {unit}"
    if p.get("packSizeG"):
        return f"{_num(p['packSizeG'])} g {unit}"
    return unit


def _label(p: dict) -> str:
    brand, name = str(p.get("brand", "")).strip(), str(p.get("name", "")).strip()
    if brand and not name.lower().startswith(brand.lower()):
        return f"{brand} {name}".strip()
    return name or brand or p.get("sku", "")


def _price(p: dict) -> str:
    price = p.get("price")
    if price is None or isinstance(price, bool):
        return "price not set"
    return f"₹{_num(price)} per {p.get('unit', 'pack')}"


def _aliases(doc: dict, lang: str) -> str:
    names = [a for a in (doc.get("aliases") or {}).get(lang, []) if a]
    return f"; also called: {', '.join(names)}" if names else ""


def _product_line(p: dict, lang: str) -> str:
    return f"- {p.get('sku', '')}: {_label(p)}, {_pack(p)}, {_price(p)}{_aliases(p, lang)}"


def _supplier_line(s: dict, lang: str) -> str:
    # Never put phone numbers into the prompt: the model must not speak or invent them.
    line = f"- {s.get('name', s.get('id', 'supplier'))} via {s.get('channel', 'whatsapp')}"
    if s.get("products"):
        line += f"; supplies: {', '.join(str(x) for x in s['products'])}"
    return line + _aliases(s, lang)


RULES = (
    (
        "1. Before any order, payment or sale is committed, read it back and wait for the owner's yes. "
        "When a tool returns needs_confirm, ask exactly its 'say' question, then call confirm_action with the "
        "confirm_id and the owner's answer."
    ),
    (
        "2. Never say a number (price, quantity, stock, money, change) that did not come from a tool result in "
        "this conversation or from the PRODUCTS list below. If you need any other number, call a tool."
    ),
    (
        "3. If a tool result is unsure, has needs_retake or low confidence, say you are not sure and give its aim "
        "hint. Never guess counts, amounts or products."
    ),
    "4. Customers may also speak. Only the owner can confirm actions.",
    "5. Never describe or identify people. Never say a currency note is real or fake.",
    (
        "6. Messages starting with [NOTICE FOR THE SHOPKEEPER] or [CONFIRMATION] come from the system: relay or "
        "acknowledge them briefly. A message starting with \"Correction needed:\" comes from the safety checker: "
        "correct yourself in one short sentence."
    ),
    (
        "7. For a customer sale, first call product_find with count_needed set to how many packs to pick, then "
        "sale_start."
    ),
    (
        "8. Payments are cash or UPI only. A payment counts only after a cash count, the soundbox announcement or "
        "the owner's yes."
    ),
    (
        "9. Evidence before state: an order is not a delivery, an invoice is not proof of receipt, and a saved "
        "location is not proof the product is still there. Check with a tool before you say it is done."
    ),
    (
        "10. For ANY question about what is around the owner, what is in front of the camera, reading text or "
        "finding things, call camera_ask (or product_find to guide the hand to a product). Never answer from "
        "imagination; you cannot see without these tools. Speak the tool's 'say' as guidance: side, distance, "
        "next step."
    ),
    (
        "11. The owner can change the shop by voice: product_add (a new product; ask the price and unit if "
        "missing), product_update (price, name or vendor), product_disable, stock_add (stock that came without "
        "an order) and supplier_add (a new vendor; for Telegram, read out the link). \"Add buttermilk\" for a "
        "product the shop already has: ask whether to add stock or a new product. Each of these waits for the "
        "owner's yes."
    ),
)


def render_prompt(shop: dict, products: list[dict], suppliers: list[dict], lang: str, verbosity: str) -> str:
    length = "One short sentence unless the owner asks for more." if verbosity == "short" else "At most three short sentences."
    intro = (
        f"You are AIRA, the voice assistant of {shop.get('name', 'the shop')}, a small dairy shop run by a blind "
        "owner. You help him run the shop alone: orders, deliveries, invoices, payments, sales and stock."
    )
    lines = [
        intro,
        f"{language_rule(lang)} {length}",
        "RULES:",
        *RULES,
        "PRODUCTS (sku: name, pack, price):",
        *[_product_line(p, lang) for p in products],
        "SUPPLIERS:",
        *[_supplier_line(s, lang) for s in suppliers],
    ]
    return "\n".join(lines)


def catalog_facts(products: list[dict]) -> dict:
    """The numbers the PRODUCTS list lets the model repeat, shaped like one more tool result.

    Pass it to ``safety.check_numbers`` / ``safety.llm_verify`` next to the turn's tool results, so a price
    read straight from the prompt ("curd is ₹60 a litre") is not flagged as invented.
    """
    keys = ("price", "packSizeL", "packSizeG")
    # Keyed by sku: the checker reads values only, so digits inside sku ids ("500ml") never become allowed.
    return {"catalog": {str(p.get("sku", "")): {k: p[k] for k in keys if p.get(k) is not None} for p in products}}


_LAST_FACTS: dict[str, dict] = {}


def last_catalog_facts(shop_id: str) -> dict:
    """``catalog_facts`` of the products in the last prompt built for ``shop_id`` ({} before the first build)."""
    return _LAST_FACTS.get(shop_id, {})


async def build_system_prompt(shop_id: str, lang: str, verbosity: str) -> str:
    shop_ref = db().collection("shops").document(shop_id)
    snap = await shop_ref.get()
    shop = (snap.to_dict() or {}) if snap.exists else {"name": shop_id}
    products = [(d.to_dict() or {}) | {"sku": d.id} async for d in shop_ref.collection("products").stream()
                if (d.to_dict() or {}).get("active") is not False]          # disabled by voice: not for sale
    suppliers = [(d.to_dict() or {}) | {"id": d.id} async for d in shop_ref.collection("suppliers").stream()
                 if (d.to_dict() or {}).get("active") is not False]
    _LAST_FACTS[shop_id] = catalog_facts(products)
    return render_prompt(shop, products, suppliers, lang, verbosity)
