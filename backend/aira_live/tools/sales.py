"""Customer sales. OWNER: Kanish (kanish/02).

``sale_start`` prices a customer's request in each product's selling unit and opens a SALE_PENDING sale;
``sale_add`` folds more items into that open sale. Both return the items and the running total as facts (plus an
English ``say_en`` hint such as "2 L Sakthi Curd 1 L and 1 Aavin Milk, total 120 rupees. Take the cash.") so Live
reads them back to the owner in his earphone, in his language: he may miscount, and the phone's screen faces
his chest. Nothing leaves stock here: the sale completes only in the committer ``sale.complete``
(``tools/payments.py``), after the payment has evidence and the owner said "yes".
"""
from aira_live import ids, monitor
from aira_live.business import hints, ledger, sales
from aira_live.business.ledger import InsufficientStock
from aira_live.contracts import StateMsg
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import Session, get_session
from aira_live.tools.orders import line_facts, product_doc, resolve_lines

active_sale: dict[str, str] = {}   # session_id → pending sale id
NO_ITEMS = {"ok": False, "error": "NO_ITEMS", "say_en": "What does the customer want?"}


def join_and(phrases: list[str], lang: str | None = None) -> str:
    """'a', 'a and b', 'a, b and c' (English; ``lang`` is accepted for compatibility and ignored)."""
    if len(phrases) < 2:
        return "".join(phrases)
    return ", ".join(phrases[:-1]) + " and " + phrases[-1]


def _insufficient(line: dict, have: float) -> dict:
    return {**hints.fact("InsufficientStock", product=line["name"], have=fmt_qty(have)),
            "onHand": have, "unit": line.get("unit", "")}


async def _open_sale(s: Session) -> dict | None:
    """This session's SALE_PENDING sale, if any (a completed or voided one no longer counts)."""
    from aira_live.business import paths
    from aira_live.business.store import get_store
    sid = active_sale.get(s.session_id)
    if not sid:
        return None
    doc = await get_store().get(paths.sale(s.shop_id, sid))
    if doc is None or doc["state"] != "SALE_PENDING":
        active_sale.pop(s.session_id, None)
        return None
    return {**doc, "id": sid}


async def _price(s: Session, lines: list[dict], phrases: list[str], running: bool = False) -> dict:
    """Check stock for every line, open the sale, return the items and the (running) total."""
    need: dict[str, float] = {}
    for line in lines:
        need[line["sku"]] = need.get(line["sku"], 0.0) + line["qty"]
    for line in lines:
        have = await ledger.on_hand(s.shop_id, line["sku"])
        if have + 1e-9 < need[line["sku"]]:
            return _insufficient(line, have)
    try:
        sale = await sales.create(s.shop_id, [{"sku": ln["sku"], "qty": ln["qty"]} for ln in lines],
                                  ids.current_idem_key())
    except InsufficientStock:                      # stock moved between the check and the transaction
        return _insufficient(lines[0], await ledger.on_hand(s.shop_id, lines[0]["sku"]))
    active_sale[s.session_id] = sale["id"]
    await s.send(StateMsg(entity="sale", id=sale["id"], state=sale["state"], summary=str(sale["total"])))
    so_far = " so far" if running else ""
    return {"ok": True, "saleId": sale["id"], "total": sale["total"], "items": line_facts(lines),
            "say_en": f"{join_and(phrases)}, total {sale['total']} rupees{so_far}. Take the cash."}


async def sale_start(items: list[dict]) -> dict:
    """A CUSTOMER asks for products, e.g. "2 litres curd and 1 paneer".

    items: [{"product": "curd", "qty": 2, "unit": "L"}]. Leave unit out when the customer only gave a count
    ("2 milk"): a bare count means packs, never litres. Opens a pending
    sale and returns the items and the total. Then ask "cash or UPI?" and call sale_pay. If the customer adds
    more items before paying ("and one curd too"), call sale_add, not sale_start again.
    """
    s = get_session()
    if not items:
        return dict(NO_ITEMS)
    # The tool registry is frozen (no new parameter), and a sale is a customer talking by definition: the
    # dashboard labels the next user lines "customer" while this sale is open (aira_live.monitor).
    monitor.set_speaker(s.session_id, "customer")
    lines, phrases, err = await resolve_lines(s.shop_id, items, s.lang)
    if err:
        return err
    return await _price(s, lines, phrases)


async def sale_add(items: list[dict]) -> dict:
    """The customer adds items to the open sale before paying ("and one curd too").

    items: same shape as sale_start. Keeps what was already asked for, adds these, and returns the running total
    ("2 Aavin milk and 1 Sakthi curd, total 120 rupees so far"). With no open sale it starts one.
    """
    s = get_session()
    if not items:
        return dict(NO_ITEMS)
    monitor.set_speaker(s.session_id, "customer")
    lines, phrases, err = await resolve_lines(s.shop_id, items, s.lang)
    if err:
        return err
    before = await _open_sale(s)
    if before is None:
        return await _price(s, lines, phrases)
    old_lines, old_phrases = [], []
    for line in before["items"]:
        prod = await product_doc(s.shop_id, line["sku"])
        unit = prod.get("unit", "")
        old_lines.append({"sku": line["sku"], "name": prod.get("name", line["sku"]), "qty": float(line["qty"]),
                          "unit": unit})
        old_phrases.append(f"{fmt_qty(line['qty'])} {unit} {prod.get('name', line['sku'])}".replace("  ", " "))
    out = await _price(s, old_lines + lines, old_phrases + phrases, running=True)
    if out["ok"]:                                   # the merged sale replaces the old one; nothing is lost on failure
        await sales.void(s.shop_id, before["id"])
    return out
