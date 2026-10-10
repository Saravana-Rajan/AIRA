# AIRA — Design Spec

| | |
|---|---|
| **Date** | 2026-10-10 (replaces the earlier multi-pillar concept) |
| **Team** | Techjays: Saravana Rajan B (lead), Ishwarya, Sarmitha, Kanish |
| **Event** | Google Cloud AI Builder Cup 2026. Theme: *Sustainability & Social Impact* (problem category: Accessibility / Social Good) |
| **Submission** | Due 2026-10-18 23:59 IST; team target: submit by 20:00 IST |
| **Mandatory tech** | The description must explain how **Firebase, Firestore, Cloud Run and Gemini** are used |

## 1. Vision

**AIRA lets a blind person run a small shop alone.** It uses voice, a chest-mounted phone, Google's visual AI and a reliable business memory, with no computer, screen or sighted helper.

It is **not** a chatbot or a billing app. It connects five things:

- what the shopkeeper **says**
- what the camera **sees**
- what **physically happens** in the shop
- what the **business records** say
- what AIRA **remembers** across days

**First shop:** *Murugan Dairy*. Murugan is blind and sells a full dairy range: **milk, curd, buttermilk, ghee, paneer and butter** (Aavin and Sakthi), plus **Arun ice cream** boxes.

**Why it matters:**

- India has about 12M kirana stores.
- Government **NHFDC self-employment loans** exist so disabled people can start shops.
- Yet blind owners still depend on a sighted helper for stock, bills, cash and orders.

**Prior art we cite honestly:**

| Prior art | What it covers | What it lacks |
|---|---|---|
| Devfolio "Voice Automated Billing System for Blind People" (Blind Relief Association café) | Voice billing | Stock, delivery checks, guidance |
| Revel POS Accessibility Mode (2014) | Accessible billing on an iPad | AI and physical tasks |
| Britannia A-Eye (2025) | Helps blind *shoppers* | Nothing for the *owner* |

No system runs the full owner loop: order → delivery count → invoice check → pay vendor → guided sale → payment check → stock → reorder.

## 2. The core loop: Murugan's day

| # | Moment | Murugan | AIRA |
|---|---|---|---|
| 1 | **Order stock** | "I need 30 litres Sakthi curd, order from the Sakthi vendor." | Reads the order back and confirms it. After "yes", sends a **WhatsApp order** to the vendor. The vendor's reply is spoken: "Delivery at 6 pm." State: `ORDER_PLACED` |
| 2 | **Delivery arrives** | Puts the packs into the fridge one by one, or a crate at a time | **Counts live**: "…28, 29, 30 ✓", or "28 counted. 2 short?" Records only the **verified** quantity. State: `DELIVERY_RECONCILED` or `DISCREPANCY` |
| 3 | **Vendor's paper invoice** | Holds up the bill | Two independent reads, then **code** compares them with the order and the count: "Bill says 30 L × ₹… = ₹…, matches" or "Bill says 32 L, but 30 arrived" |
| 4 | **Pays the vendor** | Takes cash from the cashbox | Guides him to the slot ("₹500 slot on your left"), checks the notes in his hand, and code totals them: "₹1,000 ✓." State: `PAYMENT_CONFIRMED` |
| 5 | **Remember where things are** | "This is the Sakthi curd shelf." | Saves the spatial memory "curd → fridge 2, middle shelf, left", marked as confirmed by the user |
| 6 | **Customer sale** | Customer: "2 litres curd." | Hears it, checks recorded stock, and guides his **hand** to the curd using location memory, live pointing and fingertip tracking. Verifies the pack in his hand ("Sakthi curd 1 L ✓, take one more"). Then states the price |
| 7 | **Payment** | Customer pays cash or UPI | **Cash:** checks the notes and calculates the change in code, then guides the received cash into the right cashbox slot. **UPI:** listens for the shop's **payment soundbox** ("₹60 received") and code matches the amount. State: `SALE_COMPLETED`. Stock goes down by exactly 2 L, **once** |
| 8 | **Low stock / expiry** | (automatic) | "Only 2 L Sakthi curd left. Order 30 L? You sell about 26 L a day." "6 curd packs expire tomorrow, sell them first." |
| 9 | **End of day** | "How was today?" | "48 sales, ₹2,860, top seller Aavin milk. Tomorrow's suggested order: 30 L curd, 20 L milk." |
| ∞ | **Walking inside the shop (always on)** | Walks between the fridge, freezer, counter and crates | **Bump guard:** the phone detects an obstacle in his path, on the device with no network. It **vibrates and beeps within 150 ms**, then says "Stop" or "Obstacle left". This catches crates on the floor, an open fridge door and shelf corners |

### 2.1 Products and units

| Product | Brand (demo) | Sold in | Notes |
|---|---|---|---|
| Milk | Aavin | 500 ml / 1 L pouches (litres) | Fast-moving; short shelf life |
| Curd | Sakthi | 500 ml / 1 L packs (litres) | |
| Buttermilk (moru) | Aavin | 200 ml pouches | Looks like a milk pouch, so the label check matters |
| Ghee | Aavin | 200 ml / 500 ml jars | Long shelf life |
| Paneer | Aavin | 200 g packs | Ordered by kg and sold by pack |
| Butter | Aavin | 100 g packs | |
| Ice cream | Arun | Family-pack boxes | Freezer |

**Unit rules:**
- Stock and sales are counted in each product's **selling unit** (litres or packs/jars/boxes).
- `business.catalog` converts spoken quantities. "5 kg paneer" becomes "25 packs of 200 g", and AIRA reads both back before confirming.
- Vision identification must tell confusable packs apart (milk vs buttermilk pouch, 500 ml vs 1 L) by asking to see the label side.

## 3. Principles

1. **The model proposes, the backend commits.** Gemini never writes stock, money or state directly. Tools call deterministic services that validate and commit inside **Firestore transactions**, with **idempotency keys**.
2. **Evidence before state.**
   - An order is not a delivery.
   - An invoice is not proof of receipt.
   - A saved location is not proof the product is still there.
   - A payment is unconfirmed until there is evidence: a cash count, a soundbox announcement, or explicit user confirmation.
3. **Honest uncertainty.**
   - Two reads disagree → `unsure`. Ask for another view and never invent a count.
   - Speak "last recorded on the middle shelf" when the location may be stale.
4. **Confirm before consequence.** Ordering, paying and completing a sale always need a spoken "yes". Nothing is ever executed silently.
5. **Voice-first, hands-free.**
   - The chest-mounted phone and an earphone stay on him all day.
   - Interaction: **double-tap = talk**, **long-press = stop**. Works with TalkBack.
   - Short sentences, Tamil/Hindi/English, and AIRA can be interrupted.
6. **No fake-note or counterfeit claims.** Cash recognition is presented as assistance, with confidence. Change is always computed in code.

## 4. Architecture

Region: **asia-south1 (Mumbai)** for Cloud Run, Firestore, Cloud Storage, Scheduler and BigQuery. Gemini is called through the global Gemini API. `aira-live` runs with `--min-instances 1 --max-instances 1`.

```
PHONE (chest-mounted) — AIRA app: installable PWA + Android APK (Bubblewrap TWA)
  voice I/O (built-in mic 16 kHz → Live; 24 kHz playback) · gesture surface · earcons + haptics
  camera frames on request (count / point / invoice / cash / product) · MediaPipe Hand Landmarker
  (fingertip) → guidance tones · TalkBack-friendly · judge demo mode (sample videos/images/audio)
        │ WSS
        ▼
CLOUD RUN  aira-live  (Python 3.12, FastAPI + Google ADK, gemini-3.8-live bidi; min=1, timeout 3600)
  voice orchestrator → tools (function calls) → services:
    vision/    (Gemini Robotics-ER 2: count, point, identify; Gemini 3.8 Flash: invoice + cash reads)
    memory/    (spatial · episodic · preferences)
    business/  (deterministic: products, inventory ledger, sales, payments, change, soundbox parser,
                suppliers + orders, deliveries + invoice reconciliation, expiry, state machine, audit)
    analytics/ (Pub/Sub events, BigQuery demand forecast, day summary)
    messaging/ (WhatsApp Cloud API orders + webhook; Telegram bot optional; click-to-chat fallback)
        │
        ├── Firestore (system of record: shops/{shopId}/…; transactions + idempotency)
        ├── Cloud Storage (invoice + evidence images, 30-day retention)
        ├── Pub/Sub topics: sales · inventory-movements · orders · discrepancies · alerts
        │        └─► BigQuery dataset `aira` (history, ARIMA_PLUS / moving-average demand forecast)
        ├── Secret Manager (GEMINI_API_KEY, WHATSAPP_TOKEN, TELEGRAM_TOKEN)
        └── Cloud Scheduler → aira-live /jobs/* (expiry scan 07:00, day summary 21:00, forecast refresh)
FIREBASE: Hosting (app + Shop Dashboard) · Auth · App Check · Cloud Messaging (alerts to family/
          helper phone) · Remote Config (model switch: ER 2 ↔ Flash; feature flags)
SHOP DASHBOARD (web, Firebase Hosting, Firestore realtime): live stock, today's sales, orders &
          deliveries, discrepancies, audit trail, expiry — for family, judges, and the deck
```

### 4.1 Model responsibilities

| Component | Model / tech | Job |
|---|---|---|
| Voice conversation | `gemini-3.8-live` | Understands requests, speaks replies, calls tools, can be interrupted, Tamil/Hindi/English |
| Counting, pointing, identifying | `gemini-robotics-er-2-preview` (non-streaming) | Counting packs, locating a product, hand guidance, checking the pack in his hand |
| Invoice and cash reads | `gemini-3.8-flash` | Structured reads (two independent passes) |
| Arithmetic, stock, states | Deterministic Python | Exact math, ledger, state transitions, idempotency |
| Fallback | Remote Config switch | `point` / `count` move to `gemini-3.8-flash` if ER 2 is slow or failing |

ER 2 returns text only, so all speech comes from Gemini Live. Model IDs live **only** in `services/live/aira_live/models.py`.

### 4.2 Supplier ordering channel

1. **Primary: WhatsApp Business Cloud API** (Meta).
   - A pre-approved utility template `order_request` ("New order from {{shop}}: {{items}}. Please confirm delivery time.") is sent to the vendor's number.
   - Vendor replies arrive at the **webhook** `POST /webhooks/whatsapp` on aira-live. They are stored on the order and spoken to Murugan.
   - The hackathon uses Meta's **free test number**, with vendor numbers registered as test recipients. Production carries a small per-message fee; verify current Meta pricing.
2. **Optional: Telegram Bot API.** Free and automatic, but only for vendors who have started a chat with the AIRA bot.
3. **Fallback:** a WhatsApp click-to-chat link (`https://wa.me/<number>?text=<order>`). It opens WhatsApp with the message pre-typed, and Murugan double-taps Send.

## 5. Data model (Firestore — system of record)

```
users/{uid}                       shopId, role: "owner"|"family"|"demo", lang, createdAt
shops/{shopId}                    name, ownerUid, city, currency "INR", timezone "Asia/Kolkata"
shops/{shopId}/products/{sku}     name, brand, unit "L"|"pack"|"jar"|"box", packSizeL?, packSizeG?, price, costPrice,
                                  reorderThreshold, shelfLifeDays, aliases {ta[],hi[],en[]}
shops/{shopId}/suppliers/{id}     name, phone, channel "whatsapp"|"telegram"|"call", products[sku]
shops/{shopId}/orders/{id}        supplierId, items[{sku, qty, agreedPrice?}], state, channelMsgId?,
                                  vendorReply?, idemKey, createdAt, updatedAt
shops/{shopId}/deliveries/{id}    orderId, items[{sku, expectedQty, countedQty, method, confidence}],
                                  invoice{imageUri, readA, readB, agreed, lines[], total},
                                  discrepancies[{sku|field, expected, actual}], state
shops/{shopId}/inventory/{sku}    onHand, batches[{batchId, qty, expiryDate}], updatedAt
shops/{shopId}/ledger/{txnId}     sku, delta, reason "DELIVERY"|"SALE"|"RETURN"|"ADJUST"|"EXPIRED",
                                  refType, refId, idemKey (unique), at, actor
shops/{shopId}/locations/{sku}    zone "fridge2/middle/left", confirmedBy "user"|"model",
                                  confirmedAt, confidence
shops/{shopId}/sales/{id}         items[{sku, qty, unitPrice, lineTotal}], total, state, paymentId?, idemKey
shops/{shopId}/payments/{id}      direction "in"|"out", method "cash"|"upi", amount, state,
                                  evidence{type "cash_count"|"soundbox"|"user_confirm", detail}, refType, refId
shops/{shopId}/memoryEvents/{id}  type, text, refType?, refId?, at
shops/{shopId}/auditLogs/{id}     actor "owner"|"aira"|"system", action, refType, refId, outcome,
                                  evidenceUri?, at
shops/{shopId}/prefs/main         speechRate, verbosity, productAliases, supplierAliases
idempotency/{idemKey}             result, at   (dedupe for retried tool calls)
```

**Transaction states** (validated by `business/states.py`):

| Entity | States |
|---|---|
| Order | `DRAFT → ORDER_PLACED → DELIVERY_PENDING → DELIVERY_RECONCILED \| DISCREPANCY → PAYMENT_PENDING → PAYMENT_CONFIRMED → CLOSED` |
| Sale | `SALE_PENDING → SALE_COMPLETED \| SALE_VOID` |
| Payment | `PENDING → CONFIRMED \| FAILED` |

## 6. Safety, reliability and privacy

- Every consequential tool returns a **proposal** first. The commit happens only after a spoken "yes", passed back as a `confirm` message.
- A Firestore transaction plus an `idemKey` from the tool-call ID means **a retry never deducts stock twice**.
- **Counting:**
  - One by one, or by crate.
  - Low confidence → "Show me again" or "Count by crate."
  - The final count is read back and confirmed.
- **Invoice:** two reads must agree on every number; otherwise the specific field is read out for confirmation.
- **UPI:** confirmed only by a soundbox announcement (amount parsed in code and matched to the sale ±₹0) or explicit owner confirmation. A customer's screenshot is never trusted.
- **Images:**
  - Kept only for invoices and evidence, for 30 days in Cloud Storage.
  - Faces are blurred on the phone before upload.
  - No customer personal data is stored.
- **Offline:**
  - Spoken status.
  - Sales queue locally (IndexedDB) and sync with the same idempotency keys.
- **Paid Gemini tier:** shop images are not used for training.

## 7. Testing and benchmarks (the deck's "Performance / Benchmarking" slide)

**Golden sets in `bench/data/`:**

| Set | Content |
|---|---|
| Counting clips | 20 (one-by-one and crate) |
| Invoices | 30, including 10 with errors |
| Cash images | 40 |
| Product identification | 30 (Aavin 500 ml vs Sakthi 1 L, and others) |
| Hand guidance | 20 trials |
| Soundbox audio | 20 clips |

**Metrics:**

- count accuracy and abstain rate
- invoice discrepancy catch rate
- cash-total accuracy
- product-identification accuracy
- guidance time to touch
- soundbox parse accuracy
- end-to-end sale time (with AIRA vs a baseline without AIRA)
- p50/p95 latency: first audio, ER 2 count, invoice check
- cost per sale (₹)
- uptime

**Reliability tests:** missing goods, an uncertain count, duplicate tool calls, network loss mid-sale, an unverified payment, and an ER 2 timeout (fallback).

**User validation:** at least one blind participant, ideally a real blind shop owner (via an NHFDC office or a blind association), runs the loop in a real or mock dairy shop. Record completion, errors, time, assistance needed and quotes.

## 8. Demo and submission

**Video ≤ 4:00 (target 3:50).**

- The rules text says "3 to 4 minute video". The form label says "up to 3 minutes" and the template says "3 Minutes"; the team chose 4 minutes. Confirm with the organiser (support@hack2skill.com) before upload.
- Real footage of Murugan's day, one story:

| Time | Scene |
|---|---|
| 0:00–0:25 | Hook: a blind owner opening his shop alone |
| 0:25–0:55 | WhatsApp order by voice, with the vendor's reply spoken |
| 0:55–1:40 | Delivery: live count catches 2 missing packs; invoice mismatch caught |
| 1:40–2:05 | Pays the vendor; cash checked |
| 2:05–2:55 | Customer sale: hand guided to the curd, change checked, then a UPI soundbox payment confirmed |
| 2:55–3:20 | Low-stock and expiry alerts, plus the forecast reorder |
| 3:20–3:40 | Shop Dashboard and Google Cloud console (proof of deployment) |
| 3:40–3:50 | Numbers and a quote |

**Live link:**

- Firebase Hosting: the app (judge demo mode with sample media) plus the Shop Dashboard (read-only demo shop).
- Cloud Run `aira-live` stays warm through **Nov 6**.

**Other submission items:**

- Deck: the 16-slide template exported to PDF (≤ 5 MB).
- Description: ≤ 1024 characters, naming Firebase, Firestore, Cloud Run and Gemini.
- Repo: public GitHub.

## 9. Out of scope (roadmap slide)

- Real bank UPI APIs
- Barcode-only inventory
- Multi-shop chains
- iPhone
- Fridge temperature sensors
- Offline on-device LLM
- Native Android background service

## 10. Risks

| Risk | Mitigation |
|---|---|
| ER 2 latency or quality | Day-1 spike. Remote Config fallback to Flash. Counting one by one, with read-back |
| WhatsApp template approval delay | Use the test number + the default `hello_world` template for the first test. Click-to-chat fallback |
| Noisy shop audio | Double-tap before speaking. Order read-back. Built-in mic, close to the mouth |
| Demo realism | Film in a real small dairy shop with real products; at least one blind participant |
| Scope | **Must-work core:** steps 1–4 and 6–7 of §2. Steps 5, 8 and 9 come next. Everything else is roadmap |

## 11. Business model

- **Free for blind shop owners.** Funded through the NHFDC and state disability self-employment schemes (AIRA bundled with the loan) and corporate CSR.
- **Dairy brands and distributors sponsor AIRA** (Aavin, Sakthi, Arun and others). AIRA forecasts demand and sends reorders straight to their vendors, which raises their sell-through and cuts stock-outs.
- **Banks / UPI soundbox partners** gain merchants who can verify payments by voice.
- **Later:** a small monthly subscription for sighted small-shop owners, who get the same voice inventory, ordering and forecasting.

## 12. Judging strategy (how we target 90+)

| Lever | Criterion it raises | What we must show |
|---|---|---|
| A real blind shopkeeper (or blind participant) running the loop | Impact, UX | Task completion, errors caught and time, plus a quote in the video |
| Measured numbers | Technical | Count accuracy, invoice discrepancy catch rate, cash accuracy, soundbox parse accuracy, sale time with vs without AIRA, latency, cost per sale |
| A live link that never breaks | Technical | Demo mode with sample media, warm Cloud Run, Remote Config fallback to Flash, uptime alerts |
| One clear story, real footage | Innovation, UX | Murugan's day in a real dairy shop; no mockups |
| Rubric words in the deck and description | All (AI pre-screen) | Name Firebase, Firestore, Cloud Run and Gemini, plus "meaningful Gen AI", "scalability", "impact" and "originality" |

**Honest estimate:**

| Outcome | Score |
|---|---|
| Built well, with a real blind participant and benchmarks | about 88–93 |
| Realistic, with some slippage | about 78–84 |
| Exceptional, with every lever above | 95 or more |
