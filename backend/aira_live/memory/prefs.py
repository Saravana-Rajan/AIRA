"""Owner preferences (spec §5 shops/{shopId}/prefs/main).

This module defines `set`, so it never calls the built-in `set()`; it uses dict.keys() set operations.
"""
import copy

from aira_live import db as dbmod

DEFAULTS = {"speechRate": 1.0, "verbosity": "short", "productAliases": {}, "supplierAliases": {}}
_FIELDS = {"speech_rate": "speechRate", "verbosity": "verbosity",
           "product_aliases": "productAliases", "supplier_aliases": "supplierAliases"}


def _doc(shop_id: str):
    return dbmod.db().collection("shops").document(shop_id).collection("prefs").document("main")


async def get(shop_id: str) -> dict:
    """Stored prefs over the defaults; always has speechRate, verbosity, productAliases, supplierAliases."""
    snap = await _doc(shop_id).get()
    stored = snap.to_dict() if snap.exists else {}
    return {**copy.deepcopy(DEFAULTS), **(stored or {})}


async def set(shop_id: str, **kw) -> dict:
    """Merge the given prefs (speech_rate, verbosity, product_aliases, supplier_aliases); returns all prefs.

    Each given field is replaced whole (an alias map is not deep-merged), so an alias can be removed by
    passing the full new map. Fields not given keep their stored value.
    """
    unknown = kw.keys() - _FIELDS.keys()
    if unknown:
        raise ValueError(f"unknown preference(s): {sorted(unknown)}")
    update = {_FIELDS[k]: v for k, v in kw.items()}
    if "speechRate" in update:
        rate = update["speechRate"]
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0.5 <= rate <= 2.0:
            raise ValueError("speech_rate must be a number between 0.5 and 2.0")
        update["speechRate"] = float(rate)
    if "verbosity" in update and update["verbosity"] not in ("short", "detail"):
        raise ValueError("verbosity must be 'short' or 'detail'")
    for key in ("productAliases", "supplierAliases"):
        if key in update and not (isinstance(update[key], dict)
                                  and all(isinstance(a, str) and isinstance(b, str) for a, b in update[key].items())):
            raise ValueError(f"{key} must map spoken names to ids (str -> str)")
    if update:
        # A field-path list (not merge=True): Firestore then replaces each listed field instead of
        # deep-merging nested maps such as productAliases.
        await _doc(shop_id).set(update, merge=sorted(update))
    return await get(shop_id)
