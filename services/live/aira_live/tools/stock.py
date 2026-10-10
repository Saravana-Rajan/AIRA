"""Stock tools. OWNER: Kanish (kanish/02). Stubs until then."""


async def stock_query(product: str | None = None) -> dict:
    """Say the recorded stock for one product, or a short summary of all products when product is None."""
    raise NotImplementedError


async def expiry_check() -> dict:
    """Say the batches that expire today or tomorrow, oldest first."""
    raise NotImplementedError
