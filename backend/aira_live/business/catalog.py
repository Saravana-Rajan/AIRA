"""Spoken product / supplier names (ta/hi/en, ASR-noisy) → refs, and spoken quantities → selling units.

The model proposes a name; this module decides what it means. resolve() and resolve_supplier() return None
when they are not sure: an unknown name, two different products in one name, or size variants with no
default pack and no size hint. The tool then asks, with
hints.say("PRODUCT_AMBIGUOUS" | "PRODUCT_UNKNOWN" | "SUPPLIER_UNKNOWN", lang).

Every language's aliases count, whatever `lang` is, because owners mix Tamil, Hindi and English.
Sizes ("half litre", "ஒரு லிட்டர்", "500 ml", "दो सौ एमएल") only choose between size variants of one product.

Vector fallback: when the exact / alias match finds nothing, "the blue packet curd" or "the round tub" is
embedded (aira_live.embeddings) and matched with Firestore ``find_nearest`` (COSINE, limit 3) over the shop's
products. A clear nearest neighbour resolves; a close call is a suggestion, which ``did_you_mean`` turns into
"Did you mean Sakthi Curd 500 ml?". No key, no index or any error: the result is exactly the old one (None).
"""
from __future__ import annotations

import logging
import math
import re
from difflib import SequenceMatcher
from fractions import Fraction
from functools import lru_cache

from aira_live import embeddings
from aira_live.business import hints, paths
from aira_live.business.numwords import word_amounts
from aira_live.business.store import get_store
from aira_live.business.text import tokens
from aira_live.i18n import t
from aira_live.vision.identify import ProductRef

log = logging.getLogger(__name__)

FUZZY_RATIO = 0.8           # ASR spellings: "shakti" ≈ "sakthi", "gee" ≈ "ghee"
_FUZZY_MIN_LEN = 4          # shorter alias words must match exactly ("nei", "का")
VECTOR_LIMIT = 3
VECTOR_THRESHOLD = 0.55     # cosine distance; farther than this is "I don't know that product"
VECTOR_SURE = 0.35          # nearer than this, and clearly ahead of the runner-up, resolves without asking
VECTOR_GAP = 0.08           # the runner-up must be at least this much farther, else ask
_SUGGEST_CACHE = 64

# Unit words in en / ta / hi, keyed by the canonical unit to_selling_units understands.
_UNITS: dict[str, frozenset[str]] = {
    "L": frozenset({"l", "ltr", "ltrs", "litre", "litres", "liter", "liters",
                    "லிட்டர்", "லிட்டர்கள்", "லி", "लीटर", "लिटर", "ली"}),
    "ml": frozenset({"ml", "mls", "millilitre", "millilitres", "milliliter", "milliliters",
                     "மில்லி", "மிலி", "எம்எல்", "மில்லிலிட்டர்", "एमएल", "मिली", "मिलीलीटर"}),
    "kg": frozenset({"kg", "kgs", "kilo", "kilos", "kilogram", "kilograms", "கிலோ", "किलो", "किलोग्राम"}),
    "g": frozenset({"g", "gm", "gms", "gram", "grams", "கிராம்", "ग्राम"}),
}
# Words that count physical packs of any product.
_COUNT_WORDS = frozenset({"", "pack", "packs", "packet", "packets", "pouch", "pouches", "piece", "pieces", "pc",
                          "pcs", "unit", "units", "nos", "பாக்கெட்", "பாக்கெட்டு", "பாக்கெட்டுகள்", "பை",
                          "पैकेट", "पैकेट्स", "पाउच", "थैली", "पीस", "नग"})
# Container words count packs only of a product sold in that container.
_CONTAINERS: dict[str, frozenset[str]] = {
    "jar": frozenset({"jar", "jars", "ஜார்", "ஜாடி", "जार"}),
    "box": frozenset({"box", "boxes", "பாக்ஸ்", "பெட்டி", "டப்பா", "बॉक्स", "डिब्बा", "डिब्बे"}),
}
_FILLER = frozenset().union(*_UNITS.values(), _COUNT_WORDS, *_CONTAINERS.values()) - {""}
_HALF = frozenset({"half", "அரை", "आधा", "आधे", "आधी"})
_ONE = frozenset({"ஒரு"})                    # Tamil "a / one": a size only when a unit follows ("ஒரு லிட்டர்")
_NUM_UNIT = re.compile(r"(\d+)(ml|l|ltr|g|gm|gms|kg)")


# ---------- tokens ----------

@lru_cache(maxsize=8192)
def _is_size_word(tok: str) -> bool:
    return (tok.isdigit() or _NUM_UNIT.fullmatch(tok) is not None or tok in _HALF or tok in _ONE
            or bool(word_amounts(tok)))


@lru_cache(maxsize=8192)
def _content(text: str) -> tuple[str, ...]:
    """The naming words: no unit, pack or size words ("Aavin Milk 500 ml" → ("aavin", "milk"))."""
    return tuple(t for t in tokens(text) if t not in _FILLER and not _is_size_word(t))


def _token_hit(alias_tok: str, spoken: tuple[str, ...]) -> bool:
    for s in spoken:
        if s == alias_tok:
            return True
        if (len(alias_tok) >= _FUZZY_MIN_LEN and len(s) >= _FUZZY_MIN_LEN - 1
                and SequenceMatcher(None, alias_tok, s).ratio() >= FUZZY_RATIO):
            return True
    return False


def _alias_score(alias: str, spoken: tuple[str, ...]) -> int:
    """Number of naming words in `alias` when every one of them was spoken, else 0."""
    words = _content(alias)
    return len(words) if words and all(_token_hit(w, spoken) for w in words) else 0


def _score(doc: dict, spoken: tuple[str, ...]) -> int:
    names = [doc.get("name", "")] + [a for lang in (doc.get("aliases") or {}).values() for a in lang]
    return max((_alias_score(a, spoken) for a in names if a), default=0)


# ---------- sizes ----------

def _unit_of(tok: str) -> str | None:
    return next((unit for unit, words in _UNITS.items() if tok in words), None)


def _size(n: Fraction, unit: str) -> tuple[str, Fraction]:
    """("L", litres) or ("G", grams)."""
    if unit == "L":
        return "L", n
    if unit == "ml":
        return "L", n / 1000
    if unit == "kg":
        return "G", n * 1000
    return "G", n


def _number_before(toks: list[str], i: int) -> Fraction | None:
    prev = toks[i - 1]
    if prev.isdigit():
        return Fraction(int(prev))
    if prev in _ONE:
        return Fraction(1)
    j = i
    while j > 0 and i - j < 4 and word_amounts(toks[j - 1]):
        j -= 1
    if j == i:
        return None
    values = word_amounts(" ".join(toks[j:i]))
    return values[0] if len(values) == 1 else None


def _size_hints(spoken: str) -> set[tuple[str, Fraction]]:
    """Pack sizes named in the utterance."""
    toks = tokens(spoken)
    out: set[tuple[str, Fraction]] = set()
    for i, tok in enumerate(toks):
        if m := _NUM_UNIT.fullmatch(tok):
            out.add(_size(Fraction(int(m.group(1))), _unit_of(m.group(2))))
        elif tok in _HALF:
            nxt = _unit_of(toks[i + 1]) if i + 1 < len(toks) else None
            out.add(_size(Fraction(1, 2), nxt or "L"))          # "half" alone means half a litre
        elif (unit := _unit_of(tok)) and i > 0:
            n = _number_before(toks, i)
            if n is not None:
                out.add(_size(n, unit))
    return out


def _fits(doc: dict, hint: tuple[str, Fraction]) -> bool:
    kind, value = hint
    size = doc.get("packSizeL") if kind == "L" else doc.get("packSizeG")
    return isinstance(size, (int, float)) and math.isclose(float(size), float(value), abs_tol=1e-9)


# ---------- products ----------

def _ref(sku: str, p: dict) -> ProductRef:
    return ProductRef(sku=sku, name=p.get("name") or sku, brand=p.get("brand") or "",
                      pack=p.get("pack") or p.get("unit") or "")


def _pick(products: list[tuple[str, dict]], spoken: str) -> ProductRef | None:
    key = spoken.strip().lower()
    for sku, p in products:
        if sku == key:                                    # the model passed a SKU id
            return _ref(sku, p)
    words = _content(spoken)
    if not words:
        return None
    scored = [(score, sku, p) for sku, p in products if (score := _score(p, words))]
    if not scored:
        return None
    top = max(s for s, _, _ in scored)
    tied = [(sku, p) for s, sku, p in scored if s == top]
    if len({_content(p.get("name", sku)) for sku, p in tied}) > 1:
        return None                                       # two different products: ask
    if len(tied) > 1:
        hints = _size_hints(spoken)
        sized = [(sku, p) for sku, p in tied if any(_fits(p, h) for h in hints)]
        if sized:
            tied = sized
    if len(tied) > 1:
        defaults = [(sku, p) for sku, p in tied if p.get("defaultPack")]
        if len(defaults) != 1:
            return None                                   # size variants, no default: ask which size
        tied = defaults
    return _ref(*tied[0])


def is_active(doc: dict) -> bool:
    """A product or supplier counts unless it was disabled by voice (``active: false``). Missing = active."""
    return doc.get("active") is not False


async def active_products(shop_id: str) -> list[tuple[str, dict]]:
    """(sku, doc) of every product that is not disabled. Read fresh from the Store on every call: nothing is
    cached here, so a product added or disabled a moment ago is seen at once."""
    return [(sku, p) for sku, p in await get_store().list(paths.products(shop_id)) if is_active(p)]


_suggestions: dict[tuple[str, str], list[ProductRef]] = {}


def _remember_suggestions(shop_id: str, spoken: str, refs: list[ProductRef]) -> None:
    key = (shop_id, spoken.strip().casefold())
    _suggestions.pop(key, None)
    _suggestions[key] = refs
    while len(_suggestions) > _SUGGEST_CACHE:
        _suggestions.pop(next(iter(_suggestions)))


async def nearest_products(shop_id: str, spoken: str, limit: int = VECTOR_LIMIT) -> list[tuple[ProductRef, float]]:
    """Active products nearest to ``spoken`` by vector search: (ref, cosine distance), nearest first.
    Empty when embeddings or the vector query are unavailable (no key, no index, any error)."""
    query = await embeddings.embed(spoken, query=True)
    if not query:
        return []
    try:
        rows = await get_store().find_nearest(paths.products(shop_id), embeddings.FIELD, query, limit=limit,
                                              threshold=VECTOR_THRESHOLD)
    except Exception as exc:  # noqa: BLE001 - a missing index or a transport error: behave as before
        log.warning("vector search failed for %r in %s: %s", spoken, shop_id, exc)
        return []
    return [(_ref(sku, doc), dist) for sku, doc, dist in rows if is_active(doc)]


async def resolve_vector(shop_id: str, spoken: str) -> tuple[ProductRef | None, list[ProductRef]]:
    """(sure match, suggestions) from vector search. The suggestions are remembered for ``did_you_mean``."""
    near = await nearest_products(shop_id, spoken)
    if not near:
        return None, []
    refs = [r for r, _ in near]
    (best, d0) = near[0]
    runner_up = near[1][1] if len(near) > 1 else None
    if d0 <= VECTOR_SURE and (runner_up is None or runner_up - d0 >= VECTOR_GAP):
        _remember_suggestions(shop_id, spoken, [])
        return best, refs
    _remember_suggestions(shop_id, spoken, refs)
    return None, refs


async def resolve(shop_id: str, spoken: str, lang: str) -> ProductRef | None:
    """Spoken product name (any of ta/hi/en) → ProductRef, or None when unknown, ambiguous or disabled.

    Exact / alias / size matching first; only when that finds nothing does the vector search run."""
    spoken = spoken or ""
    products = await active_products(shop_id)
    ref = _pick(products, spoken)
    if ref is not None or not _content(spoken):
        return ref
    if any(_score(p, _content(spoken)) for _, p in products):
        return None                                       # alias match was ambiguous: ask, do not guess
    sure, _ = await resolve_vector(shop_id, spoken)
    return sure


async def did_you_mean(shop_id: str, spoken: str, lang: str) -> str:
    """What to say for an unresolved product: "Did you mean Sakthi Curd 500 ml?" when the vector search found a
    close product, else the plain PRODUCT_UNKNOWN hint. Uses the suggestions ``resolve`` just found."""
    key = (shop_id, (spoken or "").strip().casefold())
    refs = _suggestions.get(key)
    if refs is None:
        _, refs = await resolve_vector(shop_id, spoken or "")
    if not refs:
        return hints.say("PRODUCT_UNKNOWN", lang, product=spoken)
    return t("biz.product.did_you_mean", lang, name=refs[0].name)


# ---------- suppliers ----------

def _supplier(supplier_id: str, doc: dict) -> dict:
    out = {**doc, "id": supplier_id}
    out.pop("phoneEnv", None)
    out.setdefault("name", supplier_id)
    out.setdefault("phone", "")
    out.setdefault("channel", "whatsapp")
    out.setdefault("products", [])
    out.setdefault("aliases", {})
    return out


async def resolve_supplier(shop_id: str, spoken: str, lang: str) -> dict | None:
    """Spoken supplier ("Sakthi vendor", "பால்காரர்", "ice cream") → {id, name, phone, channel, products, aliases}.
    A product name finds the one supplier of that product. None when unknown or ambiguous."""
    spoken = spoken or ""
    suppliers = [(sid, s) for sid, s in await get_store().list(paths.suppliers(shop_id)) if is_active(s)]
    key = spoken.strip().lower()
    for sid, s in suppliers:
        if sid == key:
            return _supplier(sid, s)
    words = _content(spoken)
    if not words:
        return None
    scored = [(score, sid, s) for sid, s in suppliers if (score := _score(s, words))]
    if scored:
        top = max(x[0] for x in scored)
        tied = [(sid, s) for score, sid, s in scored if score == top]
        if len(tied) == 1:
            return _supplier(*tied[0])
    ref = await resolve(shop_id, spoken, lang)              # "order ice cream" → the supplier of that product
    if ref is not None:
        matches = [(sid, s) for sid, s in suppliers if ref.sku in (s.get("products") or [])]
        if len(matches) == 1:
            return _supplier(*matches[0])
    return None


# ---------- quantities ----------

def _positive(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def _spoken_unit(unit: str | None, selling_unit: str) -> str | None:
    """"L" | "ml" | "kg" | "g" | "count", or None for a word that makes no sense for this product."""
    u = (unit or "").strip().lower().rstrip(".")
    canon = _unit_of(u)
    if canon:
        return canon
    own = selling_unit.lower()
    if u in _COUNT_WORDS or u == own or u in _CONTAINERS.get(own, frozenset()):
        return "count"
    return None


def to_selling_units(product_doc: dict, qty: float, unit: str) -> tuple[float, str]:
    """A spoken quantity → (qty, unit) in the product's selling unit, which the ledger stores.

    "5 kg paneer" → (25.0, "pack"); "2 litres milk" → (2.0, "L"); "3 ghee" → (3.0, "jar");
    2 × sakthi_curd_500ml counted as unit="pack" → (1.0, "L").
    Raises ValueError for a negative quantity, an unknown unit, a unit the product cannot be measured in
    ("kg" of milk), or an amount that is not a whole number of packs: AIRA never invents half a pack.
    """
    sku = product_doc.get("sku", "?")
    try:
        q = float(qty)
    except (TypeError, ValueError):
        raise ValueError(f"{sku}: not a quantity: {qty!r}") from None
    if not math.isfinite(q) or q < 0:
        raise ValueError(f"{sku}: quantity must be zero or more, got {qty!r}")
    selling = product_doc.get("unit") or "pack"
    kind = _spoken_unit(unit, selling)
    if kind is None:
        raise ValueError(f"{sku}: {unit!r} is not a unit for this product")
    if kind == "count":
        packs = q
    elif kind in ("L", "ml"):
        size_l = _positive(product_doc.get("packSizeL"))
        if size_l is None:
            raise ValueError(f"{sku} is not measured in litres")
        packs = (q if kind == "L" else q / 1000) / size_l
    else:
        size_g = _positive(product_doc.get("packSizeG"))
        if size_g is None:
            raise ValueError(f"{sku} is not measured in grams")
        packs = (q * 1000 if kind == "kg" else q) / size_g
    whole = round(packs)
    if abs(packs - whole) > 1e-6:
        raise ValueError(f"{sku}: {qty} {unit} is {packs:g} packs, not a whole number")
    if selling == "L":
        size_l = _positive(product_doc.get("packSizeL"))
        if size_l is None:
            raise ValueError(f"{sku} is sold by the litre but has no packSizeL")
        return round(whole * size_l, 6), "L"
    return float(whole), selling
