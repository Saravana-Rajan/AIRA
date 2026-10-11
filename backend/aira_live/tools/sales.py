"""Customer sales. OWNER: Kanish (kanish/02).

``sale_start`` prices a customer's request in each product's selling unit and opens a SALE_PENDING sale. Nothing
leaves stock here: the sale completes only in the committer ``sale.complete`` (``tools/payments.py``), after the
payment has evidence and the owner said "yes".
"""
from aira_live import ids
from aira_live.business import hints, ledger, sales
from aira_live.business.ledger import InsufficientStock
from aira_live.contracts import StateMsg
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import get_session
from aira_live.tools.orders import resolve_lines

active_sale: dict[str, str] = {}   # session_id → pending sale id


async def sale_start(items: list[dict]) -> dict:
    """A CUSTOMER asks for products, e.g. "2 litres curd and 1 paneer".

    items: [{"product": "curd", "qty": 2, "unit": "L"}]. Leave unit out when the customer only gave a count
    ("2 milk"): a bare count means packs, never litres. Opens a pending
    sale and says the total. Then ask "cash or UPI?" and call sale_pay.
    """
    s = get_session()
    if not items:
        return {"ok": False, "say": t("k2.sale.no_items", s.lang)}
    lines, phrases, err = await resolve_lines(s.shop_id, items, s.lang)
    if err:
        return {"ok": False, "say": err}
    need: dict[str, float] = {}
    for line in lines:
        need[line["sku"]] = need.get(line["sku"], 0.0) + line["qty"]
    for line in lines:
        have = await ledger.on_hand(s.shop_id, line["sku"])
        if have + 1e-9 < need[line["sku"]]:
            return {"ok": False, "say": hints.say("InsufficientStock", s.lang, product=line["name"],
                                                  have=fmt_qty(have))}
    try:
        sale = await sales.create(s.shop_id, [{"sku": ln["sku"], "qty": ln["qty"]} for ln in lines],
                                  ids.current_idem_key())
    except InsufficientStock:                      # stock moved between the check and the transaction
        name = lines[0]["name"]
        have = await ledger.on_hand(s.shop_id, lines[0]["sku"])
        return {"ok": False, "say": hints.say("InsufficientStock", s.lang, product=name, have=fmt_qty(have))}
    active_sale[s.session_id] = sale["id"]
    await s.send(StateMsg(entity="sale", id=sale["id"], state=sale["state"], summary=str(sale["total"])))
    return {"ok": True, "saleId": sale["id"], "total": sale["total"],
            "say": t("k2.sale.price", s.lang, items=", ".join(phrases), total=sale["total"])}
