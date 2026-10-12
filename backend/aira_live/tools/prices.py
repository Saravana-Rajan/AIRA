"""The owner's price board, spoken. OWNER: Kanish.

Read-only. "How much is curd?" → "Sakthi Curd 1 L is 60 rupees." "What are the prices?" → the active products
grouped by category (milk, curd, buttermilk, ghee, paneer, butter, ice cream), a page at a time, with "say more"
for the rest. Every number comes from the product docs in the Store (Firestore is the source of truth): a product
with no price is said to have none, never guessed. Nothing here is shown to customers; the owner hears it.
"""
from aira_live.business import catalog, hints
from aira_live.i18n import t
from aira_live.session import get_session
from aira_live.tools.orders import product_doc

CATEGORIES = ("milk", "curd", "buttermilk", "ghee", "paneer", "butter", "ice cream")
OTHER = "other"
PAGE_LIMIT = 8       # products spoken per turn; "say more" reads the next page
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


def _cat_label(cat: str, lang: str) -> str:
    return t("k3.cat." + cat.replace(" ", "_"), lang)


async def price_query(product: str | None = None, page: int = 0) -> dict:
    """The selling price, e.g. "how much is curd?". Without a product: today's price list by group (milk, curd,
    buttermilk, ghee, paneer, butter, ice cream), a few at a time; "say more" → page 1, then 2."""
    s = get_session()
    if product:
        ref = await catalog.resolve(s.shop_id, product, s.lang)
        if ref is None:
            return {"ok": False, "say": hints.say("PRODUCT_UNKNOWN", s.lang, product=product)}
        doc = await product_doc(s.shop_id, ref.sku)
        name = doc.get("name", ref.sku)
        price = _price(doc)
        if price is None:
            return {"ok": False, "sku": ref.sku, "price": None, "say": t("k3.price.unset", s.lang, product=name)}
        return {"ok": True, "sku": ref.sku, "price": price, "category": category_of(doc),
                "say": t("k3.price.one", s.lang, product=name, price=price)}

    order = {c: i for i, c in enumerate((*CATEGORIES, OTHER))}
    rows = []
    for sku, doc in await catalog.active_products(s.shop_id):
        price = _price(doc)
        if price is None:
            continue                                      # never invent: an unpriced product is not on the board
        cat = category_of({**doc, "sku": sku})
        rows.append((order[cat], _size(doc), doc.get("name", sku), sku, price, cat))
    rows.sort()
    items = [{"sku": sku, "name": name, "price": price, "category": cat} for _, _, name, sku, price, cat in rows]
    if not items:
        return {"ok": True, "items": [], "spoken": [], "page": 0, "has_more": False, "say": t("k3.price.none", s.lang)}
    start = max(int(page or 0), 0) * PAGE_LIMIT
    spoken = items[start:start + PAGE_LIMIT]
    has_more = start + PAGE_LIMIT < len(items)
    if not spoken:
        return {"ok": True, "items": items, "spoken": [], "page": page, "has_more": False, "say": t("k3.price.end", s.lang)}
    groups: list[tuple[str, list[str]]] = []
    for it in spoken:
        line = t("k3.price.item", s.lang, product=it["name"], price=it["price"])
        if groups and groups[-1][0] == it["category"]:
            groups[-1][1].append(line)
        else:
            groups.append((it["category"], [line]))
    said = [t("k3.price.group", s.lang, category=_cat_label(cat, s.lang), list=", ".join(lines)) for cat, lines in groups]
    if has_more:
        said.append(t("k3.price.more", s.lang))
    return {"ok": True, "items": items, "spoken": spoken, "page": page, "has_more": has_more, "say": " ".join(said)}
