<p align="center"><img src="assets/logo/aira-logo-512.png" alt="AIRA logo" width="220"></p>

<h1 align="center">AIRA</h1>
<p align="center"><b>A blind person can run their own shop. Alone.</b><br>
Google Cloud AI Builder Cup 2026 · Team Techjays · Theme: Sustainability &amp; Social Impact (Accessibility)</p>

**A**rtificial Intelligence: understands the world. · **I**ntelligent Interaction: communicates naturally. · **R**eal-world Guidance: helps users navigate. · **A**ccessibility: supports independence.

AIRA is a voice-first shop assistant for blind shopkeepers. A chest-mounted phone and one earphone replace the sighted helper, the computer and the inventory app. It is built on **Gemini Live**, **Gemini Robotics-ER 2**, **Firebase**, **Firestore** and **Cloud Run**.

**Murugan's dairy shop** (Aavin milk, Sakthi curd, Arun ice cream):

| | Murugan does | AIRA does |
|---|---|---|
| 🛒 | "Order 30 litres Sakthi curd" | Reads the order back, then sends it to the vendor on **WhatsApp** and speaks the vendor's reply |
| 📦 | Puts the delivered packs in the fridge | **Counts them live** ("28… 2 short?") and checks the **paper invoice** against the order and the count |
| 💵 | Pays the vendor | Guides him to the right cash, checks the notes, and records the payment |
| 🧊 | Customer: "2 litres curd" | Guides his **hand** to the curd and verifies the pack in his hand |
| ✅ | Takes payment | Checks the cash and the change, or confirms UPI by listening to the **payment soundbox** |
| 📉 | — | Gives low-stock and expiry alerts, plus a demand-based reorder suggestion and a daily summary |

**Design rule:** the model proposes, the backend commits. Exact arithmetic, a stock ledger, explicit states, idempotency and an audit trail all run in deterministic code on Firestore.

## Repository layout

```
assets/logo/          AIRA logo
docs/
  README.md           index of all documents
  ideas/              catalog, pitch, Murugan's day, judges Q&A, 4-min video script, idea evolution
  hackathon/          rules + rubric, submission checklist, official/ notes
  research/           prior art, India context, Google tech + WhatsApp API facts
  superpowers/specs/  2026-10-10-aira-design.md
  superpowers/plans/  team plan · foundation · interfaces · saravana/ ishwarya/ sarmitha/ kanish/
apps/app              AIRA phone app (installable PWA + Android APK)
apps/dashboard        Shop Dashboard (web, Firestore realtime)
packages/contracts    shared protocol + Firestore types + fixtures
services/live         Cloud Run aira-live (Gemini Live voice orchestrator + tools + business engine)
content/catalog       demo shop products + suppliers
bench/                benchmark datasets + scorers
infra/                GCP + Firebase bootstrap, rules, Pub/Sub, BigQuery, CI
```

## Team

| Member | Owns | Plans |
|---|---|---|
| Saravana (lead) | Foundation and contracts, Gemini Live voice orchestrator, cloud data/analytics/infra, submission | [`plans/saravana/`](docs/superpowers/plans/saravana/) (after [`00-foundation`](docs/superpowers/plans/2026-10-10-00-foundation.md)) |
| Ishwarya | Phone app shell and voice, camera + hand guidance, judge demo mode, APK, benchmark runner | [`plans/ishwarya/`](docs/superpowers/plans/ishwarya/) |
| Sarmitha | Vision with Gemini Robotics-ER 2 (count, identify, point), invoice + cash reads, memory + product finding | [`plans/sarmitha/`](docs/superpowers/plans/sarmitha/) |
| Kanish | Business engine (ledger, sales, payments, states), orders/delivery/payments + WhatsApp, Shop Dashboard | [`plans/kanish/`](docs/superpowers/plans/kanish/) |

**Start here:** [`docs/superpowers/plans/2026-10-10-00-team-plan.md`](docs/superpowers/plans/2026-10-10-00-team-plan.md)

## Google technology

| Required | How AIRA uses it |
|---|---|
| **Gemini** | Live (voice), Robotics-ER 2 (counting, pointing, identifying), 3.8 Flash (invoice and cash reads) |
| **Cloud Run** | aira-live: voice orchestrator, tools, business engine, webhooks, jobs |
| **Firebase** | Hosting (app + dashboard), Auth, App Check, Cloud Messaging, Remote Config |
| **Firestore** | System of record: products, stock ledger, orders, deliveries, sales, payments, memory, audit |

Also used: Pub/Sub → BigQuery (history + demand forecasting), Cloud Scheduler, Cloud Storage, Secret Manager, Cloud Build, Cloud Monitoring, and MediaPipe on the phone.

## Deployed

The URLs will be added after the foundation plan, Task 8.
