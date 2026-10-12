"""Single source of Firestore paths (spec §5).

Every document id that can come from the model (order, delivery, sale, payment, supplier, product ids spoken or
echoed back by Gemini) goes through ``doc_id`` first: a Firestore path segment must never contain "/", so a bad
id raises ``ValueError`` instead of reading or writing a different document under the shop. Shop ids are
server-side (config / jobs' own pattern) and are not re-checked here.
"""
import hashlib
import re

DOC_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def doc_id(value: str) -> str:
    """``value`` when it is a safe single path segment, else ``ValueError``."""
    if not isinstance(value, str) or not DOC_ID.fullmatch(value):
        raise ValueError(f"not a valid document id: {value!r}")
    return value


def shop(shop_id: str) -> str:
    return f"shops/{shop_id}"


def product(shop_id: str, sku: str) -> str:
    return f"shops/{shop_id}/products/{doc_id(sku)}"


def products(shop_id: str) -> str:
    return f"shops/{shop_id}/products"


def supplier(shop_id: str, supplier_id: str) -> str:
    return f"shops/{shop_id}/suppliers/{doc_id(supplier_id)}"


def suppliers(shop_id: str) -> str:
    return f"shops/{shop_id}/suppliers"


def inventory(shop_id: str, sku: str) -> str:
    return f"shops/{shop_id}/inventory/{sku}"


def inventories(shop_id: str) -> str:
    return f"shops/{shop_id}/inventory"


def ledger(shop_id: str, txn_id: str) -> str:
    return f"shops/{shop_id}/ledger/{txn_id}"


def order(shop_id: str, order_id: str) -> str:
    return f"shops/{shop_id}/orders/{doc_id(order_id)}"


def orders(shop_id: str) -> str:
    return f"shops/{shop_id}/orders"


def delivery(shop_id: str, delivery_id: str) -> str:
    return f"shops/{shop_id}/deliveries/{doc_id(delivery_id)}"


def deliveries(shop_id: str) -> str:
    return f"shops/{shop_id}/deliveries"


def sale(shop_id: str, sale_id: str) -> str:
    return f"shops/{shop_id}/sales/{doc_id(sale_id)}"


def payment(shop_id: str, payment_id: str) -> str:
    return f"shops/{shop_id}/payments/{doc_id(payment_id)}"


def price_history(shop_id: str, entry_id: str) -> str:
    """Server-only: one entry per committed price change (tools/catalog.py product_update)."""
    return f"shops/{shop_id}/priceHistory/{entry_id}"


def price_histories(shop_id: str) -> str:
    return f"shops/{shop_id}/priceHistory"


def audit(shop_id: str, audit_id: str) -> str:
    return f"shops/{shop_id}/auditLogs/{audit_id}"


def idem(key: str) -> str:
    """Top-level idempotency doc; the doc id is the SHA-1 of the raw key."""
    return "idempotency/" + hashlib.sha1(key.encode("utf-8")).hexdigest()
