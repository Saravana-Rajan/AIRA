# AIRA — Ideas Catalog

Every AIRA feature, with its status and owner. Spec: `docs/superpowers/specs/2026-10-10-aira-design.md`. Interfaces: `docs/superpowers/plans/2026-10-10-00-interfaces.md`.

**Status:** ★ demo (in the video) · ● build (in the app) · ○ roadmap (deck only) · ✕ dropped

## Core shop loop (spec §2)

> Product range: milk, curd, buttermilk, ghee, paneer, butter and ice cream (spec §1). Litres for milk, curd and buttermilk; jars and packs for ghee, paneer and butter; boxes for ice cream.

| Idea | What | Status | Owner | Where specified |
|---|---|---|---|---|
| Voice supplier order | "Order 30 L Sakthi curd from the Sakthi vendor": read back, confirm, then send | ★ | Kanish (tool) · Saravana (Live) | spec §2 step 1 · `kanish/02` · `saravana/01` |
| WhatsApp order (Cloud API) | Template `order_request` sent automatically; vendor reply via webhook, spoken to Murugan | ★ | Kanish | spec §4.2 · `kanish/02` |
| Telegram order (optional) | Free bot channel for vendors who have started a chat with the AIRA bot | ● | Kanish | spec §4.2 · `kanish/02` |
| Click-to-chat fallback | `wa.me` link with the order pre-typed; Murugan double-taps Send | ● | Kanish | spec §4.2 · `kanish/02` |
| Live delivery count | Packs counted one by one (identify each pack) or by crate (snapshot count); read back and confirmed | ★ | Sarmitha (vision) · Kanish (tool) | spec §2 step 2 · `sarmitha/01` · `kanish/02` |
| Delivery discrepancy | Records only verified stock; flags shortfalls (`DISCREPANCY`) | ★ | Kanish | spec §2 step 2 · `kanish/01` |
| Invoice check | Two independent reads; code compares the bill with the order and the count | ★ | Sarmitha (read) · Kanish (reconcile) | spec §2 step 3 · `sarmitha/02` · `kanish/02` |
| Supplier cash payment | Guides to the cashbox slot; reads notes in hand; code totals them; `PAYMENT_CONFIRMED` | ★ | Sarmitha (cash read) · Kanish (payments) | spec §2 step 4 · `sarmitha/02` · `kanish/02` |
| Location memory | "This is the Sakthi curd shelf" saves a user-confirmed location | ★ | Sarmitha | spec §2 step 5 · `sarmitha/03` |
| Guided product pick | Location memory + ER 2 pointing + fingertip tracking guide his hand; the pack in hand is verified | ★ | Sarmitha (server) · Ishwarya (tones) | spec §2 step 6 · `sarmitha/03` · `ishwarya/02` |
| Cash sale + change | Notes read; change computed in code; change in hand checked | ★ | Kanish · Sarmitha | spec §2 step 7 · `kanish/02` |
| UPI soundbox check | The shop's payment soundbox announcement is parsed in code and matched to the sale amount | ★ | Kanish | spec §2 step 7 · `kanish/01` |
| Exactly-once stock deduction | Firestore transaction + idempotency key per tool call | ★ | Kanish | spec §3 · `kanish/01` |
| Low-stock alert | "Only 2 L left, order 30 L?" | ★ | Kanish · Saravana | spec §2 step 8 |
| Expiry alerts | Daily scan; "6 packs expire tomorrow, sell them first" | ★ | Kanish (scan) · Saravana (scheduler) | spec §2 step 8 · `kanish/01` · `saravana/02` |
| Demand forecast reorder | BigQuery forecast: "you sell about 26 L a day, order 30 L" | ★ | Saravana | spec §2 steps 8–9 · `saravana/02` |
| End-of-day summary | "48 sales, ₹2,860, top seller Aavin milk" + suggested orders | ● | Saravana | spec §2 step 9 · `saravana/02` |

## Interaction and safety

| Idea | What | Status | Owner | Where specified |
|---|---|---|---|---|
| Shop-floor bump guard | On-phone obstacle alert while walking in the shop (crates, open fridge door, shelf corners). Vibration + beep within 150 ms, then "Stop" / "Obstacle left". Works offline | ★ demo | Ishwarya | spec §2 (always on) · `plans/ishwarya/02-camera-guidance.md` addendum |


| Idea | What | Status | Owner | Where specified |
|---|---|---|---|---|
| Model proposes, backend commits | Consequential actions are proposals; commit only after a spoken "yes" | ★ | Saravana (`confirm.py`) · Kanish (committers) | spec §3 · `saravana/01` |
| Explicit state machine | Order / sale / payment states validated in code | ● | Kanish | spec §5 · `kanish/01` |
| Honest uncertainty | Reads disagree → "unsure", ask for another view; stale location → "last recorded on…" | ★ | Sarmitha · Kanish | spec §3 |
| Voice-first gestures | Double-tap = talk, long-press = stop; TalkBack-friendly; interruptible | ★ | Ishwarya | spec §3 · `ishwarya/01` |
| Earcons + haptics | Ack / done / warn / match / mismatch sounds and vibrations | ● | Ishwarya | `ishwarya/01` |
| Tamil / Hindi / English | Voice in all three; English captions in the video | ● | Saravana · Ishwarya | spec §3 |
| Offline sales queue | Sales queue in IndexedDB and sync with the same idempotency keys | ● | Ishwarya | spec §6 · `ishwarya/01` |
| Remote Config model fallback | ER 2 ↔ Flash switch if ER 2 is slow or failing | ● | Saravana | spec §4.1 · `saravana/02` |
| Face blur before upload | Evidence images blurred on the phone; 30-day retention | ● | Ishwarya | spec §6 · `ishwarya/02` |
| Audit trail | Every action logged with actor, outcome and evidence | ● | Kanish | spec §5 · `kanish/01` |

## Platform, demo and proof

| Idea | What | Status | Owner | Where specified |
|---|---|---|---|---|
| Installable app + APK | PWA on Firebase Hosting + Android APK (Bubblewrap TWA) | ● | Ishwarya | spec §4 · `ishwarya/03` |
| Judge demo mode | Sample videos, images and audio so sighted judges can try every step on a laptop | ★ | Ishwarya | spec §8 · `ishwarya/03` |
| Shop Dashboard | Web, Firestore realtime: stock, sales, orders, discrepancies, expiry, audit trail | ★ | Kanish | spec §4 · `kanish/03` |
| FCM family alerts | Low stock, discrepancies and expiry pushed to a family or helper phone | ● | Saravana | spec §4 · `saravana/02` |
| Pub/Sub + BigQuery analytics | Events → BigQuery history and forecasting | ● | Saravana | spec §4 · `saravana/02` |
| Benchmarks | Count accuracy, invoice catch rate, cash accuracy, guidance time, soundbox parsing, latency, cost per sale | ★ | All (runner: Ishwarya) | spec §7 · `ishwarya/03` |
| User validation | At least one blind participant, ideally a real blind shop owner; completion, errors, time, quotes | ★ | Saravana (logistics) | spec §7 · `saravana/03` |
| Demo video | 4:00 maximum (target 3:50), "Murugan's Day" | ★ | Saravana (lead) · all | `docs/ideas/04-demo-video-script.md` · `saravana/03` |

## Roadmap (spec §9)

| Idea | Status |
|---|---|
| Real bank UPI APIs | ○ |
| Barcode-only inventory | ○ |
| Multi-shop chains | ○ |
| iPhone support | ○ |
| Fridge temperature sensors | ○ |
| Offline on-device LLM | ○ |
| Native Android background service | ○ |
