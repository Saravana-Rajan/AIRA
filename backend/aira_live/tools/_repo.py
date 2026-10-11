"""Orders, deliveries and the message/contact indexes on the engine Store (Firestore in prod, MemoryStore in tests).

The indexes are the server-only collections `shops/{shopId}/msgIndex` and `shops/{shopId}/contactIndex`
(interfaces file), written only by `aira_live.messaging`:
- msgIndex/{channel}:{messageId}  → {shopId, orderId}      (Telegram keys are "{chatId}:{messageId}")
- contactIndex/{channel}:{contact} → {shopId, supplierId}  (WhatsApp contact = normalized phone, Telegram = chat id)
A webhook does not know the shop yet, so lookups default to the one demo shop (env `AIRA_DEMO_SHOP`).
"""
from aira_live import config
from aira_live.business import paths
from aira_live.business.store import get_store


def order_path(shop_id: str, oid: str) -> str:
    """``ValueError`` for an id that is not a single path segment (business.paths.doc_id)."""
    return paths.order(shop_id, oid)


def delivery_path(shop_id: str, did: str) -> str:
    return paths.delivery(shop_id, did)


def _index_path(shop_id: str, collection: str, channel: str, key: str) -> str:
    doc_id = f"{channel}:{key}".replace("/", "_")          # a '/' would split the Firestore path
    return f"shops/{shop_id}/{collection}/{doc_id}"


def _recency(doc: dict) -> str:
    return str(doc.get("updatedAt") or "")


async def get_order(shop_id: str, oid: str) -> dict | None:
    """The order, or None when it does not exist or the (model-supplied) id is not a valid document id."""
    try:
        path = order_path(shop_id, oid)
    except ValueError:
        return None
    doc = await get_store().get(path)
    return {**doc, "id": oid} if doc else None


async def save_order(shop_id: str, order: dict) -> None:
    await get_store().set(order_path(shop_id, order["id"]), order)


async def latest_order(shop_id: str, states: list[str], supplier_id: str | None = None,
                       skip_channels: tuple[str, ...] = ()) -> dict | None:
    """The most recently updated order in one of `states` (optionally of one supplier, and not sent on any of
    `skip_channels`), or None."""
    rows = await get_store().list(f"shops/{shop_id}/orders")
    cands = [{**d, "id": oid} for oid, d in rows
             if d.get("state") in states and (supplier_id is None or d.get("supplierId") == supplier_id)
             and d.get("channel") not in skip_channels]
    return max(cands, key=_recency, default=None)


async def get_delivery(shop_id: str, did: str) -> dict | None:
    try:
        path = delivery_path(shop_id, did)
    except ValueError:
        return None
    doc = await get_store().get(path)
    return {**doc, "id": did} if doc else None


async def save_delivery(shop_id: str, delivery: dict) -> None:
    await get_store().set(delivery_path(shop_id, delivery["id"]), delivery)


async def delivery_for_order(shop_id: str, order_id: str) -> dict | None:
    rows = await get_store().list(f"shops/{shop_id}/deliveries")
    found = [{**d, "id": did} for did, d in rows if d.get("orderId") == order_id]
    return max(found, key=_recency, default=None)


async def index_message(channel: str, key: str, shop_id: str, order_id: str) -> None:
    await get_store().set(_index_path(shop_id, "msgIndex", channel, key), {"shopId": shop_id, "orderId": order_id})


async def lookup_message(channel: str, key: str, shop_id: str | None = None) -> dict | None:
    return await get_store().get(_index_path(shop_id or config.load().demo_shop_id, "msgIndex", channel, key))


async def index_contact(channel: str, contact: str, shop_id: str, supplier_id: str) -> None:
    await get_store().set(_index_path(shop_id, "contactIndex", channel, contact),
                          {"shopId": shop_id, "supplierId": supplier_id})


async def lookup_contact(channel: str, contact: str, shop_id: str | None = None) -> dict | None:
    return await get_store().get(_index_path(shop_id or config.load().demo_shop_id, "contactIndex", channel, contact))


async def unlink_contact(channel: str, contact: str, shop_id: str | None = None) -> None:
    """Forget which supplier a contact spoke for (a Telegram re-link). The Store has no delete, so the index doc
    keeps the shop and loses its supplier; readers treat a missing ``supplierId`` as unknown."""
    shop_id = shop_id or config.load().demo_shop_id
    await get_store().set(_index_path(shop_id, "contactIndex", channel, contact), {"shopId": shop_id, "supplierId": None})
