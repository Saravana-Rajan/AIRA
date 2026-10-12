"""Customer sales. OWNER: Kanish (kanish/02).

``sale_start`` prices a customer's request in each product's selling unit and opens a SALE_PENDING sale;
``sale_add`` folds more items into that open sale. Both read the running total back to the owner in his earphone
("2 Aavin milk and 1 Sakthi curd, total 120 rupees"): he may miscount, and the phone's screen faces his chest.
Nothing leaves stock here: the sale completes only in the committer ``sale.complete`` (``tools/payments.py``),
after the payment has evidence and the owner said "yes".
"""
from aira_live import ids
from aira_live.business import hints, ledger, sales
from aira_live.business.ledger import InsufficientStock
from aira_live.contracts import StateMsg
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import Session, get_session
from aira_live.tools.orders import product_doc, resolve_lines

active_sale: dict[str, str] = {}   # session_id → pending sale id


def join_and(phrases: list[str], lang: str) -> str:
    """'a', 'a and b', 'a, b and c' in the session's language."""
    if len(phrases) < 2:
        return "".join(phrases)
    return ", ".join(phrases[:-1]) + t("k2.list.and", lang) + phrases[-1]


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


async def _price(s: Session, lines: list[dict], phrases: list[str], key: str) -> dict:
    """Check stock for every line, open the sale, tell the owner the (running) total."""
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
            "say": t(key, s.lang, items=join_and(phrases, s.lang), total=sale["total"])}


async def sale_start(items: list[dict]) -> dict:
    """A CUSTOMER asks for products, e.g. "2 litres curd and 1 paneer".

    items: [{"product": "curd", "qty": 2, "unit": "L"}]. Leave unit out when the customer only gave a count
    ("2 milk"): a bare count means packs, never litres. Opens a pending
    sale and says the items and the total. Then ask "cash or UPI?" and call sale_pay. If the customer adds
    more items before paying ("and one curd too"), call sale_add, not sale_start again.
    """
    s = get_session()
    if not items:
        return {"ok": False, "say": t("k2.sale.no_items", s.lang)}
    lines, phrases, err = await resolve_lines(s.shop_id, items, s.lang)
    if err:
        return {"ok": False, "say": err}
    return await _price(s, lines, phrases, "k2.sale.price")


async def sale_add(items: list[dict]) -> dict:
    """The customer adds items to the open sale before paying ("and one curd too").

    items: same shape as sale_start. Keeps what was already asked for, adds these, and says the running total
    ("2 Aavin milk and 1 Sakthi curd, total 120 rupees so far"). With no open sale it starts one.
    """
    s = get_session()
    if not items:
        return {"ok": False, "say": t("k2.sale.no_items", s.lang)}
    lines, phrases, err = await resolve_lines(s.shop_id, items, s.lang)
    if err:
        return {"ok": False, "say": err}
    before = await _open_sale(s)
    if before is None:
        return await _price(s, lines, phrases, "k2.sale.price")
    old_lines, old_phrases = [], []
    for line in before["items"]:
        prod = await product_doc(s.shop_id, line["sku"])
        unit = prod.get("unit", "")
        old_lines.append({"sku": line["sku"], "name": prod.get("name", line["sku"]), "qty": float(line["qty"]),
                          "unit": unit})
        old_phrases.append(f"{fmt_qty(line['qty'])} {unit} {prod.get('name', line['sku'])}".replace("  ", " "))
    out = await _price(s, old_lines + lines, old_phrases + phrases, "k2.sale.running")
    if out["ok"]:                                   # the merged sale replaces the old one; nothing is lost on failure
        await sales.void(s.shop_id, before["id"])
    return out
