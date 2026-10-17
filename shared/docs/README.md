# AIRA docs

AIRA turns a blind person into a business owner who runs a shop and earns by voice. The project README is at the repo root: [../../README.md](../../README.md). This folder holds the deeper material.

## Architecture

| Doc | What it covers |
|---|---|
| [architecture.md](architecture.md) | The three parts (phone app, `aira-live` on Cloud Run, dashboard), system diagram, models and roles, WebSocket protocol, order-to-sale sequence, Firestore data model and states, trust boundaries, deploy pipeline |
| [diagrams/architecture.png](diagrams/architecture.png) ([.mmd](diagrams/architecture.mmd)) | The system diagram as an image (Mermaid source beside it) |
| [diagrams/process-flow.png](diagrams/process-flow.png) ([.mmd](diagrams/process-flow.mmd)) | Order, delivery, invoice, payment and sale flow |

## Ops (`ops/`)

| Doc | What it covers |
|---|---|
| [walking-guidance.md](ops/walking-guidance.md) | "Walk with me": the navigation session, the state machine, timeouts and watchdogs, flags, step-by-step routes from the shop map, safety limits |
| [low-light.md](ops/low-light.md) | Flashlight with confirmation, low-light boost on uploaded frames, "where is the light switch" |
| [handsfree.md](ops/handsfree.md) | Earphone button, volume-button chord and "Hello AIRA" wake word: what is built, flags, platform facts |
| [live-video.md](ops/live-video.md) | The dashboard's Live page: WebRTC with a JPEG fallback, signalling, the Listen audio relay |
| [security.md](ops/security.md) | Shared app key, Origin allowlist, the Live feed's access modes, session caps, vendor webhooks |
| [monitoring.md](ops/monitoring.md) | Uptime check and alert policy to add, Firestore `config/runtime` switches, model fallback runbook |
| [vector-search.md](ops/vector-search.md) | Firestore vector search over products and the last-seen memory (`gemini-embedding-2`) |
| [cctv.md](ops/cctv.md) | Optional fixed shop cameras (built, off until configured in Firestore) |
| [telegram-setup.md](ops/telegram-setup.md) | Telegram bot setup for supplier orders and vendor replies |
| [whatsapp-setup.md](ops/whatsapp-setup.md) | WhatsApp Cloud API setup (in the code; Telegram is the channel in use) |
| [voice-smoke-test.md](ops/voice-smoke-test.md) | Manual check that a spoken question reaches Gemini Live and comes back as voice |

## Proposals (`proposals/`)

| Doc | What it covers |
|---|---|
| [native-android-shell.md](proposals/native-android-shell.md) | Proposal only, nothing native is built: an Android shell for the volume-button trigger and the wake word |
