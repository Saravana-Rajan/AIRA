"""Payment tools (cash or UPI only). OWNER: Kanish (kanish/02). Stubs until then."""
from typing import Literal


async def supplier_pay(order_id: str, method: Literal["cash", "upi"]) -> dict:
    """Propose paying the vendor for an order. Cash is checked with the camera before paying.

    Returns a confirm proposal first; the commit sets PAYMENT_CONFIRMED.
    """
    raise NotImplementedError


async def sale_pay(sale_id: str, method: Literal["cash", "upi"]) -> dict:
    """Take the customer's payment for a pending sale.

    Cash: read the notes and say the change due. UPI: wait for the soundbox announcement.
    Returns a confirm proposal first; the commit sets SALE_COMPLETED.
    """
    raise NotImplementedError
