"""Catalog by voice: product_add / product_update / product_disable / stock_add / supplier_add.

Everything real runs on a MemoryStore seeded from the demo catalog, with the real confirm/session/ids. Each tool
proposes first and commits only through confirm.resolve("yes"), in a transaction, with an audit entry.
"""
from pathlib import Path

import pytest

from aira_live import confirm
from aira_live.business import catalog, ledger, outbox, paths, seed
from aira_live.business.store import MemoryStore, set_store
from aira_live.contracts import ConfirmMsg
from aira_live.i18n import t
from aira_live.session import Session, current_session, current_tool_call_id
from aira_live.tools import TOOLS_BY_NAME
from aira_live.tools import catalog as cat
from aira_live.tools import stock

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "shared" / "catalog" / "sunrise_dairy.json"
SHOP = "sunrise_dairy"
SECRET = "test-webhook-secret"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AIRA_DEMO_SHOP", SHOP)
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_USERNAME", raising=False)


@pytest.fixture(autouse=True)
def published():
    sent: list[tuple[str, dict]] = []

    async def fake(topic: str, payload: dict) -> str:
        sent.append((topic, payload))
        return f"fake-{len(sent)}"

    outbox.set_publisher(fake)
    yield sent
    outbox.set_publisher(None)


@pytest.fixture
async def store():
    s = MemoryStore()
    set_store(s)
    await seed.seed_into(s, seed.load_catalog(CATALOG), env={})
    yield s
    set_store(None)


class _Phone:
    def __init__(self):
        self.msgs: list = []

    async def __call__(self, msg) -> None:
        self.msgs.append(msg)

    def of(self, kind: type) -> list:
        return [m for m in self.msgs if isinstance(m, kind)]


@pytest.fixture
def phone():
    return _Phone()


@pytest.fixture
def session(phone):
    s = Session("owner1", SHOP, "en", "short", phone)
    tok_s, tok_c = current_session.set(s), current_tool_call_id.set("tc-1")
    yield s
    current_tool_call_id.reset(tok_c)
    current_session.reset(tok_s)


def use_call(call_id: str) -> None:
    current_tool_call_id.set(call_id)


@pytest.fixture
def proposals(monkeypatch):
    made: list[dict] = []
    real = confirm.propose
    previous = confirm._store
    confirm.set_store(confirm.MemoryProposalStore())

    async def recording(kind, prompt, payload, committer):
        out = await real(kind, prompt, payload, committer)
        made.append({"kind": kind, "prompt": prompt, "payload": payload, "committer": committer,
                     "confirm_id": out["confirm_id"]})
        return out

    monkeypatch.setattr(confirm, "propose", recording)
    yield made
    confirm.set_store(previous)


async def _audits(store, action: str) -> list[dict]:
    return [d for _, d in await store.list(f"shops/{SHOP}/auditLogs") if d["action"] == action]


async def _yes(r: dict) -> dict:
    return await confirm.resolve(r["confirm_id"], "yes")


# ---------------------------------------------------------------------------------------------------- registry

def test_catalog_tools_are_registered_and_confirm_gated():
    for name in ("product_add", "product_update", "product_disable", "stock_add", "supplier_add"):
        assert name in TOOLS_BY_NAME
    for name in ("catalog.product_add", "catalog.product_update", "catalog.product_disable",
                 "catalog.stock_add", "catalog.supplier_add"):
        assert name in confirm._COMMITTERS


# ---------------------------------------------------------------------------------------------------- product_add

async def test_product_add_asks_for_missing_price_then_unit(store, session, proposals):
    r = await cat.product_add("lassi")
    assert r["ok"] is False and r["missing"] == "price_rupees"
    assert r["say"] == t("k3.product.price_q", "en", product="lassi")
    r = await cat.product_add("lassi", price_rupees=20)
    assert r["ok"] is False and r["missing"] == "unit"
    assert r["say"] == t("k3.product.unit_q", "en", product="lassi")
    r = await cat.product_add("lassi", price_rupees=20, unit="bottle")
    assert r["ok"] is False and r["missing"] == "unit"
    assert proposals == []


async def test_product_add_existing_product_asks_stock_or_new(store, session, proposals):
    r = await cat.product_add("buttermilk")
    assert r["ok"] is False and r["exists"] == "aavin_buttermilk_200ml"
    assert r["say"] == t("k3.product.exists_q", "en", product="Aavin Buttermilk 200 ml")
    assert proposals == []


async def test_product_add_proposes_then_commits_in_txn_with_audit(store, session, proposals, phone):
    r = await cat.product_add("lassi", brand="Aavin", unit="pack", pack_size="200 ml", price_rupees=20,
                              supplier_id="aavin_vendor")
    assert r["needs_confirm"] is True
    p = proposals[0]
    assert p["committer"] == "catalog.product_add" and p["payload"]["sku"] == "aavin_lassi_200ml"
    assert p["prompt"] == t("k3.product.add_q", "en", product="Aavin Lassi 200 ml", price=20, unit="pack")
    assert phone.of(ConfirmMsg)[-1].prompt == p["prompt"]
    assert await store.get(paths.product(SHOP, "aavin_lassi_200ml")) is None     # nothing written before yes

    done = await _yes(r)
    assert done["ok"] is True and done["sku"] == "aavin_lassi_200ml"
    assert done["say"] == t("k3.product.added", "en", product="Aavin Lassi 200 ml")
    doc = await store.get(paths.product(SHOP, "aavin_lassi_200ml"))
    assert doc["name"] == "Aavin Lassi 200 ml" and doc["brand"] == "Aavin" and doc["unit"] == "pack"
    assert doc["price"] == 20 and isinstance(doc["price"], int) and doc["packSizeL"] == 0.2
    assert doc["active"] is True and doc["sku"] == "aavin_lassi_200ml"
    assert "lassi" in doc["aliases"]["en"] and doc["aliases"]["ta"] == []
    sup = await store.get(paths.supplier(SHOP, "aavin_vendor"))
    assert "aavin_lassi_200ml" in sup["products"]
    logs = await _audits(store, "product_added")
    assert len(logs) == 1 and logs[0]["refId"] == "aavin_lassi_200ml" and logs[0]["actor"] == "owner"
    # the new product resolves by voice right away (no stale list)
    ref = await catalog.resolve(SHOP, "lassi", "en")
    assert ref is not None and ref.sku == "aavin_lassi_200ml"


async def test_product_add_tamil_name_gets_ta_alias_and_a_safe_sku(store, session, proposals):
    r = await cat.product_add("மோர் குழம்பு", unit="pack", price_rupees=15)
    sku = proposals[0]["payload"]["sku"]
    paths.doc_id(sku)                                     # a valid Firestore id, never Tamil letters
    await _yes(r)
    doc = await store.get(paths.product(SHOP, sku))
    assert "மோர் குழம்பு" in doc["aliases"]["ta"]
    assert (await catalog.resolve(SHOP, "மோர் குழம்பு", "ta")).sku == sku


async def test_product_add_duplicate_sku_is_refused(store, session, proposals):
    r = await cat.product_add("Buttermilk", brand="Aavin", unit="pack", pack_size="200 ml", price_rupees=10)
    assert r["ok"] is False and r["error"] == "duplicate_sku" and proposals == []
    assert r["say"] == t("k3.product.duplicate", "en", product="Aavin Buttermilk 200 ml")


async def test_product_add_commit_is_idempotent(store, session, proposals):
    r = await cat.product_add("lassi", unit="pack", price_rupees=20)
    await _yes(r)
    again = await _yes(r)                                  # a repeated yes: no second write, no second audit
    assert again["status"] == "committed"
    assert len(await _audits(store, "product_added")) == 1


# ---------------------------------------------------------------------------------------------------- product_update

async def test_product_update_changes_price_after_yes(store, session, proposals):
    r = await cat.product_update("curd", price_rupees=65)
    assert r["needs_confirm"] is True and proposals[0]["committer"] == "catalog.product_update"
    assert (await store.get(paths.product(SHOP, "sakthi_curd_1l")))["price"] == 60
    done = await _yes(r)
    assert done["ok"] is True
    doc = await store.get(paths.product(SHOP, "sakthi_curd_1l"))
    assert doc["price"] == 65 and doc["name"] == "Sakthi Curd 1 L"
    assert len(await _audits(store, "product_updated")) == 1


async def test_product_update_unknown_or_nothing(store, session, proposals):
    r = await cat.product_update("rocket fuel", price_rupees=5)
    assert r["ok"] is False and proposals == []
    r = await cat.product_update("curd")
    assert r["ok"] is False and r["say"] == t("k3.product.nothing", "en", product="Sakthi Curd 1 L")


# ---------------------------------------------------------------------------------------------------- product_disable

async def test_product_disable_keeps_doc_and_excludes_from_resolution(store, session, proposals):
    inv_before = await ledger.apply(SHOP, "arun_icecream_box", 3, "ADJUST", "seed", "seed", "k-ice")
    assert inv_before["onHand"] == 3
    r = await cat.product_disable("ice cream")
    assert r["needs_confirm"] is True
    assert (await catalog.resolve(SHOP, "ice cream", "en")).sku == "arun_icecream_box"   # not yet
    done = await _yes(r)
    assert done["ok"] is True
    doc = await store.get(paths.product(SHOP, "arun_icecream_box"))
    assert doc["active"] is False and doc["name"] == "Arun Ice Cream Family Box"
    assert await store.get(paths.inventory(SHOP, "arun_icecream_box"))         # history kept
    assert await catalog.resolve(SHOP, "ice cream", "en") is None               # sale / order resolution
    assert await catalog.resolve_supplier(SHOP, "ice cream", "en") is None
    listing = await stock.stock_query()
    assert all(i["sku"] != "arun_icecream_box" for i in listing["items"])
    assert len(await _audits(store, "product_disabled")) == 1
    # disabling twice: nothing more to do
    assert (await cat.product_disable("arun_icecream_box"))["ok"] is False


# ---------------------------------------------------------------------------------------------------- stock_add

async def test_stock_add_writes_a_receive_ledger_entry(store, session, proposals, published):
    r = await cat.stock_add("curd", 10, "L")
    assert r["needs_confirm"] is True and proposals[0]["committer"] == "catalog.stock_add"
    assert proposals[0]["prompt"] == t("k3.stock.add_q", "en", qty="10 L", product="Sakthi Curd 1 L")
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 0
    done = await _yes(r)
    assert done["ok"] is True and done["qty"] == 10.0
    assert await ledger.on_hand(SHOP, "sakthi_curd_1l") == 10
    rows = [d for _, d in await store.list(f"shops/{SHOP}/ledger")]
    assert len(rows) == 1 and rows[0]["refType"] == "receive" and rows[0]["delta"] == 10.0
    assert rows[0]["sku"] == "sakthi_curd_1l" and rows[0]["actor"] == "owner"
    assert len(await _audits(store, "stock_received")) == 1
    assert published and published[-1][0] == "inventory-movements" and published[-1][1]["delta"] == 10.0
    again = await _yes(r)
    assert again["status"] == "committed" and await ledger.on_hand(SHOP, "sakthi_curd_1l") == 10


async def test_stock_add_converts_packs_and_rejects_bad_input(store, session, proposals):
    r = await cat.stock_add("aavin milk", 4, "packets")
    assert proposals[0]["payload"]["qty"] == 2.0 and proposals[0]["payload"]["unit"] == "L"
    r = await cat.stock_add("curd", 0)
    assert r["ok"] is False
    r = await cat.stock_add("curd", 2.5, "pack")
    assert r["ok"] is False
    r = await cat.stock_add("rocket fuel", 2)
    assert r["ok"] is False and r["say"] == t("k3.stock.unknown_q", "en", product="rocket fuel")


# ---------------------------------------------------------------------------------------------------- supplier_add

async def test_supplier_add_telegram_replies_with_signed_link(store, session, proposals, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "aira_bot")
    from aira_live.messaging.webhooks import start_param

    r = await cat.supplier_add("Hatsun agent", "telegram")
    assert r["needs_confirm"] is True and proposals[0]["committer"] == "catalog.supplier_add"
    assert proposals[0]["payload"]["supplier_id"] == "hatsun_agent"
    done = await _yes(r)
    assert done["ok"] is True and done["supplierId"] == "hatsun_agent"
    param = start_param("hatsun_agent")
    assert done["startParam"] == param and done["link"] == f"https://t.me/aira_bot?start={param}"
    assert done["link"] in done["say"]
    doc = await store.get(paths.supplier(SHOP, "hatsun_agent"))
    assert doc["name"] == "Hatsun agent" and doc["channel"] == "telegram" and doc["products"] == []
    assert doc["phone"] == "" and "hatsun agent" in doc["aliases"]["en"]
    assert (await catalog.resolve_supplier(SHOP, "hatsun agent", "en"))["id"] == "hatsun_agent"
    assert len(await _audits(store, "supplier_added")) == 1


async def test_supplier_add_without_secret_says_no_link(store, session, proposals):
    r = await cat.supplier_add("Hatsun agent", "telegram")
    done = await _yes(r)
    assert done["ok"] is True and done.get("link") is None
    assert t("k3.supplier.no_link", "en") in done["say"]


async def test_supplier_add_duplicate_and_bad_channel(store, session, proposals):
    r = await cat.supplier_add("Aavin vendor", "call")
    assert r["ok"] is False and r["error"] == "duplicate_supplier"
    r = await cat.supplier_add("Hatsun", "pigeon")
    assert r["ok"] is False and r["say"] == t("k3.supplier.channel_q", "en", supplier="Hatsun")
    assert proposals == []
