"""Engine outcome code -> i18n key. Tools speak hints.say(code, lang, **kw); the engine never builds sentences.

The keys live in aira_live/i18n/{en,ta,hi}.json. Extra keyword arguments are allowed and ignored by sentences
that do not use them, so a tool can pass everything it knows (product, name, expected, actual, ...).
"""
from aira_live import i18n

HINTS: dict[str, str] = {
    "InsufficientStock": "biz.stock.insufficient",             # product, have
    "InsufficientTender": "biz.cash.short",                    # short
    "PaymentMismatch": "biz.payment.mismatch",
    "InvalidTransition": "biz.state.invalid",
    "SHORT_DELIVERY": "biz.delivery.short",                    # product, expected, actual
    "OVER_DELIVERY": "biz.delivery.over",                      # product, expected, actual
    "UNORDERED_ITEM": "biz.delivery.unordered",                # product
    "INVOICE_UNREADABLE": "biz.invoice.unreadable",
    "UNKNOWN_INVOICE_LINE": "biz.invoice.unknown_line",        # name
    "INVOICE_QTY_MISMATCH": "biz.invoice.qty_mismatch",        # product, expected, actual
    "PRICE_MISMATCH": "biz.invoice.price_mismatch",            # product, expected, actual
    "LINE_TOTAL_MISMATCH": "biz.invoice.line_total_mismatch",  # product
    "TOTAL_MISMATCH": "biz.invoice.total_mismatch",            # expected, actual
    "PRODUCT_AMBIGUOUS": "biz.product.ambiguous",              # options
    "PRODUCT_UNKNOWN": "biz.product.unknown",
    "SUPPLIER_UNKNOWN": "biz.supplier.unknown",
    "SOUNDBOX_UNSURE": "biz.upi.unsure",
}


def key_for(code: str) -> str:
    """The i18n key for an engine code. Raises KeyError for an unknown code."""
    return HINTS[code]


def say(code: str, lang: str, **kw) -> str:
    """The spoken sentence for an engine code: exactly aira_live.i18n.t(key_for(code), lang, **kw)."""
    return i18n.t(HINTS[code], lang, **kw)
