"""Delivery tools. OWNER: Kanish (kanish/02); uses Sarmitha's vision. Stubs until then."""
from typing import Literal


async def delivery_start(order_id: str | None = None) -> dict:
    """Open a delivery for the order (the latest open order when order_id is None); state DELIVERY_PENDING."""
    raise NotImplementedError


async def delivery_count(sku: str, mode: Literal["one_by_one", "crate"] = "one_by_one") -> dict:
    """Count the delivered packs of one product with the camera, one by one or as a crate.

    Streams count messages, reads the count back for confirmation and records only the verified quantity.
    """
    raise NotImplementedError


async def invoice_check(order_id: str) -> dict:
    """Read the paper invoice twice and reconcile it with the order and the verified counts."""
    raise NotImplementedError
