"""Engine outcome code -> facts for the Live model. The engine never builds sentences, and neither do tools.

``fact(code, **kw)`` is what a tool returns for an engine outcome: ``{"ok": False, "error": code, **kw,
"say_en": <short English meaning>}``. Gemini Live phrases it to the owner in whatever language he is speaking;
``say_en`` is only a meaning hint (and the caption when no Live session is attached). Extra keyword arguments
are kept as facts and ignored by meanings that do not use them, so a tool can pass everything it knows.
"""

HINTS: dict[str, str] = {
    "InsufficientStock": "Only {have} of {product} in stock.",
    "InsufficientTender": "That is {short} rupees short.",
    "PaymentMismatch": "The payment does not match. Please check again.",
    "InvalidTransition": "That step cannot be done now.",
    "SHORT_DELIVERY": "{product}: ordered {expected}, counted {actual}. Short delivery.",
    "OVER_DELIVERY": "{product}: ordered {expected}, counted {actual}. Extra items.",
    "UNORDERED_ITEM": "{product} was not in the order.",
    "INVOICE_UNREADABLE": "Could not read the bill clearly; ask the owner to show it again.",
    "UNKNOWN_INVOICE_LINE": "The bill has an item that was not ordered: {name}.",
    "INVOICE_QTY_MISMATCH": "Bill says {actual} {product}, but {expected} arrived.",
    "PRICE_MISMATCH": "Bill price for {product} is {actual} rupees, agreed price is {expected}.",
    "LINE_TOTAL_MISMATCH": "The line total for {product} is wrong on the bill.",
    "TOTAL_MISMATCH": "Bill total is {actual} rupees, but the items add up to {expected}.",
    "PRODUCT_AMBIGUOUS": "Which one: {options}?",
    "PRODUCT_UNKNOWN": "That product is not in the shop's catalog.",
    "SUPPLIER_UNKNOWN": "That vendor is not known.",
    "SOUNDBOX_UNSURE": "The payment confirmation was not heard clearly.",
}


def english(code: str, **kw) -> str:
    """The short English meaning of an engine code. Raises KeyError for an unknown code."""
    text = HINTS[code]
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        return text


def fact(code: str, **kw) -> dict:
    """The tool result for an engine outcome: ``ok`` False, the code, every fact given, and the English meaning.
    Raises KeyError for an unknown code."""
    return {"ok": False, "error": code, **kw, "say_en": english(code, **kw)}
