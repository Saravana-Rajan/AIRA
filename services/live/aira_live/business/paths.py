"""Single source of Firestore paths (spec §5)."""
import hashlib


def shop(shop_id: str) -> str:
    return f"shops/{shop_id}"


def product(shop_id: str, sku: str) -> str:
    return f"shops/{shop_id}/products/{sku}"


def products(shop_id: str) -> str:
    return f"shops/{shop_id}/products"


def supplier(shop_id: str, supplier_id: str) -> str:
    return f"shops/{shop_id}/suppliers/{supplier_id}"


def suppliers(shop_id: str) -> str:
    return f"shops/{shop_id}/suppliers"


def inventory(shop_id: str, sku: str) -> str:
    return f"shops/{shop_id}/inventory/{sku}"


def inventories(shop_id: str) -> str:
    return f"shops/{shop_id}/inventory"


def ledger(shop_id: str, txn_id: str) -> str:
    return f"shops/{shop_id}/ledger/{txn_id}"


def sale(shop_id: str, sale_id: str) -> str:
    return f"shops/{shop_id}/sales/{sale_id}"


def payment(shop_id: str, payment_id: str) -> str:
    return f"shops/{shop_id}/payments/{payment_id}"


def audit(shop_id: str, audit_id: str) -> str:
    return f"shops/{shop_id}/auditLogs/{audit_id}"


def idem(key: str) -> str:
    """Top-level idempotency doc; the doc id is the SHA-1 of the raw key."""
    return "idempotency/" + hashlib.sha1(key.encode("utf-8")).hexdigest()
