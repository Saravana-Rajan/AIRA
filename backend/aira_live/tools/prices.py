"""The owner's price board, as facts. OWNER: Kanish.

Read-only. "How much is curd?" → ``{"product": "Sakthi Curd 1 L", "price": 60, ...}``. "What are the prices?" →
the active products grouped by category (milk, curd, buttermilk, ghee, paneer, butter, ice cream), a page at a
time, with ``more`` True when "say more" has a next page. Every number comes from the product docs in the Store
(Firestore is the source of truth): a product with no price is said to have none, never guessed. Gemini Live
phrases the facts in the owner's language; ``say_en`` is only the English meaning hint. Nothing here is shown to
customers; the owner hears it.
"""
from aira_live.business import catalog, hints
from aira_live.session import get_session
from aira_live.tools.orders import product_doc

CATEGORIES = ("milk", "curd", "buttermilk", "ghee", "paneer", "butter", "ice cream")
OTHER = "other"
PAGE_LIMIT = 8       # products per page; "say more" reads the next page
# Matched in this order against the name and sku, so "buttermilk" is neither butter nor milk and
# "ice cream" / "icecream" is ice cream.
_MATCH: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("buttermilk", ("buttermilk", "butter milk", "மோர்", "छाछ")),
    ("ice cream", ("ice cream", "icecream", "ice-cream", "ஐஸ்கிரீம்", "आइसक्रीम")),
    ("butter", ("butter", "வெண்ணெய்", "मक्खन")),
    ("paneer", ("paneer", "பன்னீர்", "पनीर")),
    ("ghee", ("ghee", "நெய்", "घी")),
    ("curd", ("curd", "தயிர்", "दही")),
    ("milk", ("milk", "பால்", "दूध")),
)


def category_of(doc: dict) -> str:
    """The board group of a product, from its name (and sku); ``"other"`` when none fits."""
    text = f"{doc.get('name', '')} {doc.get('sku', '')}".lower().replace("_", " ")
    for cat, words in _MATCH:
        if any(w in text for w in words):
            return cat
    return OTHER


def _price(doc: dict) -> int | float | None:
    p = doc.get("price")
    if isinstance(p, bool) or not isinstance(p, (int, float)) or p <= 0:
        return None
    return p


def _size(doc: dict) -> float:
    return float(doc.get("packSizeL") or 0) or float(doc.get("packSizeG") or 0) / 1000


async def price_query(product: str | None = None, page: int = 0) -> dict:
    """The selling price, e.g. "how much is curd?". Without a product: today's price list by group (milk, curd,
    buttermilk, ghee, paneer, butter, ice cream), a few at a time; "say more" → page 1, then 2."""
    s = get_session()
    if product:
        ref = await catalog.resolve(s.shop_id, product, s.lang)
        if ref is None:
            return hints.fact("PRODUCT_UNKNOWN", product=product)
        doc = await product_doc(s.shop_id, ref.sku)
        name = doc.get("name", ref.sku)
        price = _price(doc)
        if price is None:
            return {"ok": False, "error": "price_unset", "sku": ref.sku, "product": name, "price": None,
                    "say_en": f"{name} has no price yet. Say the price to set it."}
        return {"ok": True, "sku": ref.sku, "product": name, "price": price, "unit": doc.get("unit", ""),
                "category": category_of(doc), "say_en": f"{name} is {price} rupees."}

    order = {c: i for i, c in enumerate((*CATEGORIES, OTHER))}
    rows = []
    for sku, doc in await catalog.active_products(s.shop_id):
        price = _price(doc)
        if price is None:
            continue                                      # never invent: an unpriced product is not on the board
        cat = category_of({**doc, "sku": sku})
        rows.append((order[cat], _size(doc), doc.get("name", sku), sku, price, cat))
    rows.sort()
    items = [{"sku": sku, "product": name, "price": price, "category": cat} for _, _, name, sku, price, cat in rows]
    if not items:
        return {"ok": True, "items": [], "prices": [], "groups": [], "page": 0, "more": False,
                "say_en": "No prices yet. Add a product first."}
    start = max(int(page or 0), 0) * PAGE_LIMIT
    prices = items[start:start + PAGE_LIMIT]
    more = start + PAGE_LIMIT < len(items)
    if not prices:
        return {"ok": True, "items": items, "prices": [], "groups": [], "page": page, "more": False,
                "say_en": "That is all the prices."}
    groups: list[dict] = []
    for it in prices:
        if groups and groups[-1]["category"] == it["category"]:
            groups[-1]["prices"].append(it)
        else:
            groups.append({"category": it["category"], "prices": [it]})
    said = [f"{g['category'].capitalize()}: {', '.join(f'{p['product']} {p['price']}' for p in g['prices'])}."
            for g in groups]
    if more:
        said.append("Say more for the rest.")
    return {"ok": True, "items": items, "prices": prices, "groups": groups, "page": page, "more": more,
            "say_en": " ".join(said)}
