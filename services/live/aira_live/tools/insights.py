"""Insight tools. OWNER: Saravana (saravana/02). Stubs until then."""


async def day_summary() -> dict:
    """Say today's sales, top sellers and suggested orders."""
    raise NotImplementedError


async def reorder_suggest(product: str | None = None) -> dict:
    """Suggest a reorder quantity from recent sales, for one product or for all (only suggests, never orders)."""
    raise NotImplementedError
