# AIRA: deck content (16 slides, official AI Builder Cup template)

Paste each section into the matching slide of a copy of the official 16-slide template
(https://docs.google.com/presentation/d/13rg7vW43mEH6DkpuusAE6fdoEylSFz8waNpE4RLzoUg). Keep all 16 slides.
Text marked `<fill: ...>` is filled on Oct 17 from measured or computed sources only. The checklist at
the end lists every fill. `scripts/tests/test_deck_content.py` guards the slide count, the rubric
keywords and the honesty rules, so run it after every edit.

## Slide 1: Team details
- **Team name:** Techjays
- **Team leader name:** Saravana Rajan B
- **Problem statement:** Sustainability & Social Impact (Accessibility). A blind person who owns a small shop
  cannot order stock, check deliveries and bills, find products, or verify cash and UPI payments without a
  sighted helper.

## Slide 2: The problem in one slide
(Replace the template's download note with this content; keep the slide so the deck stays 16 slides.)
- India has about 12 million kirana stores (Britannia A-Eye press coverage, 2025), and the government's
  NHFDC loans exist so that people with 40%+ disability can own one (see `docs/research/aira-research.md` §2;
  quote no headcount of blind people without a source).
- The government's NHFDC self-employment loans exist so people with 40%+ disability can start a shop.
- But running one alone is still blocked by everyday visual tasks: counting a delivery, reading a vendor's
  bill, finding the curd in the fridge, checking cash and UPI. Today the answer is "bring a sighted helper".
- Existing assistive apps describe a scene. None of them runs the owner's business loop.

## Slide 3: Brief about the idea
**AIRA lets a blind person run a small shop alone.** The owner wears a chest-mounted phone and talks.
AIRA listens (Gemini Live), sees (Gemini Robotics-ER 2 and Gemini Flash), remembers (Firestore) and keeps
exact books in deterministic code. It places supplier orders on Telegram, counts deliveries, checks bills,
guides the owner's hand to the right product, checks cash and UPI soundbox payments, and warns about low stock
and expiry. This is a meaningful use of Generative AI for accessibility, and a functional prototype deployed
on Google Cloud (Cloud Run, Firestore, Firebase Hosting). It is built around one shop: Murugan Dairy
(Aavin milk, Sakthi curd, Arun ice cream).

## Slide 4: Opportunities
**How is it different from existing ideas?**
- Voice billing for blind cashiers (Devfolio "Voice Automated Billing System for Blind People", Blind Relief
  Association café): billing only, with no stock, delivery, bill or physical guidance.
- Revel POS Accessibility Mode (2014): accessible iPad billing, with no AI and no physical tasks.
- Britannia A-Eye (2025, Gemini Live): helps blind **shoppers**. AIRA serves the blind **owner**.
- Original contribution: to our knowledge, the first end-to-end owner loop. Order, delivery count, invoice
  check, pay the vendor, guided sale, payment check, stock update, reorder.

**How will it solve the problem?** Every visual step of running the shop becomes a spoken, verified step.
The model proposes; deterministic code commits only after the owner says "yes". Evidence (count, bill,
soundbox announcement, cash read) is required before any state changes.

**USP:** a robotics spatial model (Robotics-ER 2) guiding a human's hand to stock; an honest "not sure"
instead of a guess; stock that changes exactly once (Firestore transactions + idempotency keys).

## Slide 5: List of features
1. Order stock by voice: AIRA reads the order back, waits for a spoken "yes" and sends it to the vendor on
   Telegram (the AIRA bot; the vendor joins by opening one deep link). The vendor's reply is spoken back. A
   vendor not on the bot yet gets a pre-typed WhatsApp click-to-chat message, sent with one tap.
2. Live delivery count (one by one or by crate) with read-back; shortages flagged; "not sure" when the view
   is unclear.
3. Invoice check: two independent reads by two different Gemini Flash generations; code compares bill vs
   order vs count.
4. Pay the vendor: cash-slot guidance and a cash check.
5. Spatial memory: "This is the Sakthi curd shelf."
6. Guided sale: AIRA hears the customer, guides the hand to the product and verifies the pack in hand.
7. Payment check: cash change computed in code; UPI confirmed by the payment soundbox announcement.
8. Low-stock and expiry alerts, plus a reorder suggestion from the 7-day sales average.
9. End-of-day spoken summary.
10. Bump guard: an on-device obstacle warning (vibrate + beep) while walking in the shop, with no network.
11. Offline sale queue: if the network drops mid-sale, the sale is queued on the phone and synced once.
12. Shop Dashboard (web) for family, helpers and judges: live stock, sales, orders, discrepancies, audit trail.
13. Accessible user experience: Tamil, Hindi and English; double-tap to talk, long-press to stop; works with
    TalkBack; large live captions on screen.

## Slide 6: Process flow
Insert `docs/submission/diagrams/process-flow.png`, rendered from `docs/submission/diagrams/process-flow.mmd`
(render command in the checklist below).
Caption: "One shop loop. Nothing changes state without evidence and the owner's 'yes'."

## Slide 7: Wireframes
- **Phone (one full-screen gesture surface, light theme):**
  - Top bar: the AIRA logo and a status chip (Ready, Listening, Thinking, Counting 12 / 30, Warning, Stop!).
  - Centre: a large voice orb that moves with the mic and speaker level.
  - Live captions: the last thing the owner said and the last thing AIRA said, in 28 px+ high-contrast text.
  - Bottom: the current task card, e.g. "Sale: 2 × Sakthi curd 1 L", "Delivery: 28 / 30 counted".
  - Screen reader label: "AIRA. Double-tap to talk. Long-press to stop."
  - Sample exchange: Owner: "Customer wants 2 litres curd." AIRA: "Fridge 2, middle shelf, left… a little
    lower… that is Sakthi curd 1 litre. Take one more." Soundbox: "₹… received." AIRA: "Payment matched.
    Sale done."
- **Shop Dashboard (`/dashboard`):** header with the logo, "Murugan Dairy" and a live dot. Cards: Today (sales,
  revenue, top product) · Stock (low-stock and expiry badges) · Orders & Deliveries (state timeline, vendor
  replies, discrepancies in red) · Audit trail (time, actor, action, evidence). One column on a phone, two or
  three on a laptop.
- **Judge demo page (`/?demo=1`):** hero with the tagline "A blind shopkeeper runs his own shop. Alone.",
  a "Try AIRA" button and one scenario card per demo scene.

## Slide 8: Architecture diagram
Insert `docs/submission/diagrams/architecture.png`, rendered from `docs/submission/diagrams/architecture.mmd`.
Caption: "Google Cloud architecture: Cloud Run (aira-live, asia-south1) runs the voice orchestrator and the
deterministic business engine. Firestore (database `aira`) is the system of record. Firebase Hosting serves the
phone app and the Shop Dashboard from one site. Secret Manager holds the Gemini key, and GitHub Actions deploys
every push to main through Workload Identity Federation (no keys). Gemini Live handles voice, Gemini
Robotics-ER 2 counts and points, and two Gemini Flash generations read invoices and cash independently. The
model proposes; deterministic code commits."

## Slide 9: Technologies used
- **Gemini:** `gemini-3.8-live` (voice + tool calls, Tamil/Hindi/English, interruptible),
  `gemini-robotics-er-2-preview` (count, point, identify), `gemini-3.8-flash` (invoice and cash read A; fallback
  for count and point), `gemini-3.6-flash` (independent read B and number check, a different model generation so
  the two reads are not correlated). Model fallback is switched from a Firestore runtime config document.
- **Google Cloud:** Cloud Run, Firestore, Secret Manager, Artifact Registry, Cloud Logging.
- **Firebase:** Hosting (one site for the app and the Shop Dashboard), Firebase JS SDK with Firestore realtime
  listeners for the dashboard.
- **On device:** MediaPipe Hand Landmarker (fingertip tracking), Web Audio, IndexedDB offline queue,
  installable PWA (Android packaging as a Trusted Web Activity).
- **Messaging:** Telegram Bot API (order + vendor reply webhook), WhatsApp click-to-chat fallback. WhatsApp
  Business Cloud API: built and tested with fakes, planned next once Meta tokens exist.
- **Backend:** Python 3.12, FastAPI, Google ADK. **Frontend:** TypeScript, Preact, Vite.
- **Delivery:** GitHub Actions with Workload Identity Federation: test, build the container, deploy Cloud Run and
  Firebase Hosting on every push to main.

## Slide 10: Estimated implementation cost
Paste the three lines printed by `python3 scripts/cost_per_sale.py --rate <today's USD to INR rate>`:
- Per sale (about 1 minute of voice, 1 product check, one cash check read twice): `<fill: cost_per_sale.py line 1>`
- Per delivery (30 packs counted one by one, bill and cash read twice): `<fill: cost_per_sale.py line 2>`
- Per shop-month (60 sales/day, 2 deliveries/day): `<fill: cost_per_sale.py line 3>`

Note: "Gemini API only, thinking tokens included. Cloud Run, Firestore and Hosting are shared across shops. The design scales to many
shops on the same services, so per-shop infrastructure cost falls as shops are added."

## Slide 11: Snapshots of the prototype
Six screenshots, each with a one-line caption:
1. Phone on the chest in the shop (hands visible; face only with written consent).
2. Live count in progress ("Counting 12 / 30"): app screen with status chip and captions. Copy the caption
   from the real screenshot; the demo delivery has 28 packs, so a count never shows 29.
3. Invoice check result: "Bill says 30 L, counted 28" (discrepancy).
4. Hand guidance: fingertip and target box overlay (debug view).
5. Shop Dashboard: stock table and audit trail.
6. Google Cloud console: Cloud Run `aira-live` revision and Firestore database `aira` collections. Crop the
   project ID, the project number and the `*.run.app` service URL out before pasting.

## Slide 12: Prototype performance report / benchmarking
Paste `docs/submission/bench-table.md`, generated by `python3 scripts/bench_table.py` from the measured CSV
files in `bench/results/` (n shown per row). Rows from spec §7: count accuracy, count abstain rate, invoice
discrepancy catch rate, cash-total accuracy, product-identification accuracy, guidance time to touch, soundbox
parse accuracy, sale time with and without AIRA, task completion, p50/p95 latency (first audio, ER 2 count,
invoice check), cost per sale, uptime. A row still "pending" on Oct 17 stays "pending" or is removed; it is
never estimated.

Add underneath:
- "With AIRA vs without (user study, n = `<fill: participant count from bench/results/user_study.csv>`): sale
  time and task completion", from `bench/results/user_study.csv`.
- "Reliability tests passed: missing goods, uncertain count (abstains), duplicate tool call (stock deducted
  once), network loss mid-sale (queued, synced once), unverified payment (stays PENDING), ER 2 timeout
  (Flash fallback)." Keep only the tests that pass in the final build.
- Participant quote: `<fill: quote from docs/submission/user-validation/data-sheet.csv, with quote consent>`.

These are measurable results from the functional prototype on a named mid-range Android phone
(`<fill: phone model used in bench/results/*.csv>`).

## Slide 13: Additional details / future development
- Native Android background service; iPhone support.
- More shop types (kirana, tea stall) and barcode-assisted stocking.
- Connected fridge temperature sensors.
- Direct bank UPI APIs instead of the soundbox.
- Multi-shop owners and distributor integration (auto-replenishment).
- Offline on-device model for counting.
- Invoice and evidence images in Cloud Storage with 30-day retention, faces blurred on the phone before upload.
- Family alerts on a helper's phone (Firebase Cloud Messaging) and owner sign-in (Firebase Auth) once AIRA
  serves more than one shop.
- Sales history in BigQuery with Pub/Sub event streams for demand forecasting across shops.

Scalability: one Cloud Run service and one Firestore database serve many shops (one document tree per shop);
raise the Cloud Run instance limit and add session routing as shops are added.

## Slide 14: Links
- GitHub: https://github.com/Saravana-Rajan/AIRA
- Demo video: `<fill: "video" in docs/submission/links.json>`
- Live app (with judge demo mode): `<fill: "app" in docs/submission/links.json>`
- Shop Dashboard: `<fill: "dashboard" in docs/submission/links.json>` (the app URL + `/dashboard`)

## Slide 15: Business model, privacy and safety
**Business model (sustainable, free for the blind owner):**
- Funded through NHFDC and state disability self-employment schemes (AIRA bundled with the loan) and
  corporate CSR.
- Dairy brands and distributors sponsor AIRA because it reorders their products on time, which raises
  sell-through and cuts stock-outs.
- Banks and UPI soundbox providers partner for merchants who can verify payments by voice.
- Later: a small monthly subscription for sighted small-shop owners (voice inventory, ordering, forecasting).

**Privacy and safety:**
- The camera sends frames only when a task needs them (count, point, invoice, cash, product). Frames stay in
  server memory for the live session; Firestore keeps the numbers read and the audit trail, not the images.
  No customer personal data is stored. Paid Gemini API tier, so shop images are not used for training.
- AIRA never orders, pays or completes a sale without a spoken "yes". Uncertain counts and reads say
  "not sure". Cash recognition is assistance only: AIRA makes no claim to spot fake notes.
- Social impact: a blind owner keeps the shop's income without a paid sighted helper.

## Slide 16: Thank you
"AIRA: a blind owner, a phone on the chest, and a shop that runs. Thank you."
Team Techjays · Saravana Rajan B · Ishwarya · Sarmitha · Kanish

## Before export (Oct 17 checklist, not a slide)
- [ ] File → Make a copy of the official template; keep all 16 slides in order.
- [ ] Slides 5, 7, 8 and 9: for every feature and service named, confirm it works in the deployed build the
  judges will open; otherwise move it to slide 13 (roadmap) or delete it. Check in particular: MediaPipe
  fingertip tracking, the bump guard, the Android Trusted Web Activity package, the Telegram bot (webhook set
  and both vendors linked) and the demand forecast.
- [ ] Vendor channel: the deck says Telegram now and WhatsApp Cloud API as ready and next. If the Telegram bot is
  not live in the deployed build, reword slide 5 #1 and slide 9 to click-to-chat only, remove the "vendor reply
  webhook" and the reply edge from `architecture.mmd`, and re-render.
- [ ] If either `.mmd` file changed, render the diagrams again and insert them on slides 6 and 8:
  `npx -y @mermaid-js/mermaid-cli@11 -i docs/submission/diagrams/process-flow.mmd -o docs/submission/diagrams/process-flow.png -w 1600 -b white`
  `npx -y @mermaid-js/mermaid-cli@11 -i docs/submission/diagrams/architecture.mmd -o docs/submission/diagrams/architecture.png -w 1600 -b white`
  (To use the installed Chrome instead of a downloaded one, add `-p <file>` with a puppeteer config whose
  `executablePath` points at Chrome.)
- [ ] Slide 10: paste the three lines from `scripts/cost_per_sale.py` run with that day's rate.
- [ ] Slide 12: paste `docs/submission/bench-table.md`, the user-study numbers, the quote and the phone model.
- [ ] Slide 14: paste the links from `docs/submission/links.json`.
- [ ] Slide 11: screenshots cropped so no project ID, project number, `*.run.app` URL or personal contact detail
  is visible.
- [ ] File → Download → PDF, saved as `docs/submission/AIRA-deck.pdf` (git-ignored); then run
  `python3 scripts/check_submission.py --description docs/submission/description.txt --pdf docs/submission/AIRA-deck.pdf`
  and confirm `OK    all checks passed` (strictly under 5,000,000 bytes). If it is too big, compress the
  screenshots in Slides and export again.
- [ ] Check the text the AI pre-screen will read: `python3 scripts/check_deck_pdf.py docs/submission/AIRA-deck.pdf`
  (needs `pdftotext` from poppler). It must print `OK    deck PDF: 16 pages, every rubric keyword present`. It
  fails on a lost slide or rubric phrase, a leftover `<fill: ...>` or file path, a service the prototype does
  not run outside slide 13, a counterfeit claim, or the project ID in a screenshot. Read every `WARN` line:
  an image-only slide is invisible to the pre-screen, so give it a text caption.
- [ ] Run `uv run --no-project --python 3.12 --with pytest python -m pytest scripts/tests/test_deck_content.py -q`
  after any edit to this file or the diagrams.
