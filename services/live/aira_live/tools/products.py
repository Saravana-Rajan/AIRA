"""Product tools. OWNER: Sarmitha (sarmitha/03). Stubs until then."""


async def product_find(product: str, count_needed: int = 1) -> dict:
    """Guide the owner's hand to a product: saved location first, then the camera.

    count_needed is how many packs to pick (set it for a customer sale). Sends target messages for hand guidance.
    """
    raise NotImplementedError


async def product_identify() -> dict:
    """Identify the pack in the owner's hand against the shop catalog."""
    raise NotImplementedError


async def location_remember(product: str, zone: str) -> dict:
    """Save where a product is kept, as confirmed by the owner, e.g. zone "fridge2/middle/left"."""
    raise NotImplementedError
