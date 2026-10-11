# AIRA architecture

AIRA has three parts:

1. **The phone app** (`frontend/app`). A PWA on a chest-mounted phone. It streams the mic and camera to the backend, plays AIRA's voice in one earphone, and runs MediaPipe on the device.
2. **The backend** (`backend`). `aira-live` is a FastAPI service on Cloud Run in `asia-south1`. It holds the Gemini Live session, runs the tools, and owns every write to Firestore.
3. **The Shop Dashboard** (`frontend/dashboard`). A read-only web page that shows the shop in real time from Firestore.

The app and the dashboard are served from one Firebase Hosting site, `aira-shop`: the app at `/` and the dashboard at `/dashboard`. The backend's WebSocket URL is injected into the app at build time. It is not stored in the repository.

## System diagram

```mermaid
flowchart LR
  subgraph PHONE["Phone on the chest"]
    APP["AIRA app (PWA)<br/>mic 16 kHz · earphone 24 kHz · camera"]
    MP["MediaPipe on device<br/>Hand Landmarker · Object Detector"]
    MP --> APP
  end

  subgraph RUN["Cloud Run: aira-live (FastAPI, asia-south1)"]
    WS["WebSocket /ws<br/>session + frame store"]
    BRIDGE["Live bridge"]
    TOOLS["Tools<br/>order · delivery · invoice · pay · sale · stock · find"]
    CONFIRM["confirm.propose / resolve"]
    VISION["Vision<br/>count · point · identify · invoice · cash"]
    BIZ["Business engine<br/>states · ledger · sales · payments · money<br/>soundbox · reconcile · expiry · audit"]
    MSG["Messaging<br/>WhatsApp · Telegram · wa.me link"]
    HOOKS["Webhooks<br/>POST /webhooks/whatsapp · /webhooks/telegram"]
    JOBS["Jobs<br/>POST /jobs/expiry · day-summary · forecast-refresh"]
  end

  APP <-->|"WSS: audio, frames, events, confirm"| WS
  WS <--> BRIDGE
  BRIDGE <-->|"bidi audio + function calls"| LIVE["Gemini Live<br/>gemini-3.8-live"]
  BRIDGE --> TOOLS
  TOOLS --> CONFIRM
  TOOLS --> VISION
  VISION --> ER2["gemini-robotics-er-2-preview<br/>fallback gemini-3.8-flash"]
  VISION --> READS["read A: gemini-3.8-flash<br/>read B: gemini-3.6-flash"]
  CONFIRM -->|"only after a spoken yes"| BIZ
  TOOLS --> BIZ
  JOBS --> BIZ
  BIZ -->|"transactions + idempotency keys"| FS[("Firestore<br/>database aira")]
  BIZ --> MSG
  MSG --> WA["WhatsApp Cloud API"]
  MSG --> TG["Telegram Bot API (optional)"]
  WA -->|"vendor reply"| HOOKS
  TG -->|"vendor reply"| HOOKS
  HOOKS --> FS
  HOOKS -->|"sessions.notify: speak it"| WS

  subgraph HOST["Firebase Hosting: aira-shop"]
    BUNDLE["App bundle at /"]
    DASH["Shop Dashboard at /dashboard"]
  end
  BUNDLE -.->|"loads in the phone browser"| APP
  DASH -->|"realtime listeners, read only"| FS
  SM["Secret Manager"] -.->|"GEMINI_API_KEY as env"| RUN
```

### What runs where

| Where | What | Notes |
|---|---|---|
| Phone | Voice I/O, gestures (double-tap to talk, long-press to stop), earcons, haptics | Works with TalkBack |
| Phone | Camera frames | Sent only when the server asks (`request_frame`) |
| Phone | MediaPipe Hand Landmarker | Tracks the index fingertip. Turns the target point into stereo pan, pitch and beep rate |
| Phone | MediaPipe Object Detector | The **bump guard**. Vibrates and beeps when an obstacle is in the walking path. No network needed |
| Cloud Run | Gemini Live session, tools, vision calls, business engine, messaging, webhooks, jobs | One warm instance (`min=1, max=1`) with session affinity, because the live-session registry is in memory |
| Gemini API | Voice, pointing, counting, reading | Model IDs live only in `backend/aira_live/models.py`. They can be switched by env or the Firestore doc `config/runtime` |
| Firestore | Every record: stock, orders, sales, payments, audit | Written only by `aira-live` |
| Browser | Shop Dashboard | Reads the demo shop in real time. Cannot write |

### Models

| Role | Model | Job |
|---|---|---|
| `live` | `gemini-3.8-live` | Listens, speaks, can be interrupted, calls tools. Answers in the language of the last utterance and switches with the speaker; no default language |
| `point`, `count` | `gemini-robotics-er-2-preview` | Finds a product in the frame, counts packs, checks the pack in his hand |
| fallback for `point`, `count` | `gemini-3.8-flash` | Used when ER 2 is slow or failing |
| `read` | `gemini-3.8-flash` | Invoice and cash: read A |
| `verify` | `gemini-3.6-flash` | Invoice and cash: read B. A different model generation, so the two reads do not fail the same way |

Coordinates are `[y, x]` for a point and `[ymin, xmin, ymax, xmax]` for a box, normalised to 0–1000. All speech comes from Gemini Live. The vision models return structured data only.

### WebSocket protocol

The protocol is defined twice and kept in sync: `shared/contracts/src/messages.ts` (TypeScript) and `backend/aira_live/contracts.py` (pydantic). Shared fixtures in `shared/contracts/fixtures/` are tested on both sides.

- **App → server:** `hello`, `audio` (16 kHz PCM16), `frame` (JPEG with a purpose), `text`, `event`, `confirm`, `stop`.
- **Server → app:** `ready`, `audio` (24 kHz PCM16), `transcript`, `request_frame`, `target`, `count`, `state`, `confirm`, `cue`, `error`.

## Order-to-sale sequence

Sam talks to the app. The app streams his voice over `/ws` to `aira-live`, which streams it to Gemini Live. To keep the diagram short, arrows from Sam go straight to `aira-live`, and arrows back to him are spoken replies.

```mermaid
sequenceDiagram
  autonumber
  actor M as Sam
  participant A as AIRA app
  participant S as aira-live
  participant L as Gemini Live
  participant V as Vision models
  participant B as Business engine
  participant F as Firestore
  actor W as Vendor

  Note over M,W: 1-2. Voice order to the vendor
  M->>S: "Order 30 litres Sakthi curd"
  S->>L: audio
  L->>S: tool call order_create
  S->>F: store proposal
  S-->>M: read-back "30 litres Sakthi curd from the Sakthi vendor. Order?"
  M->>S: "Yes"
  L->>S: confirm_action yes
  S->>B: commit the order
  B->>W: WhatsApp Cloud API message (Telegram, or a wa.me link as fallback)
  B->>F: order ORDER_PLACED (transaction)
  W->>S: reply on the webhook
  S->>F: save vendorReply on the order
  S-->>M: "Vendor says delivery at 6 pm"

  Note over M,W: 3. Live delivery count
  L->>S: delivery_start, then delivery_count
  B->>F: order DELIVERY_PENDING
  S->>A: request_frame count
  A->>S: camera frames
  S->>V: count with Robotics-ER 2
  V-->>S: count and confidence
  S-->>A: count messages "28 of 30"
  S-->>M: "28 counted. 2 short?" (read back and confirmed)
  S->>B: record the verified count only
  B->>F: ledger DELIVERY +28 (transaction, idempotent)

  Note over M,W: 4. Invoice two-read check
  L->>S: invoice_check
  S->>A: request_frame invoice
  A->>S: invoice photo
  S->>V: read A (3.8 Flash) and read B (3.6 Flash)
  V-->>S: two structured reads
  S->>B: compare read A, read B, the order and the count
  B->>F: DELIVERY_RECONCILED or DISCREPANCY
  S-->>M: "Bill says 30 litres, but 28 arrived"

  Note over M,W: 5. Pay the vendor with cash guidance
  L->>S: supplier_pay cash
  S-->>M: which notes to take
  S->>A: request_frame cash
  A->>S: photo of the notes in his hand
  S->>V: two cash reads
  S->>B: total the notes in code
  S-->>M: "1,000 rupees. Pay now?"
  M->>S: "Yes"
  B->>F: payment out CONFIRMED, order PAYMENT_CONFIRMED

  Note over M,W: 6. Guided sale
  M->>S: customer wants "2 litres curd"
  L->>S: sale_start
  B->>F: sale SALE_PENDING (prices from the catalog)
  L->>S: product_find
  S->>F: last saved location of the curd
  S->>V: point at the curd with Robotics-ER 2
  S-->>A: target point
  A->>A: MediaPipe fingertip tracking and guidance tones
  A->>S: event fingertip_in_target
  S->>V: identify the pack in his hand
  S-->>M: "Sakthi curd 1 litre. Take one more."

  Note over M,W: 7. Payment check (UPI shown, cash works like step 5)
  L->>S: sale_pay upi
  S->>S: parse the soundbox announcement from the live transcript
  S-->>M: "120 rupees received. Complete the sale?"
  M->>S: "Yes"
  S->>B: complete the sale
  B->>F: payment in CONFIRMED (evidence soundbox)
  B->>F: ledger SALE -2 litres, once, and sale SALE_COMPLETED

  Note over M,W: 8-9. Stock ledger and alerts
  B->>F: read stock, expiry dates and sales history
  S-->>M: "Only 2 litres curd left. Order 30 litres?"
```

Notes on the sequence:

- Every step that changes money or stock (order, vendor payment, sale) goes through `confirm.propose(...)`. The commit runs only after a spoken "yes", either through the Live tool `confirm_action` or a `confirm` message from the app.
- Delivery counts and sales write to the **ledger**. `inventory/{sku}.onHand` changes in the same transaction. Each ledger entry carries a unique `idemKey` built from the tool-call ID, so a retried call is a no-op.
- The demand forecast is a 7-day moving average of sale ledger entries. With fewer than 3 days of data, AIRA gives no forecast. Business events are appended to `shops/{shopId}/events` after each commit.
- Expiry and day-summary checks run through `/jobs/*`. The app can call them at startup as a "morning check". They need the `X-AIRA-Job-Token` header. Alerts are spoken in the owner's live session through `sessions.notify`.
- **Bump guard** is not in this diagram. It runs only on the phone, all the time, and never waits for the server.

## Data model

Firestore database `aira`. The demo shop is `shops/sunrise_dairy`. Types are in `shared/contracts/src/firestore.ts`.

```
shops/{shopId}                     name, ownerUid, city, currency "INR", timezone "Asia/Kolkata"
  products/{sku}                   name, brand, unit "L"|"pack"|"jar"|"box", packSizeL?, packSizeG?,
                                   price, costPrice, reorderThreshold, shelfLifeDays, aliases {ta[], hi[], en[]}
  suppliers/{id}                   name, phone, channel "whatsapp"|"telegram"|"call", products[sku]
  orders/{id}                      supplierId, items[{sku, qty, agreedPrice?}], state, channelMsgId?,
                                   vendorReply?, idemKey, createdAt, updatedAt
  deliveries/{id}                  orderId, items[{sku, expectedQty, countedQty, method, confidence}],
                                   invoice{imageUri, readA, readB, agreed, lines[], total},
                                   discrepancies[{sku|field, expected, actual}], state
  inventory/{sku}                  onHand, batches[{batchId, qty, expiryDate}], updatedAt
  ledger/{txnId}                   sku, delta, reason "DELIVERY"|"SALE"|"RETURN"|"ADJUST"|"EXPIRED",
                                   refType, refId, idemKey (unique), at, actor
  locations/{sku}                  zone e.g. "fridge2/middle/left", confirmedBy "user"|"model", confirmedAt, confidence
  sales/{id}                       items[{sku, qty, unitPrice, lineTotal}], total, state, paymentId?, idemKey,
                                   createdAt, completedAt
  payments/{id}                    direction "in"|"out", method "cash"|"upi", amount, state,
                                   evidence{type "cash_count"|"soundbox"|"user_confirm", detail}, refType, refId
  proposals/{id}                   pending confirmations from confirm.propose (kind, prompt, payload, committer)
  memoryEvents/{id}                type, text, refType?, refId?, at
  auditLogs/{id}                   actor "owner"|"aira"|"system", action, refType, refId, outcome, evidenceUri?, at
  events/{autoId}                  topic, payload, ts   (sales, inventory-movements, orders, discrepancies, alerts)
  prefs/main                       speechRate, verbosity, productAliases, supplierAliases
  msgIndex/{id}, contactIndex/{id} server only: map vendor messages and contacts back to orders

idempotency/{idemKey}              result, at   (dedupe for retried tool calls; server only)
config/runtime                     model and feature overrides (pointing_model, count_model, live_model,
                                   whatsapp_enabled); server only
```

Money is in whole rupees. Stock is in each product's selling unit (litres, packs, jars or boxes). `business.catalog` converts spoken amounts, for example "5 kg paneer" becomes 25 packs of 200 g, and AIRA reads both back.

### States

Transitions are checked in code by `business/states.py`. An invalid jump raises an error.

| Entity | States |
|---|---|
| Order | `DRAFT → ORDER_PLACED → DELIVERY_PENDING → DELIVERY_RECONCILED \| DISCREPANCY → PAYMENT_PENDING → PAYMENT_CONFIRMED → CLOSED` |
| Sale | `SALE_PENDING → SALE_COMPLETED \| SALE_VOID` |
| Payment | `PENDING → CONFIRMED \| FAILED` |

## Trust boundaries

The rule across every boundary: **the model proposes, the backend commits**. Nothing that comes from a model, a client or a vendor is stored as fact until code has checked it.

```mermaid
flowchart LR
  subgraph U["Untrusted input"]
    PH["Phone app"]
    GM["Gemini output"]
    VN["Vendor messages"]
    CU["Customer payment claims"]
  end
  subgraph T["Trusted core: aira-live"]
    CHK["Validate · propose · confirm"]
    ENG["Business engine"]
  end
  DB[("Firestore aira")]
  DSH["Shop Dashboard"]
  PH --> CHK
  GM --> CHK
  VN --> CHK
  CU --> CHK
  CHK --> ENG
  ENG -->|"only writer"| DB
  DB -->|"read only"| DSH
```

| # | Boundary | What crosses it | How it is controlled |
|---|---|---|---|
| 1 | Phone → `aira-live` (`/ws`) | Audio, frames, events, confirmations | Every message is parsed against the shared contract. Frames have a size limit. Nothing is accepted before `hello`. The demo build has no sign-in, so the server pins the session to the demo shop and the owner role. A `confirm` must match a stored proposal |
| 2 | `aira-live` → phone | Audio, targets, counts, links | The app opens only `https://wa.me/`, `https://api.whatsapp.com/` and `tel:` links |
| 3 | Gemini → `aira-live` | Tool calls, counts, reads, points | Model output is a proposal. Tools validate arguments. Totals, change and stock are computed in code. State changes go through `states.transition`. Spoken numbers are checked against tool results. Two reads must agree, or AIRA says "not sure" and asks for another look |
| 4 | Physical world → records | What the camera sees | Counts need confidence and a read-back. Only the verified quantity enters the ledger. A saved location is a hint, so live pointing checks it again. Cash reading is assistance, never a counterfeit check |
| 5 | Customer → payment | "I paid" | A UPI payment is confirmed only by the soundbox announcement (amount parsed in code, exact match) or the owner's clear confirmation. A payment screenshot is never trusted |
| 6 | Vendors → webhooks | WhatsApp and Telegram replies | The WhatsApp webhook checks Meta's signature with the app secret and a verify token. Replies are stored as text on the order and spoken. A vendor's message never changes stock or money. An order is not a delivery |
| 7 | `aira-live` → Firestore | All writes | Only the `aira-live` service account writes, and its access is limited to database `aira`. Writes run in transactions with idempotency keys. Every action is written to `auditLogs` |
| 8 | Browsers → Firestore | Dashboard reads | Firestore rules allow reads of the demo shop only. All client writes are denied. `idempotency`, `config` and other shops are server only |
| 9 | Jobs → `aira-live` | `/jobs/*` calls | Require the `X-AIRA-Job-Token` header |
| 10 | GitHub → Google Cloud | Deploys | Keyless Workload Identity Federation, limited to `main` of this repository. The deployer account has the minimum roles. No JSON key exists |
| 11 | Secrets | Gemini API key, messaging tokens | Kept in Secret Manager or env. Never in git. Project IDs and service URLs are not in the repository |
| 12 | On the phone | Bump guard | Runs locally with no network. The server may raise an alert's urgency but can never silence it |

## Deploy pipeline

Every push to `main` runs `.github/workflows/deploy.yml`:

1. Test: `npm ci && npm test && npm run typecheck` in `shared/contracts/` and in `frontend/` (the npm workspace root for the app and the dashboard), then `cd backend && uv sync --frozen && uv run pytest -q`.
2. Sign in to Google Cloud with Workload Identity Federation.
3. Build the `aira-live` container image, push it to Artifact Registry (`asia-south1`) and deploy it to Cloud Run with the Gemini key mounted from Secret Manager.
4. Read the new service URL and inject the WebSocket URL into the app build (`VITE_AIRA_LIVE_WS`).
5. Build the app and the dashboard into one bundle (`shared/scripts/build_hosting.sh`) and deploy it to Firebase Hosting site `aira-shop` with `firebase deploy --config shared/infra/firebase.json`.

Pull requests run the same tests through `.github/workflows/ci.yml`, with no access to Google Cloud.
