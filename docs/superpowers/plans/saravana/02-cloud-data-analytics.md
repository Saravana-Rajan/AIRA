# AIRA — Cloud Data, Analytics & Ops Implementation Plan (Saravana · 02)

> **Addendum (2026-10-10): full dairy range.** The 30-day demo sales backfill and the forecast cover ALL SKUs:
> `aavin_milk_500ml`, `aavin_milk_1l`, `sakthi_curd_500ml`, `sakthi_curd_1l`, `aavin_buttermilk_200ml`, `aavin_ghee_200ml`, `aavin_ghee_500ml`, `aavin_paneer_200g`, `aavin_butter_100g`, `arun_icecream_box`.
>
> Plausible daily bases for the backfill:
> - milk and curd: high
> - buttermilk: medium
> - paneer, butter and ghee: low
> - ice cream: weekend-heavy

> **Addendum (2026-10-10): deploy `aira-live` with `--max-instances 1`.** The live-session registry (`sessions.notify`) is in-process, which `saravana/01` requires. Use `--min-instances 1 --max-instances 1` in every deploy command, including `infra/bootstrap.sh` and `cloudbuild.yaml`, and add a smoke-test assertion that the service config shows maxScale 1. One instance handles the single demo shop easily. Moving the registry to Firestore or Pub/Sub for multi-instance is a roadmap item.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AIRA's data layer production-shaped. That means:
- locked-down Firestore rules and indexes
- every business event flowing through Pub/Sub into BigQuery
- a demand forecast (BigQuery ML ARIMA_PLUS with a 7-day moving-average fallback) that powers `day_summary` and `reorder_suggest`
- scheduled expiry and day-summary jobs that speak to Murugan and alert his family over FCM
- a Remote Config model switch
- CI/CD, uptime monitoring and a budget alert

**Architecture:**
- **Firestore** is the system of record. The server writes through the Admin SDK, so rules deny every client write; clients only read their own shop, and the `murugan_dairy` demo shop is readable by any signed-in user for judges.
- **Events:**
  - Services publish small JSON events to five Pub/Sub topics *after* their Firestore transaction commits.
  - **BigQuery subscriptions** (`--use-table-schema`) land those events in dataset `aira` with no extra code.
  - Dedup views make the analytics robust to at-least-once delivery.
- **Forecasts** query BigQuery from `aira-live`.
- **Scheduled jobs:** Cloud Scheduler calls OIDC-protected `/jobs/*` routes on `aira-live`.
- **Remote Config** is read server-side every 60 s and can switch the pointing and counting model with no redeploy.

**Tech Stack:**

| Area | Tools |
|---|---|
| Server | Python 3.12, FastAPI, google-cloud-firestore (async), google-cloud-pubsub, google-cloud-bigquery, firebase-admin (auth, messaging), google-auth, pytest + pytest-asyncio |
| Rules tests | Node 20, `@firebase/rules-unit-testing`, Vitest, Firebase Emulator Suite |
| Ops | gcloud / bq CLIs, Cloud Build, Cloud Monitoring |

**Spec:** `docs/superpowers/specs/2026-10-10-aira-design.md` (§4 Architecture, §5 Data model, §6 Safety, §7 Benchmarks). **Interfaces:** `docs/superpowers/plans/2026-10-10-00-interfaces.md`.

**Prerequisite:** `2026-10-10-00-foundation.md` is merged. It provides `aira_live.config`, `db()`, `session`, `sessions.notify`, `i18n.t`, `main.py`, `models.py`, `firebase.json`, and the GCP bootstrap.

## Schedule

| Day | Date | Task |
|---|---|---|
| D2 | Sun Oct 11 | Task 1 (Firestore rules + indexes), Task 2 (Pub/Sub + BigQuery + `events.publish`) |
| D3 | Mon Oct 12 | Task 3 (forecast + demo backfill + seed) |
| D4 | Tue Oct 13 | Task 4 (`summary.today` + `tools/insights.py`), Task 5 (jobs + FCM + Scheduler) |
| D5 | Wed Oct 14 | Task 6 (Remote Config, secrets, Cloud Build, uptime + budget) |

## Global Constraints

- Product name in user-facing copy: **AIRA**. The previous multi-pillar concept is dropped.
- **Firestore is the system of record.**
  - All writes come from the server (Admin SDK). Client rules allow **reads only**.
  - Ledger documents are append-only: `business.ledger` never updates or deletes them.
- Shop data lives under `shops/{shopId}/…`, with field names exactly as in spec §5. The demo shop id is `murugan_dairy` (judge-readable); demo SKUs: `aavin_milk_500ml`, `aavin_milk_1l`, `sakthi_curd_500ml`, `sakthi_curd_1l`, `arun_icecream_box`.
- **Model IDs** live only in `services/live/aira_live/models.py`. Remote Config values are validated against `models.DEFAULTS` / `models.FALLBACKS`.
- **Pub/Sub topics are exactly:** `sales`, `inventory-movements`, `orders`, `discrepancies`, `alerts`.
  - Each maps to BigQuery table `aira.<topic with - → _>`.
  - Events are published **after** the Firestore transaction commits.
- **Time:** all business days are in `Asia/Kolkata`. GCP region `asia-south1` (Cloud Run, Firestore, Cloud Storage, Cloud Scheduler); BigQuery dataset `aira` location `asia-south1`.
- **Secrets** live in Secret Manager: `gemini-api-key`, `whatsapp-token`, `whatsapp-verify-token`, `telegram-token`. Never commit any key.
- **Jobs and alerts:**
  - Scheduler-invoked routes verify a Google OIDC token: audience = service URL, email = the scheduler service account.
  - FCM goes only to users with `role == "family"` of that shop.
- **Python tests:** `cd services/live && uv run pytest -q`. **Rules tests:** `cd infra/firestore && npm test` under the emulator, as in Task 1.

## Review Focus

1. **A malformed or incomplete event payload** (for example a sale without `sku`) must raise `EventError` before publishing. A missing required column must never silently land in BigQuery. *(Task 2: `test_missing_required_field_raises`, `test_required_keys_exist_in_schemas`)*
2. **A stranger, or an unauthenticated client,** trying to read another shop or write *any* document (including the ledger) must be denied. *(Task 1: `stranger cannot read`, `no client can create/update/delete ledger`)*
3. **Thin history:**
   - Fewer than 14 days of data, or an ARIMA query failure → the 7-day moving average.
   - Zero history → `None`, and `reorder_suggest` says it doesn't have enough data instead of inventing a number.

   *(Task 3: `test_choose_falls_back_*`; Task 4: `test_reorder_suggest_no_history_says_not_enough_data`)*
4. **A typo in Remote Config** (an unknown model name) is ignored; the default model is used and a warning logged. *(Task 6: `test_unknown_model_override_ignored`)*
5. **A `/jobs/*` call without a valid scheduler OIDC token** returns 401/403 and sends **no** notifications. *(Task 5: `test_jobs_reject_missing_token`, `test_jobs_reject_wrong_email`)*

---

## File Structure

```
infra/
  firestore/firestore.rules              client read-only rules (Task 1)
  firestore/firestore.indexes.json       composite indexes (Task 1)
  firestore/package.json  vitest.config.ts  tests/rules.test.ts   emulator rules tests (Task 1)
  bigquery/schemas/{sales,inventory_movements,orders,discrepancies,alerts}.json   (Task 2)
  bigquery/views.sql                     dedup views (Task 2)
  pubsub_bigquery.sh                     topics + dataset + tables + BigQuery subscriptions (Task 2)
  scheduler.sh                           scheduler SA + 3 jobs (Task 5)
  remote-config/template.json            Remote Config params (Task 6)
  secrets.sh  monitoring.sh  monitoring/uptime-alert.json  budget.sh   (Task 6)
cloudbuild.yaml                          CI/CD (Task 6)
firebase.json                            + firestore, emulators, remoteconfig keys (Tasks 1, 6)
services/live/
  aira_live/analytics/__init__.py
  aira_live/analytics/events.py          publish() + EventError (Task 2)
  aira_live/analytics/forecast.py        daily_demand(), choose(), moving_average_7(), refresh_model() (Task 3)
  aira_live/analytics/summary.py         summarize(), suggest_qty(), today() (Task 4)
  aira_live/tools/insights.py            day_summary(), reorder_suggest() (Task 4)
  aira_live/jobs.py                      /jobs/expiry, /jobs/day-summary, /jobs/forecast-refresh (Task 5)
  aira_live/fcm.py                       /fcm/register, register_token(), send_to_family() (Task 5)
  aira_live/remote_config.py             60 s cached server-side Remote Config (Task 6)
  aira_live/models.py                    (modify) env > Remote Config > default (Task 6)
  aira_live/main.py                      (modify) include jobs + fcm routers, start RC refresher (Tasks 5, 6)
  aira_live/i18n/{en,ta,hi}.json         (modify) insights.* and jobs.* keys (Tasks 4, 5)
  scripts/backfill_demo_sales.py         30 days of plausible demo sales → BigQuery (Task 3)
  scripts/seed_firestore.py              demo shop seed from content/catalog (Task 3)
  tests/analytics/test_events.py  test_forecast.py  test_summary.py
  tests/test_insights.py  test_jobs.py  test_fcm.py  test_remote_config.py  test_infra_files.py
  tests/scripts/test_backfill.py  test_seed.py
```

---

### Task 1: Firestore security rules, composite indexes, emulator rules tests

**Files:**
- Create: `infra/firestore/firestore.rules`, `infra/firestore/firestore.indexes.json`, `infra/firestore/package.json`, `infra/firestore/vitest.config.ts`, `infra/firestore/tests/rules.test.ts`
- Modify: `firebase.json` (add `firestore` + `emulators` keys)

**Interfaces:**
- Consumes: spec §5 collection layout; `users/{uid}` has `shopId`, `role` ∈ {owner, family, demo}.
- Produces:
  - **Rule contract:** members read `shops/{shopId}/**`; any signed-in user reads `shops/murugan_dairy/**`; no client writes anywhere; users read only their own `users/{uid}` and `users/{uid}/fcmTokens/*`.
  - **Composite indexes:** `ledger(sku ASC, at DESC)`, `sales(state ASC, createdAt DESC)`, `orders(state ASC, updatedAt DESC)`, `payments(refType ASC, refId ASC)`, `users(shopId ASC, role ASC)`.
  - **Cross-team requirement (Kanish):** `business.sales.create` sets `createdAt`, and `complete` sets `completedAt`. Both are server timestamps; Task 4 queries `createdAt`.

- [ ] **Step 1: Create the rules-test package**

`infra/firestore/package.json`:

```json
{
  "name": "aira-firestore-rules",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "vitest run",
    "emu:test": "firebase emulators:exec --only firestore --project demo-aira \"vitest run\""
  },
  "devDependencies": {
    "@firebase/rules-unit-testing": "^4.0.0",
    "firebase": "^11.0.0",
    "firebase-tools": "^13.0.0",
    "vitest": "^2.1.0"
  }
}
```

Verify the current major versions at https://www.npmjs.com/package/@firebase/rules-unit-testing.

`infra/firestore/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";
export default defineConfig({ test: { include: ["tests/**/*.test.ts"], testTimeout: 20000, fileParallelism: false } });
```

Add these keys to the root `firebase.json`. Keep the existing `hosting` key from the foundation; don't overwrite it:

```json
"firestore": { "rules": "infra/firestore/firestore.rules", "indexes": "infra/firestore/firestore.indexes.json" },
"emulators": { "firestore": { "port": 8085 }, "ui": { "enabled": false } }
```

- [ ] **Step 2: Write the failing rules tests** in `infra/firestore/tests/rules.test.ts`

```ts
import { initializeTestEnvironment, assertSucceeds, assertFails, type RulesTestEnvironment } from "@firebase/rules-unit-testing";
import { readFileSync } from "node:fs";
import { doc, getDoc, setDoc, updateDoc, deleteDoc } from "firebase/firestore";
import { beforeAll, afterAll, beforeEach, describe, it } from "vitest";

let env: RulesTestEnvironment;
// private_shop = a normal member-only shop; murugan_dairy = the judge-readable demo shop.
const P = "shops/private_shop/products/sakthi_curd_1l";
const L = "shops/private_shop/ledger/t1";
const DEMO = "shops/murugan_dairy/products/aavin_milk_500ml";

beforeAll(async () => {
  env = await initializeTestEnvironment({
    projectId: "demo-aira",
    firestore: { rules: readFileSync("firestore.rules", "utf8"), host: "127.0.0.1", port: 8085 },
  });
});
afterAll(async () => env.cleanup());
beforeEach(async () => {
  await env.clearFirestore();
  await env.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();
    await setDoc(doc(db, "users/owner1"), { shopId: "private_shop", role: "owner", lang: "ta" });
    await setDoc(doc(db, "users/family1"), { shopId: "private_shop", role: "family", lang: "ta" });
    await setDoc(doc(db, "users/stranger"), { shopId: "other", role: "owner", lang: "en" });
    await setDoc(doc(db, "users/owner1/fcmTokens/tok1"), { platform: "web" });
    await setDoc(doc(db, "shops/private_shop"), { name: "Private Shop" });
    await setDoc(doc(db, P), { name: "Sakthi curd", price: 60 });
    await setDoc(doc(db, L), { sku: "sakthi_curd_1l", delta: 30, reason: "DELIVERY" });
    await setDoc(doc(db, "shops/murugan_dairy"), { name: "Murugan Dairy" });
    await setDoc(doc(db, DEMO), { name: "Aavin milk" });
    await setDoc(doc(db, "idempotency/k1"), { result: {} });
  });
});

const as = (uid: string) => env.authenticatedContext(uid).firestore();

describe("shop reads", () => {
  it("owner reads own shop product", async () => { await assertSucceeds(getDoc(doc(as("owner1"), P))); });
  it("family reads ledger", async () => { await assertSucceeds(getDoc(doc(as("family1"), L))); });
  it("stranger cannot read another shop", async () => { await assertFails(getDoc(doc(as("stranger"), P))); });
  it("any signed-in user (judge, no users doc) reads the demo shop murugan_dairy", async () => {
    await assertSucceeds(getDoc(doc(as("judge-anon"), DEMO)));
  });
  it("unauthenticated cannot read the demo shop", async () => {
    await assertFails(getDoc(doc(env.unauthenticatedContext().firestore(), DEMO)));
  });
  it("no client can write the demo shop either", async () => {
    await assertFails(updateDoc(doc(as("judge-anon"), DEMO), { name: "x" }));
  });
});

describe("no client writes", () => {
  it("no client can create/update/delete ledger", async () => {
    const db = as("owner1");
    await assertFails(setDoc(doc(db, "shops/murugan/ledger/t2"), { sku: "x", delta: -1 }));
    await assertFails(updateDoc(doc(db, L), { delta: 99 }));
    await assertFails(deleteDoc(doc(db, L)));
  });
  it("owner cannot change a product price", async () => { await assertFails(updateDoc(doc(as("owner1"), P), { price: 1 })); });
  it("idempotency collection is server-only", async () => { await assertFails(getDoc(doc(as("owner1"), "idempotency/k1"))); });
});

describe("users", () => {
  it("reads own user doc and own fcm tokens", async () => {
    await assertSucceeds(getDoc(doc(as("owner1"), "users/owner1")));
    await assertSucceeds(getDoc(doc(as("owner1"), "users/owner1/fcmTokens/tok1")));
  });
  it("cannot read another user's doc", async () => { await assertFails(getDoc(doc(as("stranger"), "users/owner1"))); });
  it("cannot escalate own role", async () => { await assertFails(updateDoc(doc(as("family1"), "users/family1"), { role: "owner" })); });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd infra/firestore && npm install && npm run emu:test`
Expected: FAIL. `firestore.rules` doesn't exist yet (ENOENT), so no test runs.

- [ ] **Step 4: Write `infra/firestore/firestore.rules`**

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    function signedIn() { return request.auth != null; }
    function userPath() { return /databases/$(database)/documents/users/$(request.auth.uid); }
    function isMember(shopId) {
      return signedIn()
        && exists(userPath())
        && get(userPath()).data.shopId == shopId
        && get(userPath()).data.role in ['owner', 'family', 'demo'];
    }
    // murugan_dairy is the public demo shop judges open from the deployed link (anonymous auth).
    function canReadShop(shopId) { return (signedIn() && shopId == 'murugan_dairy') || isMember(shopId); }

    // Users: read own doc + own FCM tokens. All writes go through aira-live (Admin SDK).
    match /users/{uid} {
      allow read: if signedIn() && request.auth.uid == uid;
      allow write: if false;
      match /fcmTokens/{token} {
        allow read: if signedIn() && request.auth.uid == uid;
        allow write: if false;
      }
    }

    // Shop data: read for members (and anyone signed-in for the demo shop). Never client-writable.
    match /shops/{shopId} {
      allow read: if canReadShop(shopId);
      allow write: if false;
      match /{document=**} {
        allow read: if canReadShop(shopId);
        allow write: if false;
      }
    }

    // Server-only bookkeeping.
    match /idempotency/{key} { allow read, write: if false; }
  }
}
```

- [ ] **Step 5: Write `infra/firestore/firestore.indexes.json`**

```json
{
  "indexes": [
    { "collectionGroup": "ledger", "queryScope": "COLLECTION",
      "fields": [ { "fieldPath": "sku", "order": "ASCENDING" }, { "fieldPath": "at", "order": "DESCENDING" } ] },
    { "collectionGroup": "sales", "queryScope": "COLLECTION",
      "fields": [ { "fieldPath": "state", "order": "ASCENDING" }, { "fieldPath": "createdAt", "order": "DESCENDING" } ] },
    { "collectionGroup": "orders", "queryScope": "COLLECTION",
      "fields": [ { "fieldPath": "state", "order": "ASCENDING" }, { "fieldPath": "updatedAt", "order": "DESCENDING" } ] },
    { "collectionGroup": "payments", "queryScope": "COLLECTION",
      "fields": [ { "fieldPath": "refType", "order": "ASCENDING" }, { "fieldPath": "refId", "order": "ASCENDING" } ] },
    { "collectionGroup": "users", "queryScope": "COLLECTION",
      "fields": [ { "fieldPath": "shopId", "order": "ASCENDING" }, { "fieldPath": "role", "order": "ASCENDING" } ] }
  ],
  "fieldOverrides": []
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd infra/firestore && npm run emu:test`
Expected: `Test Files 1 passed`, `Tests 12 passed`.

- [ ] **Step 7: Deploy the rules and indexes**

Run: `npx firebase-tools deploy --only firestore --project "$PROJECT"`
Expected: `✔ firestore: released rules … ✔ firestore: deployed indexes`. Index builds can take a few minutes; check the Firebase console → Firestore → Indexes.

- [ ] **Step 8: Commit**

```bash
git add infra/firestore firebase.json
git commit -m "feat(infra): Firestore read-only client rules, composite indexes, emulator rules tests"
```

---

### Task 2: Pub/Sub topics, BigQuery dataset/tables/subscriptions, `analytics.events.publish`

**Files:**
- Create: `infra/bigquery/schemas/{sales,inventory_movements,orders,discrepancies,alerts}.json`, `infra/bigquery/views.sql`, `infra/pubsub_bigquery.sh`
- Create: `services/live/aira_live/analytics/__init__.py` (empty), `services/live/aira_live/analytics/events.py`
- Test: `services/live/tests/analytics/test_events.py`, `services/live/tests/test_infra_files.py`

**Interfaces:**
- Consumes: `aira_live.config.load()`, which returns an object with `.mock: bool` and `.gcp_project: str | None` (foundation).
- Produces:
  - `aira_live.analytics.events`:
    - `async def publish(topic, payload: dict) -> str` (returns `"mock"` in mock mode or without a project)
    - `class EventError(ValueError)`
    - `TOPICS: dict[str, str]` (topic → table)
    - `REQUIRED: dict[str, set[str]]`
    - `def build_message(topic, payload) -> bytes`
  - **Event payload contracts** (publishers: Kanish's business services and Task 5 jobs):

    | Topic | Required fields | Optional fields | Notes |
    |---|---|---|---|
    | `sales` | `shop_id, sale_id, sku, qty` | `unit_price, line_total, method` | **one message per sale line** |
    | `inventory-movements` | `shop_id, txn_id, sku, delta, reason` | `ref_type, ref_id` | |
    | `orders` | `shop_id, order_id, state` | `supplier_id, items_json` | |
    | `discrepancies` | `shop_id, delivery_id, field` | `order_id, expected, actual` | |
    | `alerts` | `shop_id, kind, message` | `sku` | |

    `ts` is added automatically (ISO UTC) when absent.
  - **BigQuery:**
    - dataset `aira`
    - views `aira.sales_dedup`, `aira.inventory_movements_dedup`, `aira.daily_sales` (`shop_id, sku, day, qty`, IST days)

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/analytics/test_events.py
import json
from types import SimpleNamespace
import pytest
from aira_live.analytics import events


def test_unknown_topic_raises():
    with pytest.raises(events.EventError):
        events.build_message("payments", {"shop_id": "s"})


def test_missing_required_field_raises():
    with pytest.raises(events.EventError, match="sku"):
        events.build_message("sales", {"shop_id": "s", "sale_id": "x", "qty": 2})


def test_ts_added_and_payload_preserved():
    body = json.loads(events.build_message("sales", {"shop_id": "s", "sale_id": "x", "sku": "curd", "qty": 2}))
    assert body["sku"] == "curd" and body["qty"] == 2 and body["ts"].endswith("+00:00")


async def test_mock_mode_returns_mock(monkeypatch):
    monkeypatch.setattr(events.config, "load", lambda: SimpleNamespace(mock=True, gcp_project="p"))
    assert await events.publish("alerts", {"shop_id": "s", "kind": "low_stock", "message": "m"}) == "mock"


async def test_publishes_to_topic_path(monkeypatch):
    sent = {}

    class FakeFuture:
        def result(self, timeout=None):
            return "msg-123"

    class FakePublisher:
        def topic_path(self, project, topic):
            return f"projects/{project}/topics/{topic}"

        def publish(self, path, data):
            sent["path"], sent["data"] = path, data
            return FakeFuture()

    monkeypatch.setattr(events.config, "load", lambda: SimpleNamespace(mock=False, gcp_project="proj"))
    monkeypatch.setattr(events, "_get_publisher", lambda: FakePublisher())
    mid = await events.publish("inventory-movements",
                               {"shop_id": "s", "txn_id": "t", "sku": "curd", "delta": -2, "reason": "SALE"})
    assert mid == "msg-123"
    assert sent["path"] == "projects/proj/topics/inventory-movements"
    assert json.loads(sent["data"])["delta"] == -2
```

```python
# services/live/tests/test_infra_files.py
import json
from pathlib import Path
from aira_live.analytics import events

ROOT = Path(__file__).resolve().parents[3]


def test_required_keys_exist_in_schemas():
    for topic, table in events.TOPICS.items():
        schema = json.loads((ROOT / "infra" / "bigquery" / "schemas" / f"{table}.json").read_text())
        cols = {c["name"] for c in schema}
        assert events.REQUIRED[topic] <= cols, (topic, events.REQUIRED[topic] - cols)
        assert "ts" in cols and "shop_id" in cols
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest -q tests/analytics/test_events.py tests/test_infra_files.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'aira_live.analytics'`.

- [ ] **Step 3: Write the BigQuery schemas**

`infra/bigquery/schemas/sales.json`:

```json
[
  {"name": "shop_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "sale_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "sku", "type": "STRING", "mode": "REQUIRED"},
  {"name": "qty", "type": "FLOAT", "mode": "REQUIRED"},
  {"name": "unit_price", "type": "INTEGER", "mode": "NULLABLE"},
  {"name": "line_total", "type": "INTEGER", "mode": "NULLABLE"},
  {"name": "method", "type": "STRING", "mode": "NULLABLE"},
  {"name": "ts", "type": "TIMESTAMP", "mode": "REQUIRED"}
]
```

`infra/bigquery/schemas/inventory_movements.json`:

```json
[
  {"name": "shop_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "txn_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "sku", "type": "STRING", "mode": "REQUIRED"},
  {"name": "delta", "type": "FLOAT", "mode": "REQUIRED"},
  {"name": "reason", "type": "STRING", "mode": "REQUIRED"},
  {"name": "ref_type", "type": "STRING", "mode": "NULLABLE"},
  {"name": "ref_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "ts", "type": "TIMESTAMP", "mode": "REQUIRED"}
]
```

`infra/bigquery/schemas/orders.json`:

```json
[
  {"name": "shop_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "order_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "supplier_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "state", "type": "STRING", "mode": "REQUIRED"},
  {"name": "items_json", "type": "STRING", "mode": "NULLABLE"},
  {"name": "ts", "type": "TIMESTAMP", "mode": "REQUIRED"}
]
```

`infra/bigquery/schemas/discrepancies.json`:

```json
[
  {"name": "shop_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "delivery_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "order_id", "type": "STRING", "mode": "NULLABLE"},
  {"name": "field", "type": "STRING", "mode": "REQUIRED"},
  {"name": "expected", "type": "STRING", "mode": "NULLABLE"},
  {"name": "actual", "type": "STRING", "mode": "NULLABLE"},
  {"name": "ts", "type": "TIMESTAMP", "mode": "REQUIRED"}
]
```

`infra/bigquery/schemas/alerts.json`:

```json
[
  {"name": "shop_id", "type": "STRING", "mode": "REQUIRED"},
  {"name": "kind", "type": "STRING", "mode": "REQUIRED"},
  {"name": "sku", "type": "STRING", "mode": "NULLABLE"},
  {"name": "message", "type": "STRING", "mode": "REQUIRED"},
  {"name": "ts", "type": "TIMESTAMP", "mode": "REQUIRED"}
]
```

- [ ] **Step 4: Implement `services/live/aira_live/analytics/events.py`**

```python
"""Pub/Sub business events. Publish only AFTER the Firestore transaction commits.
Payload keys must match infra/bigquery/schemas/<table>.json (BigQuery subscription uses the table schema)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Literal

from aira_live import config

Topic = Literal["sales", "inventory-movements", "orders", "discrepancies", "alerts"]

TOPICS: dict[str, str] = {
    "sales": "sales",
    "inventory-movements": "inventory_movements",
    "orders": "orders",
    "discrepancies": "discrepancies",
    "alerts": "alerts",
}
REQUIRED: dict[str, set[str]] = {
    "sales": {"shop_id", "sale_id", "sku", "qty"},
    "inventory-movements": {"shop_id", "txn_id", "sku", "delta", "reason"},
    "orders": {"shop_id", "order_id", "state"},
    "discrepancies": {"shop_id", "delivery_id", "field"},
    "alerts": {"shop_id", "kind", "message"},
}


class EventError(ValueError):
    """Raised for an unknown topic or a payload missing BigQuery-required columns."""


_publisher = None


def _get_publisher():
    global _publisher
    if _publisher is None:
        from google.cloud import pubsub_v1
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def build_message(topic: str, payload: dict) -> bytes:
    if topic not in TOPICS:
        raise EventError(f"unknown topic {topic!r}")
    missing = REQUIRED[topic] - payload.keys()
    if missing:
        raise EventError(f"{topic} payload missing {sorted(missing)}")
    body = dict(payload)
    body.setdefault("ts", datetime.now(timezone.utc).isoformat())
    return json.dumps(body, ensure_ascii=False, default=str).encode()


async def publish(topic: Topic, payload: dict) -> str:
    data = build_message(topic, payload)
    settings = config.load()
    if settings.mock or not settings.gcp_project:
        return "mock"
    pub = _get_publisher()
    future = pub.publish(pub.topic_path(settings.gcp_project, topic), data)
    return await asyncio.to_thread(future.result, 10)
```

Add `google-cloud-pubsub>=2.26` and `google-cloud-bigquery>=3.25` to `services/live/pyproject.toml` `dependencies` if the foundation didn't, then `uv sync`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest -q tests/analytics/test_events.py tests/test_infra_files.py`
Expected: `6 passed`.

- [ ] **Step 6: Write `infra/bigquery/views.sql`**

These dedup views absorb Pub/Sub's at-least-once delivery.

```sql
CREATE OR REPLACE VIEW `aira.sales_dedup` AS
SELECT * EXCEPT(rn) FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY shop_id, sale_id, sku ORDER BY ts) AS rn FROM `aira.sales`
) WHERE rn = 1;

CREATE OR REPLACE VIEW `aira.inventory_movements_dedup` AS
SELECT * EXCEPT(rn) FROM (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY shop_id, txn_id ORDER BY ts) AS rn FROM `aira.inventory_movements`
) WHERE rn = 1;

CREATE OR REPLACE VIEW `aira.daily_sales` AS
SELECT shop_id, sku, DATE(ts, 'Asia/Kolkata') AS day, SUM(qty) AS qty
FROM `aira.sales_dedup`
GROUP BY shop_id, sku, day;
```

- [ ] **Step 7: Write `infra/pubsub_bigquery.sh`**

```bash
#!/usr/bin/env bash
# Usage: PROJECT=<id> ./infra/pubsub_bigquery.sh   (idempotent; re-run safely)
set -euo pipefail
: "${PROJECT:?set PROJECT}"
DATASET=aira
gcloud config set project "$PROJECT" >/dev/null
gcloud services enable pubsub.googleapis.com bigquery.googleapis.com

bq --location=asia-south1 mk -d "$PROJECT:$DATASET" 2>/dev/null || echo "dataset exists"
for table in sales inventory_movements orders discrepancies alerts; do
  bq mk --table --time_partitioning_field=ts --time_partitioning_type=DAY \
    "$PROJECT:$DATASET.$table" "infra/bigquery/schemas/$table.json" 2>/dev/null || echo "table $table exists"
done
bq query --use_legacy_sql=false --project_id="$PROJECT" < infra/bigquery/views.sql

# Pub/Sub service agent needs to write BigQuery.
PN=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
PUBSUB_SA="service-${PN}@gcp-sa-pubsub.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$PUBSUB_SA" \
  --role=roles/bigquery.dataEditor --condition=None >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$PUBSUB_SA" \
  --role=roles/bigquery.metadataViewer --condition=None >/dev/null

for topic in sales inventory-movements orders discrepancies alerts; do
  table="${topic//-/_}"
  gcloud pubsub topics create "$topic" 2>/dev/null || echo "topic $topic exists"
  # verify flags against https://cloud.google.com/pubsub/docs/create-bigquery-subscription
  gcloud pubsub subscriptions create "${topic}-to-bq" --topic="$topic" \
    --bigquery-table="$PROJECT:$DATASET.$table" --use-table-schema --drop-unknown-fields \
    2>/dev/null || echo "subscription ${topic}-to-bq exists"
done
echo "OK: topics + BigQuery subscriptions ready"
```

- [ ] **Step 8: Run it and smoke-test end to end**

Run:

```bash
chmod +x infra/pubsub_bigquery.sh && PROJECT=<id> ./infra/pubsub_bigquery.sh
gcloud pubsub topics publish sales --message='{"shop_id":"murugan_dairy","sale_id":"smoke-1","sku":"sakthi_curd_1l","qty":2,"unit_price":60,"line_total":120,"method":"cash","ts":"2026-10-11T10:00:00Z"}'
sleep 20 && bq query --use_legacy_sql=false 'SELECT sale_id, qty FROM aira.sales WHERE sale_id="smoke-1"'
```

Expected: the script ends with `OK: topics + BigQuery subscriptions ready`, and the query returns one row `smoke-1 | 2.0`.

- [ ] **Step 9: Commit**

```bash
git add infra/bigquery infra/pubsub_bigquery.sh services/live/aira_live/analytics services/live/tests/analytics/test_events.py services/live/tests/test_infra_files.py services/live/pyproject.toml services/live/uv.lock
git commit -m "feat(analytics): Pub/Sub event publishing with BigQuery subscriptions and dedup views"
```

---

### Task 3: Demand forecast (ARIMA_PLUS + 7-day MA fallback), demo backfill, Firestore seed

**Files:**
- Create: `services/live/aira_live/analytics/forecast.py`, `services/live/scripts/backfill_demo_sales.py`, `services/live/scripts/seed_firestore.py`
- Test: `services/live/tests/analytics/test_forecast.py`, `services/live/tests/scripts/test_backfill.py`, `services/live/tests/scripts/test_seed.py`

**Interfaces:**
- Consumes:
  - BigQuery views `aira.daily_sales` and `aira.sales_dedup` (Task 2); `config.load()`
  - `content/catalog/murugan_dairy.json` (Kanish) with the shape below. **Cross-team requirement (Kanish):** the catalog must have exactly this top-level shape.

    ```json
    {"shop": {"id": "murugan_dairy", "name": "Murugan Dairy", "city": "Chennai"},
     "products": [{"sku": "...", "name": "...", "brand": "...", "unit": "L|pack|box", "packSizeL": 1.0,
                   "price": 0, "costPrice": 0, "reorderThreshold": 0, "shelfLifeDays": 0,
                   "aliases": {"ta": [], "hi": [], "en": []}, "demoDailyBase": 0}],
     "suppliers": [{"id": "...", "name": "...", "phone": "+91...", "channel": "whatsapp", "products": ["sku"]}],
     "inventory": [{"sku": "...", "onHand": 0, "batches": [{"batchId": "...", "qty": 0, "expiryDate": "YYYY-MM-DD"}]}]}
    ```
- Produces:
  - `aira_live.analytics.forecast`:
    - `async def daily_demand(shop_id: str, sku: str) -> float | None`
    - `def choose(daily: dict[date, float], arima: float | None, today: date) -> float | None`
    - `def moving_average_7(daily, today) -> float | None`
    - `ARIMA_MIN_DAYS = 14`
    - `async def refresh_model() -> None`
    - `CREATE_MODEL_SQL`
  - `scripts/backfill_demo_sales.py`: `generate(products, days, end, seed) -> list[dict]` plus a CLI.
  - `scripts/seed_firestore.py`: `plan_writes(catalog, shop_id) -> list[tuple[str, dict]]` plus a CLI.

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/analytics/test_forecast.py
from datetime import date, timedelta
from aira_live.analytics import forecast as fc

TODAY = date(2026, 10, 13)


def days_back(n, qty=10.0):
    return {TODAY - timedelta(days=i): qty for i in range(1, n + 1)}


def test_moving_average_counts_missing_days_as_zero():
    daily = {TODAY - timedelta(days=1): 14.0, TODAY - timedelta(days=2): 7.0}
    assert fc.moving_average_7(daily, TODAY) == 3.0          # (14 + 7) / 7


def test_moving_average_none_without_recent_history():
    assert fc.moving_average_7({TODAY - timedelta(days=30): 9.0}, TODAY) is None


def test_choose_uses_arima_with_enough_history():
    assert fc.choose(days_back(20), arima=26.4, today=TODAY) == 26.4


def test_choose_falls_back_to_ma_when_history_short():
    assert fc.choose(days_back(10, 7.0), arima=99.0, today=TODAY) == 7.0


def test_choose_falls_back_to_ma_when_arima_missing():
    assert fc.choose(days_back(20, 7.0), arima=None, today=TODAY) == 7.0


def test_choose_clamps_negative_arima():
    assert fc.choose(days_back(20), arima=-3.2, today=TODAY) == 0.0


def test_choose_none_without_history():
    assert fc.choose({}, arima=None, today=TODAY) is None


async def test_daily_demand_returns_none_in_mock(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(fc.config, "load", lambda: SimpleNamespace(mock=True, gcp_project=None))
    assert await fc.daily_demand("murugan_dairy", "sakthi_curd_1l") is None
```

```python
# services/live/tests/scripts/test_backfill.py
import json
from datetime import date
from pathlib import Path
import importlib.util

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backfill_demo_sales.py"
spec = importlib.util.spec_from_file_location("backfill", SCRIPT)
bf = importlib.util.module_from_spec(spec); spec.loader.exec_module(bf)
FIXED_SKUS = ["aavin_milk_500ml", "aavin_milk_1l", "sakthi_curd_500ml", "sakthi_curd_1l", "arun_icecream_box"]
PRODUCTS = [{"sku": sku, "price": 30} for sku in FIXED_SKUS]          # no demoDailyBase → DAILY_BASE defaults
SCHEMA = Path(__file__).resolve().parents[4] / "infra" / "bigquery" / "schemas" / "sales.json"


def test_deterministic_and_complete_for_all_fixed_skus():
    a = bf.generate(PRODUCTS, days=30, end=date(2026, 10, 10), seed=7)
    b = bf.generate(PRODUCTS, days=30, end=date(2026, 10, 10), seed=7)
    assert a == b and len(a) == 150
    assert {r["sku"] for r in a} == set(FIXED_SKUS) and {r["shop_id"] for r in a} == {"murugan_dairy"}


def test_daily_base_covers_fixed_skus():
    assert set(bf.DAILY_BASE) == set(FIXED_SKUS)


def test_rows_match_sales_schema_and_are_non_negative():
    cols = {c["name"] for c in json.loads(SCHEMA.read_text())}
    for r in bf.generate(PRODUCTS, days=30, end=date(2026, 10, 10), seed=1):
        assert set(r) <= cols and r["qty"] >= 0 and r["line_total"] == r["qty"] * r["unit_price"]


def test_weekends_sell_more_on_average():
    rows = bf.generate([{"sku": "sakthi_curd_1l", "price": 60}], days=56, end=date(2026, 10, 10), seed=3)
    wk = [r["qty"] for r in rows if date.fromisoformat(r["ts"][:10]).weekday() >= 5]
    wd = [r["qty"] for r in rows if date.fromisoformat(r["ts"][:10]).weekday() < 5]
    assert sum(wk) / len(wk) > sum(wd) / len(wd)
```

```python
# services/live/tests/scripts/test_seed.py
from pathlib import Path
import importlib.util

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_firestore.py"
spec = importlib.util.spec_from_file_location("seed", SCRIPT)
seed = importlib.util.module_from_spec(spec); spec.loader.exec_module(seed)
CATALOG = {
    "shop": {"id": "murugan_dairy", "name": "Murugan Dairy", "city": "Chennai"},
    "products": [{"sku": "sakthi_curd_1l", "name": "Sakthi curd", "price": 60, "reorderThreshold": 5, "demoDailyBase": 26}],
    "suppliers": [{"id": "sakthi_vendor", "name": "Sakthi vendor", "phone": "+910000000000", "channel": "whatsapp", "products": ["sakthi_curd_1l"]}],
    "inventory": [{"sku": "sakthi_curd_1l", "onHand": 4, "batches": [{"batchId": "b1", "qty": 4, "expiryDate": "2026-10-12"}]}],
}


def test_plan_writes_paths_and_no_demo_only_fields():
    writes = dict(seed.plan_writes(CATALOG, "murugan_dairy"))
    shop = writes["shops/murugan_dairy"]
    assert shop["name"] == "Murugan Dairy" and shop["timezone"] == "Asia/Kolkata"
    assert writes["shops/murugan_dairy/products/sakthi_curd_1l"]["price"] == 60
    assert "demoDailyBase" not in writes["shops/murugan_dairy/products/sakthi_curd_1l"]
    assert writes["shops/murugan_dairy/suppliers/sakthi_vendor"]["channel"] == "whatsapp"
    assert writes["shops/murugan_dairy/inventory/sakthi_curd_1l"]["onHand"] == 4
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest -q tests/analytics/test_forecast.py tests/scripts`
Expected: FAIL. `aira_live.analytics.forecast` doesn't exist, and the scripts aren't found.

- [ ] **Step 3: Implement `services/live/aira_live/analytics/forecast.py`**

```python
"""Per-SKU daily demand. BigQuery ML ARIMA_PLUS when >= 14 days of history, else 7-day moving average."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aira_live import config

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
ARIMA_MIN_DAYS = 14

CREATE_MODEL_SQL = """
CREATE OR REPLACE MODEL `{project}.aira.demand_arima`
OPTIONS(model_type = 'ARIMA_PLUS',
        time_series_timestamp_col = 'day',
        time_series_data_col = 'qty',
        time_series_id_col = ['shop_id', 'sku'],
        data_frequency = 'DAILY',
        horizon = 7,
        auto_arima = TRUE) AS
SELECT shop_id, sku, day, qty FROM `{project}.aira.daily_sales`
WHERE day >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 120 DAY)
"""
HISTORY_SQL = """
SELECT day, qty FROM `{project}.aira.daily_sales`
WHERE shop_id = @shop AND sku = @sku
  AND day >= DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 30 DAY)
"""
FORECAST_SQL = """
SELECT forecast_value FROM ML.FORECAST(MODEL `{project}.aira.demand_arima`,
                                       STRUCT(1 AS horizon, 0.8 AS confidence_level))
WHERE shop_id = @shop AND sku = @sku
ORDER BY forecast_timestamp LIMIT 1
"""


def moving_average_7(daily: dict[date, float], today: date) -> float | None:
    window = [today - timedelta(days=i) for i in range(1, 8)]
    if not any(d in daily for d in window):
        return None
    return round(sum(daily.get(d, 0.0) for d in window) / 7, 2)


def choose(daily: dict[date, float], arima: float | None, today: date) -> float | None:
    if len(daily) >= ARIMA_MIN_DAYS and arima is not None:
        return round(max(arima, 0.0), 2)
    return moving_average_7(daily, today)


def _client():
    from google.cloud import bigquery
    return bigquery.Client(project=config.load().gcp_project)


def _params(shop_id: str, sku: str):
    from google.cloud import bigquery
    return bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("shop", "STRING", shop_id),
        bigquery.ScalarQueryParameter("sku", "STRING", sku),
    ])


def _history_sync(shop_id: str, sku: str) -> dict[date, float]:
    project = config.load().gcp_project
    rows = _client().query(HISTORY_SQL.format(project=project), job_config=_params(shop_id, sku)).result()
    return {r["day"]: float(r["qty"]) for r in rows}


def _arima_sync(shop_id: str, sku: str) -> float | None:
    project = config.load().gcp_project
    try:
        rows = list(_client().query(FORECAST_SQL.format(project=project), job_config=_params(shop_id, sku)).result())
    except Exception as exc:  # model missing or series unknown → fallback
        log.warning("ARIMA forecast unavailable for %s/%s: %s", shop_id, sku, exc)
        return None
    return float(rows[0]["forecast_value"]) if rows else None


async def daily_demand(shop_id: str, sku: str) -> float | None:
    settings = config.load()
    if settings.mock or not settings.gcp_project:
        return None
    daily = await asyncio.to_thread(_history_sync, shop_id, sku)
    arima = await asyncio.to_thread(_arima_sync, shop_id, sku) if len(daily) >= ARIMA_MIN_DAYS else None
    return choose(daily, arima, datetime.now(IST).date())


async def refresh_model() -> None:
    project = config.load().gcp_project
    await asyncio.to_thread(lambda: _client().query(CREATE_MODEL_SQL.format(project=project)).result())


if __name__ == "__main__":
    import sys
    if "--refresh" in sys.argv:
        asyncio.run(refresh_model())
        print("demand_arima refreshed")
```

- [ ] **Step 4: Implement `services/live/scripts/backfill_demo_sales.py`**

```python
"""Generate 30 days of plausible demo sales and stream them into BigQuery aira.sales.
Usage: uv run python scripts/backfill_demo_sales.py --catalog ../../content/catalog/murugan_dairy.json --shop murugan_dairy --project $PROJECT"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

WEEKDAY_FACTOR = {5: 1.15, 6: 1.3}   # Sat, Sun
# Plausible units/day for the fixed demo SKUs (interfaces: "Fixed demo data"). Catalog `demoDailyBase` overrides.
DAILY_BASE = {
    "aavin_milk_500ml": 40,
    "aavin_milk_1l": 18,
    "sakthi_curd_500ml": 20,
    "sakthi_curd_1l": 26,
    "arun_icecream_box": 6,
}


def generate(products: list[dict], days: int, end: date, seed: int, shop_id: str = "murugan_dairy") -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    for i in range(days, 0, -1):
        day = end - timedelta(days=i - 1)
        factor = WEEKDAY_FACTOR.get(day.weekday(), 1.0)
        for p in products:
            base = float(p.get("demoDailyBase", DAILY_BASE.get(p["sku"], 10)))
            qty = max(int(round(base * factor * rng.uniform(0.8, 1.2))), 0)
            price = int(p.get("price", 0))
            rows.append({
                "shop_id": shop_id, "sale_id": f"backfill-{day.isoformat()}-{p['sku']}", "sku": p["sku"],
                "qty": qty, "unit_price": price, "line_total": qty * price, "method": "cash",
                "ts": f"{day.isoformat()}T12:30:00Z",   # 18:00 IST
            })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--shop", default="murugan_dairy")
    ap.add_argument("--project", required=True)
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    products = json.loads(Path(args.catalog).read_text())["products"]
    rows = generate(products, args.days, date.today() - timedelta(days=1), seed=2026, shop_id=args.shop)
    from google.cloud import bigquery
    errors = bigquery.Client(project=args.project).insert_rows_json(f"{args.project}.aira.sales", rows)
    print("errors:", errors) if errors else print(f"inserted {len(rows)} rows")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Implement `services/live/scripts/seed_firestore.py`**

```python
"""Seed a shop (default: the judge-readable `murugan_dairy` demo shop) from content/catalog/*.json.
Usage: uv run python scripts/seed_firestore.py --catalog ../../content/catalog/murugan_dairy.json --shop murugan_dairy --project $PROJECT
Emulator: FIRESTORE_EMULATOR_HOST=127.0.0.1:8085 uv run python scripts/seed_firestore.py ... --project demo-aira"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DEMO_ONLY = {"demoDailyBase"}


def plan_writes(catalog: dict, shop_id: str) -> list[tuple[str, dict]]:
    shop = catalog["shop"]
    writes: list[tuple[str, dict]] = [(f"shops/{shop_id}", {
        "name": shop["name"], "city": shop.get("city", ""), "currency": "INR", "timezone": "Asia/Kolkata",
        "ownerUid": shop.get("ownerUid", ""),
    })]
    for p in catalog["products"]:
        writes.append((f"shops/{shop_id}/products/{p['sku']}", {k: v for k, v in p.items() if k not in DEMO_ONLY}))
    for s in catalog["suppliers"]:
        writes.append((f"shops/{shop_id}/suppliers/{s['id']}", {k: v for k, v in s.items() if k != "id"}))
    for inv in catalog.get("inventory", []):
        writes.append((f"shops/{shop_id}/inventory/{inv['sku']}", {"onHand": inv["onHand"], "batches": inv.get("batches", [])}))
    return writes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--shop", default="murugan_dairy")
    ap.add_argument("--project", required=True)
    args = ap.parse_args()
    from google.cloud import firestore
    db = firestore.Client(project=args.project)
    writes = plan_writes(json.loads(Path(args.catalog).read_text()), args.shop)
    batch = db.batch()
    for path, data in writes:
        batch.set(db.document(path), {**data, "updatedAt": firestore.SERVER_TIMESTAMP})
    batch.commit()
    print(f"seeded {len(writes)} docs into shops/{args.shop}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest -q tests/analytics/test_forecast.py tests/scripts`
Expected: `13 passed`.

- [ ] **Step 7: Backfill, train and smoke-test** (after Kanish's catalog exists and Task 2 has run)

Run:

```bash
cd services/live
uv run python scripts/seed_firestore.py --catalog ../../content/catalog/murugan_dairy.json --shop murugan_dairy --project "$PROJECT"
uv run python scripts/backfill_demo_sales.py --catalog ../../content/catalog/murugan_dairy.json --shop murugan_dairy --project "$PROJECT"
GCP_PROJECT="$PROJECT" uv run python -m aira_live.analytics.forecast --refresh
GCP_PROJECT="$PROJECT" GEMINI_API_KEY=x uv run python -c "import asyncio; from aira_live.analytics.forecast import daily_demand; print(asyncio.run(daily_demand('murugan_dairy','sakthi_curd_1l')))"
```

Expected:
- `seeded N docs into shops/murugan_dairy`
- `inserted 30×(#products) rows`
- `demand_arima refreshed`
- a positive float near that SKU's `demoDailyBase` (for example `25.8`)

- [ ] **Step 8: Commit**

```bash
git add services/live/aira_live/analytics/forecast.py services/live/scripts services/live/tests/analytics/test_forecast.py services/live/tests/scripts
git commit -m "feat(analytics): ARIMA_PLUS demand forecast with 7-day MA fallback, demo backfill and Firestore seed"
```

---

### Task 4: `analytics.summary.today` + Live tools `day_summary`, `reorder_suggest`

**Files:**
- Create: `services/live/aira_live/analytics/summary.py`, `services/live/aira_live/tools/insights.py`
- Modify: `services/live/aira_live/i18n/en.json`, `ta.json`, `hi.json` (add `insights.*` keys); `services/live/aira_live/tools/__init__.py` (register both tools in `TOOL_FUNCS`)
- Test: `services/live/tests/analytics/test_summary.py`, `services/live/tests/test_insights.py`

**Interfaces:**
- Consumes:
  - `db()`, `forecast.daily_demand`
  - `business.ledger.on_hand(shop_id, sku) -> float` (Kanish)
  - `business.catalog.resolve(shop_id, spoken, lang) -> ProductRef | None` (Kanish)
  - `get_session()` with `.shop_id`, `.lang`; `i18n.t`
- Produces:
  - `aira_live.analytics.summary`:
    - `def suggest_qty(forecast: float | None, on_hand: float, safety: float = 1.15) -> int`
    - `def summarize(sales, products, inventory, forecasts, open_discrepancies) -> dict`
    - `async def today(shop_id: str) -> dict`. Keys: `sales_count, revenue, top_sku, qty_by_sku, low_stock, open_discrepancies, suggested_orders, names`.
  - `aira_live.tools.insights`:
    - `async def day_summary() -> dict` returns `{say, ...summary}`
    - `async def reorder_suggest(product: str | None = None) -> dict` returns `{say, suggestions: [{sku, name, qty, supplier, forecast, on_hand}], next_tool: "order_create"}`
    - **It only suggests; it never proposes or commits.** On "yes", the model calls Kanish's `order_create`, which runs its own read-back confirmation, so there's no double confirm.

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/analytics/test_summary.py
from aira_live.analytics import summary as sm

PRODUCTS = {"sakthi_curd_1l": {"name": "Sakthi curd", "reorderThreshold": 5},
            "aavin_milk_500ml": {"name": "Aavin milk", "reorderThreshold": 10}}


def test_suggest_qty_rounds_up_with_safety_and_never_negative():
    assert sm.suggest_qty(26.0, 2) == 28        # ceil(26*1.15 - 2) = ceil(27.9)
    assert sm.suggest_qty(5.0, 40) == 0
    assert sm.suggest_qty(None, 0) == 0


def test_summarize_counts_only_completed_sales():
    sales = [
        {"state": "SALE_COMPLETED", "total": 120, "items": [{"sku": "sakthi_curd_1l", "qty": 2}]},
        {"state": "SALE_COMPLETED", "total": 54, "items": [{"sku": "aavin_milk_500ml", "qty": 2}, {"sku": "sakthi_curd_1l", "qty": 1}]},
        {"state": "SALE_VOID", "total": 999, "items": [{"sku": "aavin_milk_500ml", "qty": 50}]},
    ]
    inventory = {"sakthi_curd_1l": {"onHand": 2}, "aavin_milk_500ml": {"onHand": 30}}
    out = sm.summarize(sales, PRODUCTS, inventory, {"sakthi_curd_1l": 26.0, "aavin_milk_500ml": 20.0}, 1)
    assert out["sales_count"] == 2 and out["revenue"] == 174
    assert out["top_sku"] == "sakthi_curd_1l" and out["qty_by_sku"]["sakthi_curd_1l"] == 3
    assert out["low_stock"] == ["sakthi_curd_1l"]
    assert out["suggested_orders"] == [{"sku": "sakthi_curd_1l", "qty": 28}]
    assert out["open_discrepancies"] == 1


def test_summarize_empty_day():
    out = sm.summarize([], PRODUCTS, {}, {}, 0)
    assert out["sales_count"] == 0 and out["top_sku"] is None and out["suggested_orders"] == []
```

```python
# services/live/tests/test_insights.py
from types import SimpleNamespace
import pytest
from aira_live.tools import insights
from aira_live.session import current_session


@pytest.fixture
def session():
    tok = current_session.set(SimpleNamespace(shop_id="murugan_dairy", lang="en", session_id="s1"))
    yield
    current_session.reset(tok)


async def test_day_summary_speaks_counts(monkeypatch, session):
    async def fake_today(shop_id):
        return {"sales_count": 48, "revenue": 2860, "top_sku": "aavin_milk_500ml", "qty_by_sku": {},
                "low_stock": [], "open_discrepancies": 0,
                "suggested_orders": [{"sku": "sakthi_curd_1l", "qty": 30}],
                "names": {"aavin_milk_500ml": "Aavin milk", "sakthi_curd_1l": "Sakthi curd"}}
    monkeypatch.setattr(insights.summary, "today", fake_today)
    out = await insights.day_summary()
    assert "48" in out["say"] and "2860" in out["say"] and "Aavin milk" in out["say"] and "Sakthi curd" in out["say"]


async def test_reorder_suggest_no_history_says_not_enough_data(monkeypatch, session):
    async def fake_products(shop_id):
        return {"sakthi_curd_1l": {"name": "Sakthi curd", "reorderThreshold": 5}}
    async def no_forecast(shop_id, sku): return None
    async def on_hand(shop_id, sku): return 20.0
    async def suppliers(shop_id): return {}
    monkeypatch.setattr(insights, "_products", fake_products)
    monkeypatch.setattr(insights, "_suppliers_by_sku", suppliers)
    monkeypatch.setattr(insights.forecast, "daily_demand", no_forecast)
    monkeypatch.setattr(insights.ledger, "on_hand", on_hand)
    out = await insights.reorder_suggest()
    assert out["suggestions"] == [] and "enough" in out["say"].lower()


async def test_reorder_suggest_low_stock_suggests_order(monkeypatch, session):
    async def fake_products(shop_id):
        return {"sakthi_curd_1l": {"name": "Sakthi curd", "reorderThreshold": 5}}
    async def fc(shop_id, sku): return 26.0
    async def on_hand(shop_id, sku): return 2.0
    async def suppliers(shop_id): return {"sakthi_curd_1l": "Sakthi vendor"}
    monkeypatch.setattr(insights, "_products", fake_products)
    monkeypatch.setattr(insights, "_suppliers_by_sku", suppliers)
    monkeypatch.setattr(insights.forecast, "daily_demand", fc)
    monkeypatch.setattr(insights.ledger, "on_hand", on_hand)
    out = await insights.reorder_suggest()
    assert out["suggestions"][0]["qty"] == 28 and out["next_tool"] == "order_create"
    assert "Sakthi vendor" in out["say"] and "28" in out["say"]
```

The foundation exports `current_session` (a ContextVar) from `aira_live.session` next to `get_session()`. If its name differs, use the foundation's setter.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest -q tests/analytics/test_summary.py tests/test_insights.py`
Expected: FAIL with `ModuleNotFoundError` for `aira_live.analytics.summary` / `aira_live.tools.insights`.

- [ ] **Step 3: Implement `services/live/aira_live/analytics/summary.py`**

```python
"""Day summary for one shop (IST business day). Pure aggregation in summarize(); I/O in today()."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, time
from zoneinfo import ZoneInfo

from aira_live.analytics import forecast
from aira_live.db import db

IST = ZoneInfo("Asia/Kolkata")


def suggest_qty(forecast_value: float | None, on_hand: float, safety: float = 1.15) -> int:
    if forecast_value is None:
        return 0
    return max(math.ceil(forecast_value * safety - on_hand), 0)


def summarize(sales: list[dict], products: dict[str, dict], inventory: dict[str, dict],
              forecasts: dict[str, float | None], open_discrepancies: int) -> dict:
    completed = [s for s in sales if s.get("state") == "SALE_COMPLETED"]
    qty_by_sku: dict[str, float] = {}
    for s in completed:
        for item in s.get("items", []):
            qty_by_sku[item["sku"]] = qty_by_sku.get(item["sku"], 0.0) + float(item.get("qty", 0))
    low = sorted(sku for sku, p in products.items()
                 if float(inventory.get(sku, {}).get("onHand", 0)) <= float(p.get("reorderThreshold", 0)))
    suggestions = []
    for sku in products:
        qty = suggest_qty(forecasts.get(sku), float(inventory.get(sku, {}).get("onHand", 0)))
        if qty > 0:
            suggestions.append({"sku": sku, "qty": qty})
    return {
        "sales_count": len(completed),
        "revenue": sum(int(s.get("total", 0)) for s in completed),
        "top_sku": max(qty_by_sku, key=qty_by_sku.get) if qty_by_sku else None,
        "qty_by_sku": qty_by_sku,
        "low_stock": low,
        "open_discrepancies": open_discrepancies,
        "suggested_orders": suggestions,
    }


async def today(shop_id: str) -> dict:
    shop = db().collection("shops").document(shop_id)
    start = datetime.combine(datetime.now(IST).date(), time.min, tzinfo=IST)
    products = {d.id: d.to_dict() async for d in shop.collection("products").stream()}
    inventory = {d.id: d.to_dict() async for d in shop.collection("inventory").stream()}
    sales = [d.to_dict() async for d in shop.collection("sales")
             .where("state", "==", "SALE_COMPLETED").where("createdAt", ">=", start).stream()]
    open_disc = len([d async for d in shop.collection("orders").where("state", "==", "DISCREPANCY").stream()])
    values = await asyncio.gather(*(forecast.daily_demand(shop_id, sku) for sku in products))
    out = summarize(sales, products, inventory, dict(zip(products, values)), open_disc)
    out["names"] = {sku: p.get("name", sku) for sku, p in products.items()}
    return out
```

On google-cloud-firestore ≥ 2.11, `where(...)` takes `filter=FieldFilter(...)`; the positional form still works but warns. Verify against https://cloud.google.com/python/docs/reference/firestore/latest.

- [ ] **Step 4: Add the i18n keys**

Merge these keys into the existing files; don't replace the files.

`services/live/aira_live/i18n/en.json`:

```json
"insights.day_summary": "Today: {count} sales, ₹{revenue}. Top seller: {top}.",
"insights.no_sales": "No sales recorded today yet.",
"insights.tomorrow_order": "Suggested order for tomorrow: {items}.",
"insights.reorder_item": "{name}: {on_hand} left, you sell about {forecast} a day. Order {qty} from {supplier}?",
"insights.not_enough_data": "I don't have enough sales history yet to suggest an order.",
"insights.nothing_to_order": "Stock looks fine. Nothing to order now.",
"insights.unknown_product": "I couldn't find {name} in your shop."
```

`ta.json`:

```json
"insights.day_summary": "இன்று {count} விற்பனை, ₹{revenue}. அதிகம் விற்றது: {top}.",
"insights.no_sales": "இன்று இன்னும் விற்பனை பதிவாகவில்லை.",
"insights.tomorrow_order": "நாளைக்கு பரிந்துரை ஆர்டர்: {items}.",
"insights.reorder_item": "{name}: {on_hand} மட்டுமே உள்ளது, தினமும் சுமார் {forecast} விற்கிறீர்கள். {supplier}-இடம் {qty} ஆர்டர் செய்யவா?",
"insights.not_enough_data": "ஆர்டர் பரிந்துரைக்க இன்னும் போதுமான விற்பனை வரலாறு இல்லை.",
"insights.nothing_to_order": "சரக்கு போதுமானது. இப்போது ஆர்டர் தேவையில்லை.",
"insights.unknown_product": "உங்கள் கடையில் {name} கிடைக்கவில்லை."
```

`hi.json`:

```json
"insights.day_summary": "आज {count} बिक्री, ₹{revenue}. सबसे ज़्यादा बिका: {top}।",
"insights.no_sales": "आज अभी तक कोई बिक्री दर्ज नहीं हुई।",
"insights.tomorrow_order": "कल के लिए सुझाया गया ऑर्डर: {items}।",
"insights.reorder_item": "{name}: सिर्फ़ {on_hand} बचे हैं, आप रोज़ लगभग {forecast} बेचते हैं। {supplier} से {qty} ऑर्डर करूँ?",
"insights.not_enough_data": "ऑर्डर सुझाने के लिए अभी पर्याप्त बिक्री इतिहास नहीं है।",
"insights.nothing_to_order": "स्टॉक ठीक है। अभी ऑर्डर की ज़रूरत नहीं।",
"insights.unknown_product": "आपकी दुकान में {name} नहीं मिला।"
```

- [ ] **Step 5: Implement `services/live/aira_live/tools/insights.py`**

```python
"""Live tools: day_summary, reorder_suggest. Suggest only — ordering goes through order_create's confirmation."""
from __future__ import annotations

import asyncio

from aira_live.analytics import forecast, summary
from aira_live.business import catalog, ledger
from aira_live.db import db
from aira_live.i18n import t
from aira_live.session import get_session


async def _products(shop_id: str) -> dict[str, dict]:
    return {d.id: d.to_dict() async for d in db().collection("shops").document(shop_id).collection("products").stream()}


async def _suppliers_by_sku(shop_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    async for d in db().collection("shops").document(shop_id).collection("suppliers").stream():
        sup = d.to_dict()
        for sku in sup.get("products", []):
            out.setdefault(sku, sup.get("name", d.id))
    return out


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:.1f}"


async def day_summary() -> dict:
    s = get_session()
    d = await summary.today(s.shop_id)
    names = d.get("names", {})
    if d["sales_count"] == 0:
        say = t("insights.no_sales", s.lang)
    else:
        say = t("insights.day_summary", s.lang, count=d["sales_count"], revenue=d["revenue"],
                top=names.get(d["top_sku"], d["top_sku"]))
    if d["suggested_orders"]:
        items = ", ".join(f"{o['qty']} {names.get(o['sku'], o['sku'])}" for o in d["suggested_orders"])
        say = f"{say} {t('insights.tomorrow_order', s.lang, items=items)}"
    return {"say": say, **d}


async def reorder_suggest(product: str | None = None) -> dict:
    s = get_session()
    products = await _products(s.shop_id)
    if product:
        ref = await catalog.resolve(s.shop_id, product, s.lang)
        if ref is None:
            return {"say": t("insights.unknown_product", s.lang, name=product), "suggestions": [], "next_tool": None}
        skus = [ref.sku]
    else:
        skus = list(products)
    suppliers = await _suppliers_by_sku(s.shop_id)
    forecasts, stocks = await asyncio.gather(
        asyncio.gather(*(forecast.daily_demand(s.shop_id, sku) for sku in skus)),
        asyncio.gather(*(ledger.on_hand(s.shop_id, sku) for sku in skus)),
    )
    suggestions, any_history = [], False
    for sku, fc, on in zip(skus, forecasts, stocks):
        if fc is None:
            continue
        any_history = True
        qty = summary.suggest_qty(fc, on)
        if qty > 0:
            suggestions.append({"sku": sku, "name": products.get(sku, {}).get("name", sku), "qty": qty,
                                "supplier": suppliers.get(sku), "forecast": fc, "on_hand": on})
    if not any_history:
        return {"say": t("insights.not_enough_data", s.lang), "suggestions": [], "next_tool": None}
    if not suggestions:
        return {"say": t("insights.nothing_to_order", s.lang), "suggestions": [], "next_tool": None}
    say = " ".join(t("insights.reorder_item", s.lang, name=x["name"], on_hand=_fmt(x["on_hand"]),
                     forecast=_fmt(x["forecast"]), qty=x["qty"], supplier=x["supplier"] or "")
                   for x in suggestions)
    return {"say": say, "suggestions": suggestions, "next_tool": "order_create"}
```

Then register both tools in `aira_live/tools/__init__.py`: add `from aira_live.tools.insights import day_summary, reorder_suggest` and append both to `TOOL_FUNCS`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest -q tests/analytics/test_summary.py tests/test_insights.py`
Expected: `7 passed`.

- [ ] **Step 7: Try it through the harness against the seeded `murugan_dairy` demo shop**

Run: `cd services/live && GCP_PROJECT="$PROJECT" uv run python -m aira_live.devtools.call_tool reorder_suggest --args '{}' --shop murugan_dairy`
Expected: JSON with `"next_tool": "order_create"` and at least one suggestion whose `qty > 0` (the backfill created history), plus a `say` like `Sakthi curd: 4 left, you sell about 25.8 a day. Order 26 from Sakthi vendor?`.

- [ ] **Step 8: Commit**

```bash
git add services/live/aira_live/analytics/summary.py services/live/aira_live/tools/insights.py services/live/aira_live/tools/__init__.py services/live/aira_live/i18n services/live/tests/analytics/test_summary.py services/live/tests/test_insights.py
git commit -m "feat(insights): day_summary and reorder_suggest tools backed by forecast and live stock"
```

---

### Task 5: Scheduled jobs (`/jobs/*`, OIDC), FCM registration + family alerts, Cloud Scheduler

**Files:**
- Create: `services/live/aira_live/jobs.py`, `services/live/aira_live/fcm.py`, `infra/scheduler.sh`
- Modify: `services/live/aira_live/main.py` (`app.include_router(jobs.router)`, `app.include_router(fcm.router)`); i18n files (`jobs.*` keys)
- Test: `services/live/tests/test_jobs.py`, `services/live/tests/test_fcm.py`

**Interfaces:**
- Consumes:
  - `business.expiry.scan(shop_id, today) -> list[dict]` (Kanish). Each dict has at least `sku`, plus `name`, `qty` and `daysLeft` where available.
  - `sessions.notify(shop_id, text, cue)`, `summary.today`, `forecast.refresh_model`, `events.publish("alerts", …)`
  - firebase-admin `auth.verify_id_token`, `messaging.send_each_for_multicast`
- Produces:
  - **Routes:**
    - `POST /jobs/expiry`, `POST /jobs/day-summary`, `POST /jobs/forecast-refresh`: OIDC, audience = env `AIRA_JOBS_AUDIENCE`, email = env `AIRA_SCHEDULER_SA`
    - `POST /fcm/register`: header `Authorization: Bearer <Firebase ID token>`, body `{"token": str, "platform": "web"|"android"}` → `users/{uid}/fcmTokens/{token}`
  - `aira_live.fcm`:
    - `async def register_token(uid, token, platform) -> None`
    - `async def family_tokens(shop_id) -> list[str]`
    - `async def send_to_family(shop_id, title, body) -> int` (returns the number of successful sends)
  - **Cross-team requirement (Ishwarya):** after Firebase sign-in on a family phone, the app calls `POST /fcm/register` with the web-push token.

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/test_jobs.py
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from aira_live import jobs


@pytest.fixture
def client(monkeypatch):
    calls = {"notify": [], "fcm": [], "events": []}

    async def shops(): return [("murugan_dairy", "en")]
    async def scan(shop_id, today): return [{"sku": "sakthi_curd_1l", "name": "Sakthi curd", "qty": 6, "daysLeft": 1}]
    async def notify(shop_id, text, cue="ack"):
        calls["notify"].append((shop_id, text, cue)); return 1
    async def send(shop_id, title, body):
        calls["fcm"].append((shop_id, title, body)); return 1
    async def publish(topic, payload):
        calls["events"].append((topic, payload)); return "mock"

    monkeypatch.setattr(jobs, "_shops", shops)
    monkeypatch.setattr(jobs.expiry, "scan", scan)
    monkeypatch.setattr(jobs.sessions, "notify", notify)
    monkeypatch.setattr(jobs.fcm, "send_to_family", send)
    monkeypatch.setattr(jobs.events, "publish", publish)
    app = FastAPI(); app.include_router(jobs.router)
    return TestClient(app), calls


def test_jobs_reject_missing_token(client):
    c, calls = client
    assert c.post("/jobs/expiry").status_code == 401
    assert calls["notify"] == [] and calls["fcm"] == []


def test_jobs_reject_wrong_email(client, monkeypatch):
    c, calls = client
    def verify(*a, **k): raise HTTPException(403, "wrong caller")
    monkeypatch.setattr(jobs, "verify_scheduler", verify)
    assert c.post("/jobs/expiry", headers={"Authorization": "Bearer x"}).status_code == 403
    assert calls["fcm"] == []


def test_expiry_job_speaks_alerts_and_publishes(client, monkeypatch):
    c, calls = client
    monkeypatch.setattr(jobs, "verify_scheduler", lambda authorization: {"email": "sched@x"})
    r = c.post("/jobs/expiry", headers={"Authorization": "Bearer ok"})
    assert r.status_code == 200 and r.json() == {"shops": 1, "alerts": 1}
    assert "Sakthi curd" in calls["notify"][0][1] and calls["notify"][0][2] == "warn"
    assert calls["fcm"][0][0] == "murugan_dairy"
    assert calls["events"][0][0] == "alerts" and calls["events"][0][1]["kind"] == "expiry"
```

```python
# services/live/tests/test_fcm.py
from fastapi import FastAPI
from fastapi.testclient import TestClient
from aira_live import fcm


def test_register_requires_bearer():
    app = FastAPI(); app.include_router(fcm.router)
    assert TestClient(app).post("/fcm/register", json={"token": "t", "platform": "web"}).status_code == 401


def test_register_stores_token_for_verified_uid(monkeypatch):
    stored = {}
    monkeypatch.setattr(fcm, "_verify_firebase", lambda token: {"uid": "family1"})
    async def reg(uid, token, platform): stored.update(uid=uid, token=token, platform=platform)
    monkeypatch.setattr(fcm, "register_token", reg)
    app = FastAPI(); app.include_router(fcm.router)
    r = TestClient(app).post("/fcm/register", json={"token": "tok-9", "platform": "web"},
                             headers={"Authorization": "Bearer idt"})
    assert r.status_code == 200 and stored == {"uid": "family1", "token": "tok-9", "platform": "web"}


async def test_send_to_family_returns_zero_without_tokens(monkeypatch):
    async def none(shop_id): return []
    monkeypatch.setattr(fcm, "family_tokens", none)
    assert await fcm.send_to_family("murugan_dairy", "t", "b") == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest -q tests/test_jobs.py tests/test_fcm.py`
Expected: FAIL with `ImportError: cannot import name 'jobs'` / `'fcm'`.

- [ ] **Step 3: Implement `services/live/aira_live/fcm.py`**

```python
"""FCM: token registration (family phones) and alerts to a shop's family members."""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from aira_live.db import db

log = logging.getLogger(__name__)
router = APIRouter()


def _admin():
    import firebase_admin
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    return firebase_admin


def _verify_firebase(id_token: str) -> dict:
    from firebase_admin import auth
    _admin()
    return auth.verify_id_token(id_token)


class RegisterBody(BaseModel):
    token: str
    platform: Literal["web", "android"] = "web"


async def register_token(uid: str, token: str, platform: str) -> None:
    from google.cloud import firestore
    await db().collection("users").document(uid).collection("fcmTokens").document(token).set(
        {"platform": platform, "createdAt": firestore.SERVER_TIMESTAMP})


async def family_tokens(shop_id: str) -> list[str]:
    tokens: list[str] = []
    users = db().collection("users").where("shopId", "==", shop_id).where("role", "==", "family")
    async for u in users.stream():
        tokens += [t.id async for t in u.reference.collection("fcmTokens").stream()]
    return tokens


async def send_to_family(shop_id: str, title: str, body: str) -> int:
    tokens = await family_tokens(shop_id)
    if not tokens:
        return 0
    from firebase_admin import messaging
    _admin()
    msg = messaging.MulticastMessage(tokens=tokens, notification=messaging.Notification(title=title, body=body))
    resp = await asyncio.to_thread(messaging.send_each_for_multicast, msg)
    for tok, r in zip(tokens, resp.responses):
        if not r.success:
            log.warning("FCM send failed for a token of shop %s: %s", shop_id, r.exception)
    return resp.success_count


@router.post("/fcm/register")
async def register(body: RegisterBody, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = _verify_firebase(authorization[7:])
    except Exception:
        raise HTTPException(401, "invalid token")
    await register_token(claims["uid"], body.token, body.platform)
    return {"ok": True}
```

- [ ] **Step 4: Implement `services/live/aira_live/jobs.py`**

```python
"""Cloud Scheduler-invoked jobs. Each route requires a Google OIDC token from the scheduler service account."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException

from aira_live import fcm, sessions
from aira_live.analytics import events, forecast, summary
from aira_live.business import expiry
from aira_live.db import db
from aira_live.i18n import t

IST = ZoneInfo("Asia/Kolkata")
router = APIRouter()


def verify_scheduler(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    from google.auth.transport import requests as greq
    from google.oauth2 import id_token
    try:
        claims = id_token.verify_oauth2_token(authorization[7:], greq.Request(),
                                              audience=os.environ["AIRA_JOBS_AUDIENCE"])
    except (ValueError, KeyError):
        raise HTTPException(401, "invalid token")
    if claims.get("email") != os.environ.get("AIRA_SCHEDULER_SA") or not claims.get("email_verified"):
        raise HTTPException(403, "caller not allowed")
    return claims


async def _shops() -> list[tuple[str, str]]:
    """(shop_id, owner language) for every shop."""
    out = []
    async for s in db().collection("shops").stream():
        lang = "en"
        owner = (s.to_dict() or {}).get("ownerUid")
        if owner:
            u = await db().collection("users").document(owner).get()
            lang = (u.to_dict() or {}).get("lang", "en") if u.exists else "en"
        out.append((s.id, lang))
    return out


@router.post("/jobs/expiry")
async def job_expiry(authorization: str | None = Header(default=None)):
    verify_scheduler(authorization)
    today = datetime.now(IST).date()
    shops, total = 0, 0
    for shop_id, lang in await _shops():
        alerts = await expiry.scan(shop_id, today)
        shops += 1
        if not alerts:
            continue
        total += len(alerts)
        items = ", ".join(f"{a.get('qty', '')} {a.get('name') or a['sku']}".strip() for a in alerts)
        text = t("jobs.expiry_alert", lang, items=items)
        await sessions.notify(shop_id, text, cue="warn")
        await fcm.send_to_family(shop_id, "AIRA", text)
        for a in alerts:
            await events.publish("alerts", {"shop_id": shop_id, "kind": "expiry", "sku": a["sku"], "message": text})
    return {"shops": shops, "alerts": total}


@router.post("/jobs/day-summary")
async def job_day_summary(authorization: str | None = Header(default=None)):
    verify_scheduler(authorization)
    sent = 0
    for shop_id, lang in await _shops():
        d = await summary.today(shop_id)
        if d["sales_count"] == 0:
            text = t("insights.no_sales", lang)
        else:
            top = d.get("names", {}).get(d["top_sku"], d["top_sku"])
            text = t("insights.day_summary", lang, count=d["sales_count"], revenue=d["revenue"], top=top)
        sent += await sessions.notify(shop_id, text, cue="done")
        await fcm.send_to_family(shop_id, "AIRA", text)
    return {"notified_sessions": sent}


@router.post("/jobs/forecast-refresh")
async def job_forecast_refresh(authorization: str | None = Header(default=None)):
    verify_scheduler(authorization)
    await forecast.refresh_model()
    return {"ok": True}
```

Add the i18n key `jobs.expiry_alert` to the three files:
- `en`: `"Expiring soon: {items}. Sell them first or return them to the vendor."`
- `ta`: `"விரைவில் காலாவதி: {items}. முதலில் விற்கவும் அல்லது விற்பனையாளரிடம் திருப்பவும்."`
- `hi`: `"जल्द एक्सपायर: {items}। पहले बेचें या विक्रेता को लौटाएँ।"`

In `main.py`, add `from aira_live import fcm, jobs`, then `app.include_router(jobs.router)` and `app.include_router(fcm.router)`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/live && uv run pytest -q tests/test_jobs.py tests/test_fcm.py`
Expected: `6 passed`.

- [ ] **Step 6: Write and run `infra/scheduler.sh`**

```bash
#!/usr/bin/env bash
# Usage: PROJECT=<id> ./infra/scheduler.sh  (after aira-live is deployed)
set -euo pipefail
: "${PROJECT:?set PROJECT}"
REGION=asia-south1
gcloud config set project "$PROJECT" >/dev/null
gcloud services enable cloudscheduler.googleapis.com
URL=$(gcloud run services describe aira-live --region "$REGION" --format='value(status.url)')
SA="aira-scheduler@${PROJECT}.iam.gserviceaccount.com"
gcloud iam service-accounts create aira-scheduler --display-name="AIRA Scheduler" 2>/dev/null || true
gcloud run services update aira-live --region "$REGION" \
  --update-env-vars "AIRA_JOBS_AUDIENCE=${URL},AIRA_SCHEDULER_SA=${SA}"

job() {  # name schedule path
  gcloud scheduler jobs create http "$1" --location "$REGION" --schedule "$2" --time-zone "Asia/Kolkata" \
    --uri "${URL}$3" --http-method POST --oidc-service-account-email "$SA" --oidc-token-audience "$URL" 2>/dev/null \
  || gcloud scheduler jobs update http "$1" --location "$REGION" --schedule "$2" --time-zone "Asia/Kolkata" \
    --uri "${URL}$3" --http-method POST --oidc-service-account-email "$SA" --oidc-token-audience "$URL"
}
job aira-expiry "0 7 * * *" /jobs/expiry
job aira-day-summary "0 21 * * *" /jobs/day-summary
job aira-forecast-refresh "0 2 * * *" /jobs/forecast-refresh
echo "OK: 3 scheduler jobs"
```

Run: `chmod +x infra/scheduler.sh && PROJECT=<id> ./infra/scheduler.sh && gcloud scheduler jobs run aira-expiry --location asia-south1`
Expected: `OK: 3 scheduler jobs`. In Cloud Run logs, `POST /jobs/expiry` returns 200 with `{"shops": N, "alerts": M}`. A direct `curl -X POST "$URL/jobs/expiry"` returns **401**.

- [ ] **Step 7: Commit**

```bash
git add services/live/aira_live/jobs.py services/live/aira_live/fcm.py services/live/aira_live/main.py services/live/aira_live/i18n infra/scheduler.sh services/live/tests/test_jobs.py services/live/tests/test_fcm.py
git commit -m "feat(ops): OIDC-protected expiry/day-summary/forecast jobs, FCM family alerts, Cloud Scheduler"
```

---

### Task 6: Remote Config model switch, Secret Manager, Cloud Build CI/CD, uptime + alerts + budget

**Files:**
- Create: `services/live/aira_live/remote_config.py`, `infra/remote-config/template.json`, `infra/secrets.sh`, `cloudbuild.yaml`, `infra/monitoring.sh`, `infra/monitoring/uptime-alert.json`, `infra/budget.sh`
- Modify:
  - `services/live/aira_live/models.py`: the order becomes env > Remote Config > default, with a model allowlist
  - `services/live/aira_live/main.py`: start the refresher on startup
  - `firebase.json`: add the `remoteconfig` key
  - `services/live/tests/test_infra_files.py`: add the template and Cloud Build checks
- Test: `services/live/tests/test_remote_config.py`

**Interfaces:**
- Consumes: `models.DEFAULTS`, `models.FALLBACKS` (foundation).
- Produces:
  - `aira_live.remote_config`:
    - `PARAMS`
    - `def parse_template(tpl: dict) -> dict[str, str]`
    - `async def refresh(force: bool = False) -> dict[str, str]`
    - `def get_override(param: str) -> str | None`
    - `def override_for_role(role: str) -> str | None` (`point → pointing_model`, `count → count_model`, `live → live_model`)
    - `def flag(name: str, default: bool) -> bool`
    - `async def run_refresher() -> None`
  - **Cross-team names (Kanish):**
    - `remote_config.flag("whatsapp_enabled", True)` gates the WhatsApp channel (when off, use click-to-chat).
    - Secrets and env: `WHATSAPP_TOKEN`, `WHATSAPP_VERIFY_TOKEN`, `TELEGRAM_TOKEN` (Secret Manager); `WHATSAPP_PHONE_ID` (plain env var).

- [ ] **Step 1: Write the failing tests**

```python
# services/live/tests/test_remote_config.py
import json
from pathlib import Path
import pytest
from aira_live import models, remote_config as rc

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    rc._cache.clear()
    for role in ("POINT", "COUNT", "LIVE", "READ", "VERIFY"):
        monkeypatch.delenv(f"AIRA_MODEL_{role}", raising=False)
    yield
    rc._cache.clear()


def test_parse_template_reads_default_values():
    tpl = {"parameters": {"pointing_model": {"defaultValue": {"value": "gemini-3.8-flash"}},
                          "whatsapp_enabled": {"defaultValue": {"value": "false"}},
                          "empty": {"defaultValue": {"value": ""}}}}
    assert rc.parse_template(tpl) == {"pointing_model": "gemini-3.8-flash", "whatsapp_enabled": "false"}


def test_remote_config_switches_point_model_to_fallback():
    rc._cache.update({"pointing_model": models.FALLBACKS["point"]})
    assert models.model_for("point") == models.FALLBACKS["point"]


def test_unknown_model_override_ignored():
    rc._cache.update({"pointing_model": "gemini-typo-9000"})
    assert models.model_for("point") == models.DEFAULTS["point"]


def test_env_beats_remote_config(monkeypatch):
    rc._cache.update({"pointing_model": models.FALLBACKS["point"]})
    monkeypatch.setenv("AIRA_MODEL_POINT", models.DEFAULTS["point"])
    assert models.model_for("point") == models.DEFAULTS["point"]


def test_flag_parsing():
    rc._cache.update({"whatsapp_enabled": "false"})
    assert rc.flag("whatsapp_enabled", True) is False and rc.flag("missing", True) is True


def test_template_defaults_match_models():
    tpl = rc.parse_template(json.loads((ROOT / "infra" / "remote-config" / "template.json").read_text()))
    assert tpl["pointing_model"] == models.DEFAULTS["point"]
    assert tpl["count_model"] == models.DEFAULTS["count"]
    assert tpl["live_model"] == models.DEFAULTS["live"]
```

Append to `services/live/tests/test_infra_files.py`. Add `pyyaml>=6` to the dev dependency group, then run `uv sync`.

```python
import yaml


def test_cloudbuild_deploys_live_and_hosting():
    cb = yaml.safe_load((ROOT / "cloudbuild.yaml").read_text())
    ids = [s["id"] for s in cb["steps"]]
    assert ids.index("test-live") < ids.index("deploy-live") < ids.index("deploy-hosting")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/live && uv run pytest -q tests/test_remote_config.py tests/test_infra_files.py`
Expected: FAIL with `ImportError: cannot import name 'remote_config'`.

- [ ] **Step 3: Implement `services/live/aira_live/remote_config.py`**

```python
"""Server-side Firebase Remote Config (REST), cached 60 s. Lets us flip ER 2 → Flash with no redeploy."""
from __future__ import annotations

import asyncio
import logging
import time

from aira_live import config

log = logging.getLogger(__name__)
PARAMS = ("pointing_model", "count_model", "live_model", "whatsapp_enabled")
ROLE_PARAM = {"point": "pointing_model", "count": "count_model", "live": "live_model"}
TTL_S = 60.0
_cache: dict[str, str] = {}
_fetched_at = 0.0


def parse_template(tpl: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, p in (tpl.get("parameters") or {}).items():
        value = ((p or {}).get("defaultValue") or {}).get("value")
        if value not in (None, ""):
            out[name] = value
    return out


def _fetch_sync(project: str) -> dict:
    # verify against https://firebase.google.com/docs/reference/remote-config/rest/v1/projects/getRemoteConfig
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/firebase.remoteconfig"])
    resp = AuthorizedSession(creds).get(
        f"https://firebaseremoteconfig.googleapis.com/v1/projects/{project}/remoteConfig", timeout=5)
    resp.raise_for_status()
    return resp.json()


async def refresh(force: bool = False) -> dict[str, str]:
    global _fetched_at
    settings = config.load()
    if settings.mock or not settings.gcp_project:
        return dict(_cache)
    if not force and time.monotonic() - _fetched_at < TTL_S:
        return dict(_cache)
    try:
        values = parse_template(await asyncio.to_thread(_fetch_sync, settings.gcp_project))
        _cache.clear()
        _cache.update(values)
        _fetched_at = time.monotonic()
    except Exception as exc:  # keep the last good values
        log.warning("Remote Config refresh failed: %s", exc)
    return dict(_cache)


def get_override(param: str) -> str | None:
    return _cache.get(param)


def override_for_role(role: str) -> str | None:
    param = ROLE_PARAM.get(role)
    return _cache.get(param) if param else None


def flag(name: str, default: bool) -> bool:
    value = _cache.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


async def run_refresher() -> None:
    while True:
        await refresh(force=True)
        await asyncio.sleep(TTL_S)
```

- [ ] **Step 4: Modify `model_for` in `services/live/aira_live/models.py`**

Keep `DEFAULTS` and `FALLBACKS` as the foundation wrote them. Replace only the function:

```python
import logging

_log = logging.getLogger(__name__)


def model_for(role: Role) -> str:
    """Priority: env AIRA_MODEL_<ROLE> > Remote Config (validated) > DEFAULTS."""
    env = os.getenv(f"AIRA_MODEL_{role.upper()}")
    if env:
        return env
    from aira_live import remote_config  # local import avoids a cycle
    override = remote_config.override_for_role(role)
    if override:
        known = set(DEFAULTS.values()) | set(FALLBACKS.values())
        if override in known:
            return override
        _log.warning("ignoring unknown Remote Config model %r for role %s", override, role)
    return DEFAULTS[role]
```

In `main.py`, start the refresher with the app's existing startup hook. If the foundation uses a FastAPI lifespan, add the task there instead:

```python
import asyncio
from aira_live import remote_config


@app.on_event("startup")
async def _start_remote_config() -> None:
    asyncio.get_running_loop().create_task(remote_config.run_refresher())
```

- [ ] **Step 5: Write `infra/remote-config/template.json`** and add the `remoteconfig` key to `firebase.json`

```json
{
  "parameters": {
    "pointing_model": { "defaultValue": { "value": "gemini-robotics-er-2-preview" },
                        "description": "point_to model. Emergency: set to gemini-3.8-flash and Publish." },
    "count_model": { "defaultValue": { "value": "gemini-robotics-er-2-preview" },
                     "description": "count_in_frame model. Emergency: gemini-3.8-flash." },
    "live_model": { "defaultValue": { "value": "gemini-3.8-live" }, "description": "Gemini Live voice model." },
    "whatsapp_enabled": { "defaultValue": { "value": "true" },
                          "description": "false → supplier orders use click-to-chat fallback." }
  }
}
```

Add to `firebase.json`: `"remoteconfig": { "template": "infra/remote-config/template.json" }`.

- [ ] **Step 6: Write `cloudbuild.yaml`** (repo root)

```yaml
# Trigger: push to main (Cloud Build → Triggers → connect GitHub repo Saravana-Rajan/AIRA, then:
#   gcloud builds triggers create github --repo-owner=Saravana-Rajan --repo-name=AIRA \
#     --branch-pattern='^main$' --build-config=cloudbuild.yaml --substitutions=_LIVE_WS=wss://<aira-live-host>/ws )
steps:
  - id: test-live
    name: ghcr.io/astral-sh/uv:python3.12-bookworm
    dir: services/live
    entrypoint: bash
    args: ["-c", "uv sync --frozen && uv run pytest -q"]
  - id: test-web
    name: node:20
    entrypoint: bash
    args: ["-c", "npm ci && npm test"]
  - id: deploy-live
    name: gcr.io/google.com/cloudsdktool/cloud-sdk:slim
    entrypoint: gcloud
    args: ["run", "deploy", "aira-live", "--source", "services/live", "--region", "asia-south1", "--quiet"]
  - id: build-web
    name: node:20
    entrypoint: bash
    env: ["VITE_AIRA_LIVE_WS=${_LIVE_WS}"]
    args: ["-c", "npm ci && npm run build -w apps/app && npm run build -w apps/dashboard"]
  - id: deploy-hosting
    name: node:20
    entrypoint: bash
    args: ["-c", "npx --yes firebase-tools deploy --only hosting,firestore,remoteconfig --project $PROJECT_ID --non-interactive"]
substitutions:
  _LIVE_WS: "wss://REPLACE-WITH-AIRA-LIVE-HOST/ws"
options:
  logging: CLOUD_LOGGING_ONLY
timeout: 1800s
```

`gcloud run deploy --source` keeps existing service settings (min instances, secrets, env) unless they are overridden. Grant the Cloud Build service account these roles: `roles/run.admin`, `roles/iam.serviceAccountUser`, `roles/firebasehosting.admin`, `roles/firebaserules.admin`, `roles/datastore.indexAdmin`, `roles/firebaseremoteconfig.admin`, `roles/artifactregistry.writer`.

- [ ] **Step 7: Write `infra/secrets.sh`, `infra/monitoring.sh`, `infra/monitoring/uptime-alert.json`, `infra/budget.sh`**

```bash
#!/usr/bin/env bash
# infra/secrets.sh — Usage: PROJECT=<id> ./infra/secrets.sh ; then add values with:
#   printf '%s' "$VALUE" | gcloud secrets versions add <name> --data-file=-
set -euo pipefail
: "${PROJECT:?set PROJECT}"
gcloud config set project "$PROJECT" >/dev/null
PN=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
RUNTIME_SA="${PN}-compute@developer.gserviceaccount.com"
for s in gemini-api-key whatsapp-token whatsapp-verify-token telegram-token; do
  gcloud secrets create "$s" --replication-policy=automatic 2>/dev/null || echo "secret $s exists"
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$RUNTIME_SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done
gcloud run services update aira-live --region asia-south1 \
  --update-secrets "GEMINI_API_KEY=gemini-api-key:latest,WHATSAPP_TOKEN=whatsapp-token:latest,WHATSAPP_VERIFY_TOKEN=whatsapp-verify-token:latest,TELEGRAM_TOKEN=telegram-token:latest" \
  --update-env-vars "WHATSAPP_PHONE_ID=${WHATSAPP_PHONE_ID:-unset},GCP_PROJECT=${PROJECT}"
echo "OK: secrets wired"
```

```bash
#!/usr/bin/env bash
# infra/monitoring.sh — Usage: PROJECT=<id> ALERT_EMAIL=<you@x> HOSTING_HOST=<project>.web.app ./infra/monitoring.sh
# verify flags at https://cloud.google.com/sdk/gcloud/reference/monitoring/uptime/create
set -euo pipefail
: "${PROJECT:?}" "${ALERT_EMAIL:?}" "${HOSTING_HOST:?}"
gcloud config set project "$PROJECT" >/dev/null
LIVE_HOST=$(gcloud run services describe aira-live --region asia-south1 --format='value(status.url)' | sed 's#https://##')
gcloud monitoring uptime create aira-live-healthz --resource-type=uptime-url \
  --resource-labels="host=${LIVE_HOST},project_id=${PROJECT}" --path=/healthz --protocol=https --period=5 || true
gcloud monitoring uptime create aira-hosting --resource-type=uptime-url \
  --resource-labels="host=${HOSTING_HOST},project_id=${PROJECT}" --path=/ --protocol=https --period=5 || true
CHANNEL=$(gcloud beta monitoring channels create --display-name="AIRA on-call" --type=email \
  --channel-labels="email_address=${ALERT_EMAIL}" --format='value(name)')
sed "s#CHANNEL_NAME#${CHANNEL}#" infra/monitoring/uptime-alert.json > /tmp/aira-alert.json
gcloud alpha monitoring policies create --policy-from-file=/tmp/aira-alert.json
echo "OK: uptime checks + alert policy"
```

`infra/monitoring/uptime-alert.json`:

```json
{
  "displayName": "AIRA uptime check failing",
  "combiner": "OR",
  "conditions": [{
    "displayName": "Any AIRA uptime check failing for 2 min",
    "conditionThreshold": {
      "filter": "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\"",
      "aggregations": [{ "alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_NEXT_OLDER",
                         "crossSeriesReducer": "REDUCE_COUNT_FALSE", "groupByFields": ["resource.label.host"] }],
      "comparison": "COMPARISON_GT", "thresholdValue": 1, "duration": "120s"
    }
  }],
  "notificationChannels": ["CHANNEL_NAME"]
}
```

```bash
#!/usr/bin/env bash
# infra/budget.sh — Usage: BILLING=<billing-account-id> AMOUNT=20000INR ./infra/budget.sh
# verify flags at https://cloud.google.com/sdk/gcloud/reference/billing/budgets/create
set -euo pipefail
: "${BILLING:?}" "${AMOUNT:=20000INR}"
gcloud billing budgets create --billing-account="$BILLING" --display-name="AIRA hackathon" \
  --budget-amount="$AMOUNT" --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
echo "OK: budget alerts at 50/90/100%"
```

- [ ] **Step 8: Run all the tests**

Run: `cd services/live && uv run pytest -q`
Expected: the whole suite passes, including `test_remote_config.py` (6) and `test_infra_files.py` (2).

- [ ] **Step 9: Apply and verify in the cloud**

Run:

```bash
npx firebase-tools deploy --only remoteconfig --project "$PROJECT"
PROJECT=<id> ./infra/secrets.sh
PROJECT=<id> ALERT_EMAIL=<email> HOSTING_HOST=<id>.web.app ./infra/monitoring.sh
BILLING=<billing-id> ./infra/budget.sh
```

Then the emergency drill: in the Firebase console, set `pointing_model` to `gemini-3.8-flash` and click **Publish**. Within 60 s, `uv run python -m aira_live.devtools.call_tool product_find --args '{"product":"Sakthi curd"}' --image bench/data/identify/curd1.jpg` logs model `gemini-3.8-flash`. Set it back.

Expected: each script prints its `OK:` line, the uptime checks show green in Cloud Monitoring, and the drill switches models with no redeploy.

- [ ] **Step 10: Commit**

```bash
git add services/live/aira_live/remote_config.py services/live/aira_live/models.py services/live/aira_live/main.py services/live/tests/test_remote_config.py services/live/tests/test_infra_files.py services/live/pyproject.toml services/live/uv.lock infra/remote-config infra/secrets.sh infra/monitoring.sh infra/monitoring infra/budget.sh cloudbuild.yaml firebase.json
git commit -m "feat(ops): Remote Config model switch, secrets wiring, Cloud Build CI/CD, uptime + budget alerts"
```
