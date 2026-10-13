<p align="center"><img src="shared/assets/logo/aira-logo-512.png" alt="AIRA logo" width="220"></p>

<h1 align="center">AIRA</h1>
<p align="center"><b>A blind person can run their own shop. Alone.</b><br>
Google Cloud AI Builder Cup 2026 · Team Techjays · Theme: Sustainability &amp; Social Impact (Accessibility)</p>

**A**rtificial Intelligence: understands the world. · **I**ntelligent Interaction: communicates naturally. · **R**eal-world Guidance: helps users navigate. · **A**ccessibility: supports independence.

AIRA is a voice-first assistant that lets a blind shopkeeper run a small shop alone. He wears a phone on his chest and one earphone. He talks, and AIRA listens, looks through the camera and answers in whatever language he spoke: Tamil, English, Hindi or any other, switching whenever he switches. It orders stock from vendors, counts deliveries, checks bills and cash, guides his hand to the right product, confirms UPI payments and keeps the stock book. Gemini understands and sees. Plain, tested code does the maths and keeps the records in Firestore. Our demo shop is **Sunrise Dairy**, which sells Aavin milk, Sakthi curd and Arun ice cream.

## Try it

| What | Link |
|---|---|
| AIRA phone app (judge demo mode) | https://aira-shop.web.app |
| Shop Dashboard (live, read-only) | https://aira-shop.web.app/dashboard |
| Architecture | [shared/docs/architecture.md](shared/docs/architecture.md) |

### Use AIRA on iPhone

AIRA runs in Safari. You do not need an app from the App Store.

1. Open https://aira-shop.web.app in **Safari**.
2. Tap **Share**, then **Add to Home Screen**, then **Add**. Open AIRA from the new icon. It opens full screen.
3. Tap **Tap to start** once each time you open AIRA. iPhone only turns on sound, speech and the motion sensors after a tap.
4. When iPhone asks, allow the **Camera**, the **Microphone** and **Motion & Orientation**. If you refuse motion, AIRA still works. It just cannot tell you to tilt the phone, and the bump guard only warns about things coming closer.
5. Go to **Settings → Display & Brightness → Auto-Lock** and choose **Never** while you work. AIRA also asks iPhone to keep the screen on, but Auto-Lock is the safer choice.
6. iPhone has no vibration for web apps. Every warning still plays its beep and is spoken, so keep one earphone in or the volume up.
7. Keep the Ring/Silent switch on Ring. In Silent mode iPhone can mute AIRA's beeps until the microphone is on.

## What AIRA does

| # | Sam says or does | AIRA does |
|---|---|---|
| 1 | "Order 30 litres Sakthi curd." | Reads the order back and waits for "yes" |
| 2 | (after "yes") | Sends the order to the vendor on **WhatsApp** (Cloud API). Telegram is optional. A `wa.me` link is the fallback. The vendor's reply is spoken to him |
| 3 | Puts the delivered packs in the fridge | **Counts them live** with Gemini Robotics-ER 2: "28 counted. 2 short?" |
| 4 | Holds up the paper invoice | Reads it **twice with two different models**. Code compares both reads with the order and the count |
| 5 | Pays the vendor | Tells him which notes to take, checks the cash in his hand and records the payment |
| 6 | Customer: "2 litres curd." | Guides his **hand** to the curd and checks the pack he picked up |
| 7 | Customer pays | **Cash:** checks the notes and works out the change in code. **UPI:** listens to the shop's payment soundbox and matches the amount |
| 8 | (every sale and delivery) | Updates the **stock ledger** exactly once per event |
| 9 | (automatic) | Speaks **low-stock, expiry and demand forecast** alerts and suggests a reorder |
| ∞ | Walks around the shop | **Bump guard:** the phone vibrates and beeps before he walks into a crate, an open fridge door or a shelf corner. It runs on the phone, with no network |

## Google stack

| Part | How AIRA uses it |
|---|---|
| **Gemini Live** `gemini-3.8-live` | The voice. Listens, talks, can be interrupted, and calls AIRA's tools |
| **Gemini Robotics-ER 2** `gemini-robotics-er-2-preview` | Points at and counts products. Falls back to `gemini-3.8-flash` if it is slow or down |
| **Gemini 3.8 Flash** `gemini-3.8-flash` | Reading: invoice and cash, first read |
| **Gemini 3.6 Flash** `gemini-3.6-flash` | Verify: an independent second read from a different model generation |
| **Cloud Run** | `aira-live`, the FastAPI backend in `asia-south1`. Holds the WebSocket voice session, tools, business engine and webhooks |
| **Firestore** | The system of record, in database `aira`. Every stock, money and state change is a transaction |
| **Firebase Hosting** | One site, `aira-shop`: the phone app at `/` and the Shop Dashboard at `/dashboard` |
| **Secret Manager** | Holds the Gemini API key and optional messaging tokens |
| **Artifact Registry** | Stores the `aira-live` container images |
| **GitHub Actions + Workload Identity Federation** | Tests and deploys on every push to `main`. Keyless: no JSON service account key exists |
| **MediaPipe** (on the phone) | Hand Landmarker tracks his fingertip for hand guidance. Object Detector powers the bump guard |

Model IDs live in one file: `backend/aira_live/models.py`.

## Repository layout

```
frontend/            npm workspace root (package.json, package-lock.json)
  app/               AIRA phone app (Vite + TypeScript + Preact, installable PWA)
  dashboard/         Shop Dashboard (Vite + TypeScript + Preact, Firestore realtime)
backend/             aira-live on Cloud Run (Python 3.12, FastAPI, Gemini Live)
  aira_live/         voice session, tools, vision, memory, business engine, messaging
  tests/             pytest suite (runs offline with fakes)
shared/contracts/    shared WebSocket protocol + Firestore types + JSON fixtures (npm package, file: dependency of both apps)
shared/catalog/      demo shop products and suppliers (sunrise_dairy.json) + schemas/
shared/infra/        firebase.json + .firebaserc, Firestore rules and indexes, GCP bootstrap, keyless deploy setup
shared/scripts/      seed_firestore.py, build_hosting.sh
shared/bench/        benchmark data formats and scorers
shared/docs/         team-rules.md, design spec, plans, architecture, research
shared/assets/logo/  AIRA logo
shared/.env.example  template for the local .env
.github/workflows/   ci.yml (pull requests) · deploy.yml (push to main)
```

The repo root holds only these folders plus this README and `.gitignore`.

## Run it locally

You need Node 20+, Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Settings. Leave GEMINI_API_KEY empty and AIRA_MOCK=1 to run in mock mode.
#    Scripts read .env at the repo root first, then shared/.env.
cp shared/.env.example .env

# 2. Backend on http://localhost:8080 (WebSocket at ws://localhost:8080/ws)
cd backend
uv sync
uv run uvicorn aira_live.main:app --reload --port 8080 --env-file ../.env

# 3. Phone app (new terminal). npm runs inside frontend/, the workspace root.
#    The app connects to ws://localhost:8080/ws by default.
cd frontend
npm ci
npm run dev -w app

# 4. Optional: Shop Dashboard. Set the VITE_FIREBASE_* values,
#    or VITE_USE_EMULATOR=1 with the Firestore emulator on port 8085.
npm run dev -w dashboard

# 5. Optional: seed the demo shop into Firestore database "aira" (from the repo root)
uv run --project backend python shared/scripts/seed_firestore.py
```

## Tests

```bash
cd backend && uv run pytest -q                        # backend: business engine, tools, protocol, rules
cd frontend && npm test && npm run typecheck          # app + dashboard (vitest, tsc)
cd shared/contracts && npm ci && npm test             # the shared contracts package
```

The default suites run offline. Real Gemini calls only run in opt-in tests (`AIRA_LIVE_TESTS=1`).

Accuracy and latency numbers are measured in `shared/bench/results`. We do not quote numbers we have not measured.

## Deploy

Every push to `main` deploys through [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml):

1. Run all tests.
2. Sign in to Google Cloud with Workload Identity Federation. Only `main` of this repository can deploy.
3. Build the `aira-live` image, push it to Artifact Registry and deploy it to Cloud Run (`asia-south1`).
4. Build the app and the dashboard. The backend URL is injected at build time.
5. Deploy both to Firebase Hosting (`firebase deploy --config shared/infra/firebase.json`).

No project ID, key or service URL is stored in this repository. `shared/infra/setup_github_deploy.sh` documents the one-time GCP setup.

## Safety principles

- **The model proposes, the backend commits.** Gemini never writes stock, money or state. Tools call plain code that checks the request and commits it in a Firestore transaction with an idempotency key. A retried call never deducts stock twice.
- **Confirm before consequence.** Ordering, paying and completing a sale always wait for a spoken "yes".
- **Evidence before state.** An order is not a delivery. An invoice is not proof of receipt. A saved location is not proof the product is still there. A payment counts only after a cash count, a soundbox announcement or the owner's clear confirmation.
- **Uncertainty is allowed; invention is not.** If two reads disagree, AIRA says "not sure" and asks for another look. It never invents a count or an amount.
- **Maths in code.** Totals, change and stock are computed in code, never by the model.
- **No counterfeit claims.** Cash reading is help, not a fake-note detector.
- **Payments are cash or UPI only.** A customer's payment screenshot is never trusted.

## Team

**Techjays**

| Member | Area |
|---|---|
| Saravana Rajan B (lead) | Contracts, Gemini Live voice session, cloud and deploy |
| Ishwarya | Phone app, voice, camera, hand guidance, bump guard, demo mode |
| Sarmitha | Vision with Gemini Robotics-ER 2, invoice and cash reads, memory |
| Kanish | Business engine, orders, deliveries, payments, WhatsApp, Shop Dashboard |
