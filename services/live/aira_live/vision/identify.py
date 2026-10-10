"""Which product is Murugan holding? ER 2 picks from a candidate list; code decides whether to trust it."""
from __future__ import annotations

import json
import math
import re
from typing import Any

from pydantic import BaseModel

from aira_live.frames import Frame
from aira_live.vision.gemini import generate_json, image_part

ACCEPT = 0.6             # below this we never name a product
CONFUSION_ACCEPT = 0.8   # look-alike packs (500 ml vs 1 L, paneer vs butter) need more certainty AND a visible label
MAX_ALTERNATIVES = 3

# Look-alike packs of DIFFERENT products (plan addendum 2026-10-10). Size variants of one product
# (milk 500 ml / 1 L, curd 500 ml / 1 L, ghee 200 ml / 500 ml) are found automatically.
CONFUSION_PAIRS: frozenset[frozenset[str]] = frozenset({
    frozenset({"aavin_milk_500ml", "aavin_buttermilk_200ml"}),
    frozenset({"aavin_paneer_200g", "aavin_butter_100g"}),
})
_SIZE_SUFFIX = re.compile(r"_\d+(?:\.\d+)?(?:ml|l|g|kg)$")


class ProductRef(BaseModel):
    sku: str
    name: str
    brand: str
    pack: str


class IdentifyResult(BaseModel):
    sku: str | None
    confidence: float
    alternatives: list[str]


IDENTIFY_PROMPT = (
    "A blind shopkeeper is holding up ONE product to the camera. Which ONE of these candidates is it?\n"
    "Candidates (JSON): {cands}\n"
    "Use the brand logo, colours and the printed product name and pack size (for example 500 ml vs 1 L, "
    "milk vs buttermilk, paneer vs butter). "
    "If the printed name or pack-size text is not readable, set label_visible to false. "
    "If none of the candidates match, set sku to null. Only use skus from the candidate list."
)
IDENTIFY_SCHEMA = (
    '{"sku": "<one candidate sku or null>", "confidence": 0.0, '
    '"alternatives": ["<other candidate sku>"], "label_visible": true}'
)


def _family(sku: str) -> str:
    """aavin_milk_500ml -> aavin_milk: the product without its pack size."""
    return _SIZE_SUFFIX.sub("", sku)


def confusion_partners(sku: str, candidates: list[ProductRef]) -> list[str]:
    """Candidate SKUs that look like `sku` on camera: same product in another pack size, or a known look-alike."""
    me = next((c for c in candidates if c.sku == sku), None)
    if me is None:
        return []
    out: list[str] = []
    for c in candidates:
        if c.sku == sku or c.sku in out:
            continue
        size_variant = (c.brand == me.brand and c.pack != me.pack
                        and (_family(c.sku) == _family(me.sku) or c.name.casefold() == me.name.casefold()))
        if size_variant or frozenset({sku, c.sku}) in CONFUSION_PAIRS:
            out.append(c.sku)
    return out


def _conf(v: Any) -> float:
    try:
        f = float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(f) or f < 0:
        return 0.0
    if f > 1.0:  # some replies use a percentage
        f = f / 100.0 if f <= 100.0 else 0.0
    return f


def _flag(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return v is True or (isinstance(v, (int, float)) and v == 1)


def _sku(v: Any, by_lower: dict[str, str]) -> str | None:
    if not isinstance(v, str):
        return None
    return by_lower.get(v.strip().lower())


def decide(data: dict, candidates: list[ProductRef]) -> IdentifyResult:
    """Pure: turn the model's raw answer into a result we are willing to speak. Never invents a SKU."""
    if not isinstance(data, dict):  # a bare string/number/list is not an answer we can trust
        data = {}
    if "sku" not in data and isinstance(data.get("items"), list):  # model answered with a list
        first = data["items"][0] if data["items"] else {}
        data = first if isinstance(first, dict) else {}
    by_lower = {c.sku.lower(): c.sku for c in candidates}
    raw = _sku(data.get("sku"), by_lower)
    conf = _conf(data.get("confidence"))
    # Missing label_visible = unknown; treat as not readable (matters only for look-alike packs).
    label_visible = _flag(data.get("label_visible", False))
    raw_alts = data.get("alternatives")
    alts: list[str] = []
    for a in raw_alts if isinstance(raw_alts, list) else []:
        s = _sku(a, by_lower)
        if s and s != raw and s not in alts:
            alts.append(s)
    if raw is None:
        return IdentifyResult(sku=None, confidence=0.0, alternatives=alts[:MAX_ALTERNATIVES])
    if conf < ACCEPT:
        return IdentifyResult(sku=None, confidence=conf, alternatives=[raw, *alts][:MAX_ALTERNATIVES])
    partners = confusion_partners(raw, candidates)
    if partners and (conf < CONFUSION_ACCEPT or not label_visible):
        ordered = [raw, *partners, *[a for a in alts if a not in partners]]
        return IdentifyResult(sku=None, confidence=conf, alternatives=ordered[:MAX_ALTERNATIVES])
    return IdentifyResult(sku=raw, confidence=conf, alternatives=alts[:MAX_ALTERNATIVES])


def next_hint(result: IdentifyResult, candidates: list[ProductRef]) -> str:
    """Aim-hint key (vision.aim.*) to speak after `result`; "" when the product was identified."""
    if result.sku:
        return ""
    if result.alternatives and any(
            p in result.alternatives for p in confusion_partners(result.alternatives[0], candidates)):
        return "vision.aim.show_label"
    return "vision.aim.hold_closer"


async def identify_product(frame: Frame, candidates: list[ProductRef]) -> IdentifyResult:
    """Ask the `point` model (ER 2, Flash fallback) which candidate is held up. Raises VisionError if every attempt fails."""
    if not candidates:
        return IdentifyResult(sku=None, confidence=0.0, alternatives=[])
    cands = json.dumps([c.model_dump() for c in candidates], ensure_ascii=False)
    data = await generate_json(
        "point",
        [image_part(frame.jpeg_b64), IDENTIFY_PROMPT.format(cands=cands)],
        IDENTIFY_SCHEMA,
        thinking="low",
        timeout_s=6.0,
    )
    return decide(data, candidates)
