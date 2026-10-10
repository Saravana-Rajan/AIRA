# AIRA docs

![AIRA logo](../assets/logo/aira-logo.png)

**AIRA**: a voice-first assistant that lets a blind person run a small shop alone. First shop: *Murugan Dairy* (Aavin milk, Sakthi curd, Arun ice cream).

- **Repo:** https://github.com/Saravana-Rajan/AIRA
- **Event:** Google Cloud AI Builder Cup 2026 · Theme: Sustainability & Social Impact
- **Team:** Techjays (Saravana, Ishwarya, Sarmitha, Kanish)

## Start here

1. [superpowers/specs/2026-10-10-aira-design.md](superpowers/specs/2026-10-10-aira-design.md): the design spec.
2. [superpowers/plans/2026-10-10-00-team-plan.md](superpowers/plans/2026-10-10-00-team-plan.md): who builds what, the timeline and the git workflow.
3. [superpowers/plans/2026-10-10-00-interfaces.md](superpowers/plans/2026-10-10-00-interfaces.md): the exact shared names every plan uses.
4. Your own plan folder, below. Run it with Claude Code.

## Ideas (`ideas/`)

| Doc | What it covers |
|---|---|
| [00-ideas-catalog.md](ideas/00-ideas-catalog.md) | Every AIRA feature with status (★ demo · ● build · ○ roadmap · ✕ dropped), owner and where it's specified |
| [01-pitch.md](ideas/01-pitch.md) | The pitch, why it matters, and the business model |
| [02-murugan-day.md](ideas/02-murugan-day.md) | Murugan's day at his dairy shop, step by step |
| [03-judges-qa.md](ideas/03-judges-qa.md) | Likely judge questions with our answers |
| [04-demo-video-script.md](ideas/04-demo-video-script.md) | The 3:50 shot list, filming method, consent and schedule |

## Hackathon (`hackathon/`)

| Doc | What it covers |
|---|---|
| [rules-and-rubric.md](hackathon/rules-and-rubric.md) | Timeline, eligibility, prizes, theme, judging rubric 40/25/25/10, mandatory tech |
| [submission-checklist.md](hackathon/submission-checklist.md) | Every required item with an owner and due date, plus freeze rules |
| [official/](hackathon/official/README.md) | Notes from the official event pages: overview, prizes, eligibility, submission, terms, themes |

## Research (`research/`)

| Doc | What it covers |
|---|---|
| [aira-research.md](research/aira-research.md) | Prior art for shop owners, India context (NHFDC, kirana), verified Google model/API facts, WhatsApp/Telegram ordering notes |

## Plans (`superpowers/plans/`)

| Plan | Owner | What it covers |
|---|---|---|
| [2026-10-10-00-team-plan.md](superpowers/plans/2026-10-10-00-team-plan.md) | All | Ownership, timeline, milestones, git and Claude Code workflow |
| [2026-10-10-00-foundation.md](superpowers/plans/2026-10-10-00-foundation.md) | Saravana (first) | Contracts, mock `aira-live`, app + dashboard skeletons, Firestore, bootstrap |
| [2026-10-10-00-interfaces.md](superpowers/plans/2026-10-10-00-interfaces.md) | All | Shared names, signatures and repo layout |
| `saravana/01-live-voice-orchestrator.md` | Saravana | Gemini Live bridge, tool registry, confirm flow, tool harness |
| `saravana/02-cloud-data-analytics.md` | Saravana | Firestore rules, Pub/Sub, BigQuery forecast, Scheduler jobs, Remote Config, FCM, CI |
| `saravana/03-submission.md` | Saravana | Deck, description, video, user validation, submission |
| `ishwarya/01-app-shell-voice.md` | Ishwarya | App shell, onboarding, voice I/O, gestures, earcons, haptics, offline queue |
| `ishwarya/02-camera-guidance.md` | Ishwarya | Camera capture, fingertip tracking, guidance tones, face blur |
| `ishwarya/03-demo-mode-apk-bench.md` | Ishwarya | Judge demo mode, APK, benchmark runner |
| `sarmitha/01-vision-er2.md` | Sarmitha | Gemini helper, counting, product identification, pointing |
| `sarmitha/02-invoice-cash.md` | Sarmitha | Invoice and cash reads (two independent passes) |
| `sarmitha/03-memory-product-find.md` | Sarmitha | Spatial / episodic / preference memory, product find and location tools |
| `kanish/01-business-engine.md` | Kanish | States, ledger, sales, payments, money, soundbox parser, reconcile, expiry, audit |
| `kanish/02-orders-delivery-payments-whatsapp.md` | Kanish | Order, delivery, payment and sale tools; WhatsApp / Telegram / click-to-chat |
| `kanish/03-shop-dashboard.md` | Kanish | Shop Dashboard web app |
