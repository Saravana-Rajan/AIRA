"""Delivery tools. OWNER: Kanish (kanish/02); uses Sarmitha's vision.

Open → count (verified quantity only) → invoice check.
- ``delivery_start`` moves the placed order to DELIVERY_PENDING and opens a delivery doc.
- ``delivery_count`` counts ONE product with the camera, one pack at a time (``OnePassCounter`` de-duplicates
  frames) or as a crate (two count reads that must agree). It reads the count back through ``confirm.propose``;
  only after the owner's "yes" does the committer ``delivery.record_count`` put the counted quantity into stock.
  An order is not a delivery, and an invoice is not proof of receipt: only the confirmed count enters stock.
- ``invoice_check`` reads the paper bill twice and reconciles it with the order and the counts.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Literal

from aira_live import confirm, ids
from aira_live.business import audit, catalog, hints, ledger, outbox, paths, reconcile
from aira_live.business._ids import doc_id_for
from aira_live.business.clock import now_iso
from aira_live.business.states import transition
from aira_live.business.store import get_store
from aira_live.contracts import CountMsg, CueMsg, RequestFrameMsg, StateMsg
from aira_live.i18n import t
from aira_live.messaging.clicktochat import fmt_qty
from aira_live.session import Session, get_session
from aira_live.tools import _repo
from aira_live.vision import count as vcount
from aira_live.vision import identify as vid
from aira_live.vision import invoice as vinv

COUNT_IDLE_S = 8.0           # stop counting after this long without a new frame
COUNT_MAX_S = 180.0          # hard cap per count session (the tool is non-blocking)
COUNT_KINDS = {"SHORT_DELIVERY", "OVER_DELIVERY", "UNORDERED_ITEM"}   # published when the count is saved
OPEN_STATES = ["DELIVERY_PENDING", "DISCREPANCY"]
# A bill can be checked until the vendor is paid, also after a matching count or an earlier matching bill.
INVOICE_STATES = [*OPEN_STATES, "DELIVERY_RECONCILED"]
_active: dict[str, str] = {}  # session_id → delivery_id


@dataclass
class OnePassCounter:
    """Counts packs shown one at a time. Pure: feed it (timestamp, identified sku, confidence) per frame.

    A pack counts when the SKU matches with confidence >= min_confidence and either it is the first pack, or an
    absence was seen since the last counted pack and at least gap_ms have passed. A pack held in view never
    counts twice; a short flicker is ignored.
    """
    sku: str
    min_confidence: float = 0.7
    gap_ms: float = 1200.0
    counted: int = 0
    confidences: list[float] = field(default_factory=list)
    _last_count_ts: float = 0.0
    _absent: bool = False

    def observe(self, ts_ms: float, sku: str | None, confidence: float) -> bool:
        """Feed one identified frame. True when it adds a new pack."""
        if sku != self.sku or confidence < self.min_confidence:
            self._absent = True
            return False
        if self.counted == 0 or (self._absent and ts_ms - self._last_count_ts >= self.gap_ms):
            self.counted += 1
            self.confidences.append(confidence)
            self._last_count_ts, self._absent = ts_ms, False
            return True
        self._absent = False                                   # the same pack still (or again) in view
        return False

    @property
    def confidence(self) -> float:
        return min(self.confidences) if self.confidences else 0.0


async def _product(shop_id: str, sku: str) -> dict:
    return {**(await get_store().get(paths.product(shop_id, sku)) or {}), "sku": sku}


async def _resolve_sku(s: Session, spoken: str, delivery: dict) -> str | None:
    if any(i["sku"] == spoken for i in delivery["items"]):
        return spoken
    ref = await catalog.resolve(s.shop_id, spoken, s.lang)
    return ref.sku if ref else None


async def _current_delivery(s: Session) -> dict | None:
    did = _active.get(s.session_id)
    if did and (d := await _repo.get_delivery(s.shop_id, did)):
        return d
    order = await _repo.latest_order(s.shop_id, OPEN_STATES)
    return await _repo.delivery_for_order(s.shop_id, order["id"]) if order else None


async def delivery_start(order_id: str | None = None) -> dict:
    """Open a delivery for the order (the latest open order when order_id is None); state DELIVERY_PENDING."""
    s = get_session()
    order = (await _repo.get_order(s.shop_id, order_id) if order_id
             else await _repo.latest_order(s.shop_id, ["ORDER_PLACED", *OPEN_STATES]))
    if order is None or order["state"] not in ("ORDER_PLACED", *OPEN_STATES):
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    if order["state"] == "ORDER_PLACED":
        order = {**order, "state": transition("order", "ORDER_PLACED", "DELIVERY_PENDING"), "updatedAt": now_iso()}
        await _repo.save_order(s.shop_id, order)
        await outbox.order_state(s.shop_id, order["id"], "DELIVERY_PENDING")
    d = await _repo.delivery_for_order(s.shop_id, order["id"])
    if d is None:
        now = now_iso()
        d = {"id": doc_id_for("d", order.get("idemKey") or order["id"]), "orderId": order["id"],
             "state": "DELIVERY_PENDING", "discrepancies": [], "createdAt": now, "updatedAt": now,
             "items": [{"sku": it["sku"], "name": it["name"], "unit": it["unit"], "expectedQty": float(it["qty"]),
                        "countedQty": None, "method": None, "confidence": None, "countIdem": None}
                       for it in order["items"]]}
        await _repo.save_delivery(s.shop_id, d)
    _active[s.session_id] = d["id"]
    supplier = order.get("supplierName") or order.get("supplierId", "")
    await s.send(StateMsg(entity="delivery", id=d["id"], state="DELIVERY_PENDING", summary=supplier))
    return {"ok": True, "deliveryId": d["id"], "orderId": order["id"], "skus": [i["sku"] for i in d["items"]],
            "say": t("k2.delivery.started", s.lang, supplier=supplier)}


async def _count_one_by_one(s: Session, product: dict, expected_packs: int | None) -> tuple[int, float]:
    rows = await get_store().list(paths.products(s.shop_id))
    candidates = [vid.ProductRef(sku=sku, name=p.get("name", sku), brand=p.get("brand", ""), pack=p.get("pack", ""))
                  for sku, p in rows]
    counter = OnePassCounter(product["sku"])
    after = s.frames.last_ts("identify")
    loop = asyncio.get_running_loop()
    started = loop.time()
    await s.send(RequestFrameMsg(purpose="identify", continuous=True))
    try:
        while loop.time() - started < COUNT_MAX_S:
            got = await s.frames.wait_for(count=1, purpose="identify", after_ts=after, timeout_s=COUNT_IDLE_S)
            if not got:
                break                                           # the owner stopped showing packs
            frame = got[0]
            after = frame.ts
            res = await vid.identify_product(frame, candidates)
            if counter.observe(frame.ts, res.sku, res.confidence):
                await s.send(CountMsg(sku=product["sku"], counted=counter.counted, expected=expected_packs,
                                      final=False, confidence=res.confidence))
                if expected_packs is not None and counter.counted >= expected_packs:
                    break
    finally:
        await s.send(RequestFrameMsg(purpose="identify", stop=True))
    return counter.counted, counter.confidence


async def delivery_count(sku: str, mode: Literal["one_by_one", "crate"] = "one_by_one") -> dict:
    """Count the delivered packs of one product with the camera, one by one or as a crate.

    one_by_one: the owner shows each pack, then lowers it. crate: one look at a full crate or shelf.
    Streams count messages, reads the count back for confirmation and records only the verified quantity.
    """
    s = get_session()
    d = await _current_delivery(s)
    if d is None:
        return {"ok": False, "say": t("k2.delivery.none", s.lang)}
    real_sku = await _resolve_sku(s, sku, d)
    if real_sku is None:
        return {"ok": False, "say": hints.say("PRODUCT_UNKNOWN", s.lang, product=sku)}
    product = await _product(s.shop_id, real_sku)
    name = product.get("name") or real_sku
    per_pack, unit = catalog.to_selling_units(product, 1, "pack")
    line = next((i for i in d["items"] if i["sku"] == real_sku), None)
    expected = float(line["expectedQty"]) if line else None
    expected_packs = round(expected / per_pack) if expected is not None and per_pack else None

    if mode == "crate":
        frames = await s.request_frames("count", count=2, interval_ms=700)
        if len(frames) < 2:
            return {"ok": False, "needs_retake": True, "say": t("vision.aim.not_visible", s.lang)}
        reads = [await vcount.count_in_frame(f, name) for f in frames]
        for r in reads:
            if r.needs_retake or r.aim_hint:
                return {"ok": False, "needs_retake": True, "say": t(r.aim_hint or "vision.aim.show_items", s.lang)}
        if reads[0].count is None or reads[0].count != reads[1].count:      # two reads disagree: not sure
            return {"ok": False, "needs_retake": True, "say": t("vision.aim.spread_out", s.lang)}
        packs, conf = reads[0].count, min(r.confidence for r in reads)
    else:
        packs, conf = await _count_one_by_one(s, product, expected_packs)

    if packs == 0:
        return {"ok": False, "say": t("k2.delivery.nothing_seen", s.lang, product=name)}
    qty = catalog.to_selling_units(product, packs, "pack")[0]
    await s.send(CountMsg(sku=real_sku, counted=packs, expected=expected_packs, final=True, confidence=conf))
    if expected_packs is not None and packs != expected_packs:      # the video: "28 packs ... Two may be missing"
        key = "k2.delivery.readback_short" if packs < expected_packs else "k2.delivery.readback_over"
        prompt = t(key, s.lang, counted=packs, product=name, expected=expected_packs,
                   missing=expected_packs - packs, extra=packs - expected_packs)
    else:
        prompt = t("k2.delivery.readback", s.lang, counted=f"{fmt_qty(qty)} {unit}", product=name,
                   expected=f"{fmt_qty(expected)} {unit}" if expected is not None else "0")
    if line and line.get("countedQty") is not None and abs(float(line["countedQty"]) - qty) < 1e-9:
        prompt = f"{t('k2.delivery.still', s.lang, counted=packs)} {prompt}"   # "Count again." -> "Still 28."
    kind = "discrepancy" if expected is None or abs(qty - expected) > 1e-9 else "generic"
    payload = {"delivery_id": d["id"], "sku": real_sku, "qty": float(qty), "packs": packs,
               "expected_packs": expected_packs, "method": mode, "confidence": conf, "idem": ids.current_idem_key()}
    return await confirm.propose(kind, prompt, payload, "delivery.record_count")


def _saved_line(payload: dict, name: str, lang: str) -> str:
    """"Recorded: 28 packs received, 2 short." The packs are what the owner handled; litres stay in the ledger."""
    packs, expected = payload.get("packs"), payload.get("expected_packs")
    if packs is None:
        return t("k2.delivery.saved", lang, counted=fmt_qty(payload["qty"]), product=name)
    if expected is None or packs == expected:
        return t("k2.delivery.saved", lang, counted=packs, product=name)
    if packs < expected:
        return t("k2.delivery.saved_short", lang, counted=packs, missing=expected - packs)
    return t("k2.delivery.saved_over", lang, counted=packs, extra=packs - expected)


async def _mark_order(s: Session, d: dict) -> dict:
    """Once every ordered product is counted, the order says what the count found: DISCREPANCY when anything is
    short or extra (or an earlier bill check found a problem), else DELIVERY_RECONCILED. The vendor can then be paid
    without waiting for the bill, and the dashboard shows the shortfall at once."""
    order = await _repo.get_order(s.shop_id, d["orderId"])
    if order is None:
        return d
    counted = {i["sku"]: float(i["countedQty"]) for i in d["items"] if i.get("countedQty") is not None}
    found = [x for x in reconcile.compare(order["items"], counted, None).discrepancies if x["kind"] in COUNT_KINDS]
    kept = [x for x in d.get("discrepancies") or [] if x["kind"] not in COUNT_KINDS]   # an earlier bill check
    d = {**d, "discrepancies": found + kept}
    if order["state"] in INVOICE_STATES and all(it["sku"] in counted for it in order["items"]):
        target = "DISCREPANCY" if d["discrepancies"] else "DELIVERY_RECONCILED"
        if order["state"] != target:
            order = {**order, "state": transition("order", order["state"], target), "updatedAt": now_iso()}
            await _repo.save_order(s.shop_id, order)
            await outbox.order_state(s.shop_id, order["id"], target)
        d["state"] = target
    return d


async def _commit_count(payload: dict, value: str | None) -> dict:
    """Committer "delivery.record_count": the owner said yes to the count. Stock grows by the counted quantity;
    a recount of the same product corrects it by the difference instead of adding it again."""
    s = get_session()
    d = await _repo.get_delivery(s.shop_id, payload["delivery_id"])
    if d is None:
        return {"ok": False, "say": t("k2.delivery.none", s.lang)}
    product = await _product(s.shop_id, payload["sku"])
    name = product.get("name") or payload["sku"]
    saved = {"ok": True, "say": _saved_line(payload, name, s.lang)}
    line = next((i for i in d["items"] if i["sku"] == payload["sku"]), None)
    if line and line.get("countIdem") == payload["idem"]:       # retried confirm: nothing new
        return saved
    # ledger.apply is idempotent on the key too, so a crash between here and save_delivery cannot double stock.
    previous = float(line["countedQty"]) if line and line.get("countedQty") is not None else None
    if previous is None:
        await ledger.apply(s.shop_id, payload["sku"], payload["qty"], "DELIVERY", "delivery", d["id"], payload["idem"])
    elif abs(payload["qty"] - previous) > 1e-9:                 # "Count again": only the difference moves stock
        await ledger.apply(s.shop_id, payload["sku"], payload["qty"] - previous, "ADJUST", "delivery", d["id"],
                           payload["idem"])
    if line is None:
        line = {"sku": payload["sku"], "name": name, "unit": product.get("unit", "pack"), "expectedQty": 0.0}
        d["items"].append(line)
    line.update(countedQty=payload["qty"], method=payload["method"], confidence=payload["confidence"],
                countIdem=payload["idem"])
    d = await _mark_order(s, {**d, "updatedAt": now_iso()})
    await _repo.save_delivery(s.shop_id, d)
    if abs(payload["qty"] - float(line["expectedQty"])) > 1e-9:
        order = await _repo.get_order(s.shop_id, d["orderId"])
        ordered = [it for it in (order or {}).get("items", []) if it["sku"] == payload["sku"]]
        await outbox.discrepancies(s.shop_id, d["id"],
                                   reconcile.compare(ordered, {payload["sku"]: payload["qty"]}, None))
    await audit.log(s.shop_id, "owner", "delivery_counted", "delivery", d["id"], f"{payload['sku']}={payload['qty']}")
    return saved


async def _say_discrepancies(s: Session, items: list[dict]) -> str:
    out = []
    for item in items[:2]:                                      # keep it short: the first two problems
        name = item.get("name", "")
        if item.get("sku"):
            name = (await _product(s.shop_id, item["sku"])).get("name") or name
        out.append(hints.say(item["kind"], s.lang, product=name, name=name,
                             expected=fmt_qty(item["expected"]) if item.get("expected") is not None else "",
                             actual=fmt_qty(item["actual"]) if item.get("actual") is not None else ""))
    return " ".join(out)


async def invoice_check(order_id: str) -> dict:
    """Read the paper invoice twice and reconcile it with the order and the verified counts."""
    s = get_session()
    order = (await _repo.get_order(s.shop_id, order_id) if order_id
             else await _repo.latest_order(s.shop_id, INVOICE_STATES))
    if order is None:
        return {"ok": False, "say": t("k2.order.none", s.lang)}
    d = await _repo.delivery_for_order(s.shop_id, order["id"])
    if d is None or order["state"] not in INVOICE_STATES:
        return {"ok": False, "say": t("k2.delivery.none", s.lang)}
    frames = await s.request_frames("invoice", count=2, interval_ms=600)
    if not frames:
        return {"ok": False, "needs_retake": True, "say": t("vision.aim.show_label", s.lang)}
    inv = await vinv.read_invoice(frames)
    if not inv.agreed:                                          # two reads disagree: never use the bill
        return {"ok": False, "needs_retake": True, "say": hints.say("INVOICE_UNREADABLE", s.lang)}
    counted = {i["sku"]: float(i["countedQty"]) for i in d["items"] if i.get("countedQty") is not None}
    result = reconcile.compare(order["items"], counted, inv)
    target = "DELIVERY_RECONCILED" if result.status == "match" else "DISCREPANCY"
    now = now_iso()
    if order["state"] != target:
        order = {**order, "state": transition("order", order["state"], target), "updatedAt": now}
        await _repo.save_order(s.shop_id, order)
        await outbox.order_state(s.shop_id, order["id"], target)
    d = {**d, "state": target, "discrepancies": result.discrepancies, "updatedAt": now,
         "invoice": {"supplier": inv.supplier, "date": inv.date, "total": inv.total, "agreed": inv.agreed,
                     "lines": [ln.model_dump() for ln in inv.lines]}}
    await _repo.save_delivery(s.shop_id, d)
    invoice_only = [x for x in result.discrepancies if x["kind"] not in COUNT_KINDS]
    if invoice_only:
        await outbox.discrepancies(s.shop_id, d["id"],
                                   reconcile.ReconcileResult(status="discrepancy", discrepancies=invoice_only))
    await audit.log(s.shop_id, "aira", "invoice_checked", "delivery", d["id"], target)
    await s.send(StateMsg(entity="delivery", id=d["id"], state=target, summary=order.get("supplierName", "")))
    if result.status == "match":
        await s.send(CueMsg(cue="match"))
        say = t("k2.invoice.match", s.lang, total=inv.total)
    else:
        await s.send(CueMsg(cue="mismatch"))
        # What the bill got wrong first; the count's own shortfall was already said when it was counted.
        say = await _say_discrepancies(s, invoice_only + [x for x in result.discrepancies if x["kind"] in COUNT_KINDS])
        due = reconcile.payable(order["items"], counted)
        if inv.total is not None and due and int(inv.total) != int(due):   # "The bill should be ₹980."
            say = f"{say} {t('k2.invoice.should_be', s.lang, total=inv.total, payable=due)}"
    return {"ok": True, "orderId": order["id"], "state": target, "discrepancies": result.discrepancies, "say": say}


confirm.register_committer("delivery.record_count", _commit_count)
