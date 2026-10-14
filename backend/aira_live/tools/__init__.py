"""Every Live tool exposed to Gemini (names and signatures: the interfaces "Live tools" table)."""
from collections.abc import Awaitable, Callable

from aira_live.tools.camera import camera_ask, scene_recall
from aira_live.tools.catalog import product_add, product_disable, product_update, stock_add, supplier_add
from aira_live.tools.core import confirm_action
from aira_live.tools.delivery import delivery_count, delivery_start, invoice_check
from aira_live.tools.describe import describe_mode
from aira_live.tools.insights import day_summary, reorder_suggest
from aira_live.tools.memory import memory_forget, memory_remembering, memory_where, scene_report
from aira_live.tools.orders import order_create, order_status
from aira_live.tools.payments import sale_pay, supplier_pay
from aira_live.tools.prices import price_query
from aira_live.tools.products import location_remember, product_find, product_identify
from aira_live.tools.sales import sale_add, sale_start
from aira_live.tools.search import web_search
from aira_live.tools.stock import expiry_check, stock_query
from aira_live.tools.walk import nav_report, walk_stop, walk_to

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
    walk_to, walk_stop, nav_report,  # "walk with me": guidance from Live's own video (background loop, read-only)
    scene_recall,  # what was last counted, with age and evidence (read-only, not ER: also in Live-only mode)
    scene_report,  # Live's silent answer to a [SCENE CHECK]: what it sees, for the last-seen memory
]
TOOLS_BY_NAME: dict[str, Callable[..., Awaitable[dict]]] = {f.__name__: f for f in TOOL_FUNCS}
# Tools that work through Robotics-ER (camera questions, identification, narration, delivery counts).
# AIRA_ER2=0 leaves them out so Gemini Live answers from its own video (config.Settings.er2).
ER_TOOLS: frozenset[str] = frozenset({
    "camera_ask", "product_identify", "describe_mode", "delivery_count",
})
# "Walk with me" (tools.walk): Gemini Live only, no Robotics-ER. Gated by AIRA_WALK (config.Settings.walk,
# default off), independent of AIRA_ER2.
WALK_TOOLS: frozenset[str] = frozenset({"walk_to", "walk_stop", "nav_report"})
# Responses scheduled SILENT (and NON_BLOCKING): nav_report answers a [NAV CHECK]; Live never speaks it.
SILENT_TOOLS: frozenset[str] = frozenset({"nav_report", "scene_report"})
# Scene memory (aira_live.scene_watch): gated by AIRA_SCENE_MEMORY (config.Settings.scene_memory, default off).
SCENE_TOOLS: frozenset[str] = frozenset({"scene_report"})


def active_tools(er2: bool = True, walk: bool = False, scene: bool = False) -> list[Callable[..., Awaitable[dict]]]:
    """The Live tool list: without the Robotics-ER tools when ``er2`` is off (the Live-only mode), without
    the walk tools when ``walk`` is off and without scene_report when ``scene`` is off."""
    off = (frozenset() if er2 else ER_TOOLS) | (frozenset() if walk else WALK_TOOLS) | \
        (frozenset() if scene else SCENE_TOOLS)
    return [f for f in TOOL_FUNCS if f.__name__ not in off]
# Live tools that run in the background (Live NON_BLOCKING behaviour): the conversation goes on while
# delivery_count streams counts, sale_pay waits for the soundbox, walk_to starts the walk loop, and every camera / vision tool that takes
# over ~1.5 s (frames + a model call) runs while AIRA says a short holding line, so the owner hears AIRA within
# a second or two instead of silence. The prompt (rule 17) forbids any number, price, count or path claim in
# that holding line; the result is spoken when it lands (``tool_runtime`` sets the response scheduling).
NON_BLOCKING_TOOLS: frozenset[str] = frozenset({
    "delivery_count", "sale_pay", "walk_to",
    "camera_ask", "product_identify", "product_find", "invoice_check", "supplier_pay",
})
