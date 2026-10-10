"""Supplier order tools. OWNER: Kanish (kanish/02). Stubs until then: exact signatures from the interfaces table."""


async def order_create(supplier: str, items: list[dict]) -> dict:
    """Propose a supplier order, e.g. supplier "Sakthi vendor", items [{"name": "curd 1 litre", "qty": 30}].

    Returns a confirm proposal first. Only after the owner says yes is the order sent
    (messaging.send_order) and set to ORDER_PLACED.
    """
    raise NotImplementedError


async def order_status(order_id: str | None = None) -> dict:
    """Say the state and the vendor's reply for the latest order, or for the given order_id."""
    raise NotImplementedError
