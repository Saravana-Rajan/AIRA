"""Every Live tool exposed to Gemini (names and signatures: the interfaces "Live tools" table)."""
from collections.abc import Awaitable, Callable

from aira_live.tools.camera import camera_ask, scene_recall
from aira_live.tools.catalog import product_add, product_disable, product_update, stock_add, supplier_add
from aira_live.tools.core import confirm_action
from aira_live.tools.delivery import delivery_count, delivery_start, invoice_check
from aira_live.tools.describe import describe_mode
from aira_live.tools.insights import day_summary, reorder_suggest
from aira_live.tools.memory import memory_forget, memory_remembering, memory_where
from aira_live.tools.orders import order_create, order_status
from aira_live.tools.payments import sale_pay, supplier_pay
from aira_live.tools.prices import price_query
from aira_live.tools.products import location_remember, product_find, product_identify
from aira_live.tools.sales import sale_add, sale_start
from aira_live.tools.search import web_search
from aira_live.tools.stock import expiry_check, stock_query
from aira_live.tools.walk import walk_stop, walk_to

TOOL_FUNCS: list[Callable[..., Awaitable[dict]]] = [
    order_create, order_status, delivery_start, delivery_count, invoice_check,
    supplier_pay, sale_start, sale_add, sale_pay, stock_query, expiry_check,
    product_find, product_identify, location_remember, camera_ask, describe_mode,
    day_summary, reorder_suggest, confirm_action,
    # last-seen memory of personal items (read-only: no confirmation)
    memory_where, memory_forget, memory_remembering,
    web_search,  # Google Search grounding for current facts (open assistant; read-only, external data)
    # catalog by voice (confirm-gated: each proposes, then commits after the owner's yes)
    product_add, product_update, product_disable, stock_add, supplier_add,
    price_query,  # the owner's price board, spoken (read-only: prices come from the products in Firestore)
    walk_to, walk_stop,  # "walk with me": continuous guidance to a place (background loop, read-only)
    scene_recall,  # what was last counted, with age and evidence (read-only, not ER: also in Live-only mode)
]
TOOLS_BY_NAME: dict[str, Callable[..., Awaitable[dict]]] = {f.__name__: f for f in TOOL_FUNCS}
# Tools that work through Robotics-ER (camera questions, identification, walking, narration, delivery counts).
# AIRA_ER2=0 leaves them out so Gemini Live answers from its own video (config.Settings.er2).
ER_TOOLS: frozenset[str] = frozenset({
    "camera_ask", "product_identify", "walk_to", "walk_stop", "describe_mode", "delivery_count",
})


def active_tools(er2: bool = True) -> list[Callable[..., Awaitable[dict]]]:
    """The Live tool list: every tool, or without the Robotics-ER ones in the Live-only test mode."""
    return list(TOOL_FUNCS) if er2 else [f for f in TOOL_FUNCS if f.__name__ not in ER_TOOLS]
# Live tools that run in the background (Live NON_BLOCKING behaviour): the conversation goes on while
# delivery_count streams counts, sale_pay waits for the soundbox, walk_to starts the walk loop, and every camera / vision tool that takes
# over ~1.5 s (frames + a model call) runs while AIRA says a short holding line, so the owner hears AIRA within
# a second or two instead of silence. The prompt (rule 17) forbids any number, price, count or path claim in
# that holding line; the result is spoken when it lands (``tool_runtime`` sets the response scheduling).
NON_BLOCKING_TOOLS: frozenset[str] = frozenset({
    "delivery_count", "sale_pay", "walk_to",
    "camera_ask", "product_identify", "product_find", "invoice_check", "supplier_pay",
})
