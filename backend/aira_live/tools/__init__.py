"""Every Live tool exposed to Gemini (names and signatures: the interfaces "Live tools" table)."""
from collections.abc import Awaitable, Callable

from aira_live.tools.camera import camera_ask
from aira_live.tools.catalog import product_add, product_disable, product_update, stock_add, supplier_add
from aira_live.tools.core import confirm_action
from aira_live.tools.delivery import delivery_count, delivery_start, invoice_check
from aira_live.tools.describe import describe_mode
from aira_live.tools.insights import day_summary, reorder_suggest
from aira_live.tools.orders import order_create, order_status
from aira_live.tools.payments import sale_pay, supplier_pay
from aira_live.tools.products import location_remember, product_find, product_identify
from aira_live.tools.sales import sale_start
from aira_live.tools.stock import expiry_check, stock_query

TOOL_FUNCS: list[Callable[..., Awaitable[dict]]] = [
    order_create, order_status, delivery_start, delivery_count, invoice_check,
    supplier_pay, sale_start, sale_pay, stock_query, expiry_check,
    product_find, product_identify, location_remember, camera_ask, describe_mode,
    day_summary, reorder_suggest, confirm_action,
    # catalog by voice (confirm-gated: each proposes, then commits after the owner's yes)
    product_add, product_update, product_disable, stock_add, supplier_add,
]
TOOLS_BY_NAME: dict[str, Callable[..., Awaitable[dict]]] = {f.__name__: f for f in TOOL_FUNCS}
# Live tools that run in the background (Live NON_BLOCKING behaviour): the conversation goes on while
# delivery_count streams counts and sale_pay waits for the soundbox.
NON_BLOCKING_TOOLS: frozenset[str] = frozenset({"delivery_count", "sale_pay"})
