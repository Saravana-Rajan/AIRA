# AIRA demo script v2: "Sam's Day at Sunrise Dairy"

Judge-facing demo and video script. It replaces the persona in `docs/ideas/04-demo-video-script.md`
(owner **Sam**, shop **Sunrise Dairy**) and keeps the timings, the decision rule, the edit and upload
steps, and the honesty rules from `docs/submission/video-checklist.md`. Where this file and the checklist
differ, the checklist wins on process (consent, export, upload); this file wins on lines and captions.

This is a plan for people to follow. Nothing here records, uploads or sends anything.

**Two cuts from one shoot:**

| Cut | Length | File (git-ignored) | When it is submitted |
|---|---|---|---|
| Main | 4:00 (240 s) hard ceiling, target 3:50 | `docs/submission/aira-demo.mp4` | Only with written approval from support (see the checklist's decision rule) |
| Fallback | 2:55 (175 s) or less, strictly under 3:00 | `docs/submission/aira-demo-short.mp4` | The default |

**One story, real footage.** Sam is blind and runs Sunrise Dairy alone: Aavin milk, Sakthi curd, Arun
ice cream. A phone on a chest mount, one earphone. Every scene shows the deployed app. No mockups, no
scripted responses, no feature montage. Every ₹ amount below is a placeholder: use the shop's real prices
on filming day and keep the arithmetic consistent across scenes. Every number on screen is
**measured in bench/results** or is not shown.

## What the judges must take away

1. **The whole owner loop, not a scene describer.** Order, live delivery count, invoice check, pay the
   vendor, guided sale, UPI soundbox, stock and alerts, and a bump guard while walking.
2. **The model proposes, the backend commits.** Ordering, paying and completing a sale wait for a spoken
   "yes". Stock changes exactly once (Firestore transactions, idempotency keys).
3. **Evidence before state.** An order is not a delivery; a bill is not proof of receipt; a payment is
   confirmed only by a cash count, a soundbox announcement or the owner's explicit confirmation.
4. **AIRA follows the speaker's language with no settings.** Sam talks Tamil, then English, mid-conversation,
   and AIRA follows. Gemini Live converses in 70 languages; AIRA's fixed strings (alerts, hints, captions) are
   shipped in Tamil, Hindi and English.
5. **Vendor orders over Telegram today; WhatsApp Cloud API is next.** The order goes out through the AIRA
   Telegram bot and the vendor's reply is spoken back.
6. **Deployed on Google Cloud.** Cloud Run (`aira-live`, asia-south1), Firestore (database `aira`),
   Firebase Hosting (one site for the app and the Shop Dashboard), Secret Manager.

## Cast, props, set

| Role | Who | Notes |
|---|---|---|
| Sam (owner) | A blind participant, ideally a real blind shop owner | Written consent; face shown only when the face box is ticked (`docs/submission/user-validation/consent-form.md`) |
| Vendor (Sakthi curd) | A teammate | Phone B shows the Telegram chat; blur the chat name and number |
| Customer 1 (cash) and Customer 2 (UPI) | Teammates or consenting volunteers | Customer 1 speaks Tamil; Customer 2 speaks English, so the language switch is natural |
| Props | 28 Sakthi curd 1 L packs, a printed vendor bill for 30, a cashbox with slots, a UPI soundbox, a crate left on the floor for the bump guard | Count never shows 29 or 30; the delivery really has 28 |
| Phone A | On Sam's chest, runs the deployed AIRA build with screen recording and internal audio | Note `git describe --tags` of the deployed build |
| Phone B | Teammate, films hands, fridge, bill, cashbox and the vendor's phone | |
| Laptop | Shop Dashboard at `/dashboard`, then the Cloud Run and Firestore consoles | Project ID, project number, project picker, URL bar and every `*.run.app` URL hidden or blurred |

## Main cut: 4:00 ceiling, target 3:50

Timestamps follow the scene table in `docs/submission/video-checklist.md` (0:00–0:25, 0:25–0:55,
0:55–1:40, 1:40–2:05, 2:05–2:55, 2:55–3:20, 3:20–3:40, 3:40–3:50). Scenes are split into shots inside those
ranges. Every spoken line gets an English caption; Tamil lines get English subtitles.

### Scene 1: 0:00–0:25, Hook: a blind owner opens the shop alone

| Time | Shot | Spoken lines | On-screen caption |
|---|---|---|---|
| 0:00–0:10 | Early morning. Sam walks to the shutter with a white cane. Chest phone, one earphone | Sam: *"Every morning I open my shop alone."* | "Sam is blind. He runs Sunrise Dairy." |
| 0:10–0:17 | He walks in. A crate sits in the aisle. Phone A buzzes and beeps; Sam stops | AIRA: *"Stop. Obstacle ahead."* Sam steps around it | "Bump guard: on the phone, no network. Vibrates and beeps, then speaks." |
| 0:17–0:25 | He double-taps the phone on his chest. Status chip goes Ready, then Listening | AIRA (Tamil): *"Good morning Sam. Sakthi curd is low. Three litres left."* | "AIRA: a shop assistant that listens, sees, remembers and checks" |

### Scene 2: 0:25–0:55, Vendor order over Telegram

| Time | Shot | Spoken lines | On-screen caption |
|---|---|---|---|
| 0:25–0:38 | Sam speaks in Tamil. Phone A captions show his words and AIRA's read-back | Sam (Tamil): *"Order thirty litres Sakthi curd from the Sakthi vendor."* AIRA (Tamil): *"Thirty litres of Sakthi curd from the Sakthi vendor, on Telegram. Shall I send it?"* Sam (Tamil): *"Yes."* | "The model proposes. Sam confirms. Then the backend acts." |
| 0:38–0:48 | Phone B: the vendor's phone receives the message from the AIRA Telegram bot. The vendor taps a reply | Vendor (typed): "OK, 6 pm." | "Sent by the AIRA Telegram bot. WhatsApp Cloud API is next." |
| 0:48–0:55 | Back to Sam. Task card: "Order sent to Sakthi vendor" | AIRA (Tamil): *"The Sakthi vendor confirmed. Delivery at 6 pm."* | "The vendor's reply is spoken back. State: ORDER_CONFIRMED, not DELIVERED." |

The caption "Sent by the AIRA Telegram bot" is used only if the bot really sent it. See the honesty rules.

### Scene 3: 0:55–1:40, Live delivery count and invoice check

| Time | Shot | Spoken lines | On-screen caption |
|---|---|---|---|
| 0:55–1:20 | Evening. The vendor brings a crate. Sam moves packs into the fridge one by one. Split screen: Phone B hands, Phone A status chip "Counting 12 / 30" | AIRA: *"1… 2… 3…"* (speed-up shown) *"…27, 28."* *"I counted 28 packs. The order was 30. Two may be missing. Shall I count again?"* Sam: *"Count again."* AIRA: *"Still 28. Recorded: 28 received, 2 short."* | "Live counting with Gemini Robotics-ER 2. Only verified stock is recorded." |
| 1:20–1:40 | Sam holds up the vendor's paper bill to the chest camera | AIRA: *"The bill says 30 litres, ₹1,050. Only 28 arrived. The bill should be ₹980."* Sam (to vendor): *"Two packs are short, brother."* Vendor: *"Sorry, I'll correct it."* | "Two independent reads by two Gemini Flash generations. The arithmetic is checked in code." |

If the two invoice reads disagree on the day, AIRA says "not sure, show me the bill again". Keep that take:
it demonstrates the rule. Never dub a confident line over it.

### Scene 4: 1:40–2:05, Pay the vendor

| Time | Shot | Spoken lines | On-screen caption |
|---|---|---|---|
| 1:40–1:55 | Sam opens the cashbox. Phone B on the slots | AIRA: *"₹500 notes are in the left slot. Take one. Now ₹200 notes, middle slot."* | "Cash-slot guidance from spatial memory" |
| 1:55–2:05 | Sam fans the notes to the camera | AIRA: *"₹500, ₹200, ₹200, ₹50, ₹20, ₹10. That is ₹980. Correct."* Sam: *"Pay him."* AIRA: *"Paid ₹980 to the Sakthi vendor. Recorded."* | "Cash total checked in code. AIRA reads amounts; it makes no claim about whether a note is genuine." |

### Scene 5: 2:05–2:55, Guided sale, cash change, then UPI soundbox

| Time | Shot | Spoken lines | On-screen caption |
|---|---|---|---|
| 2:05–2:12 | Next morning. Sam holds a pack and names its place | Sam (Tamil): *"This is the Sakthi curd shelf."* AIRA (Tamil): *"Saved: Sakthi curd, fridge 2, middle shelf, left."* | "Spatial memory, confirmed by the owner" |
| 2:12–2:30 | Customer 1 arrives, speaks Tamil. Split screen: Phone B hand and fridge, Phone A fingertip and target box | Customer 1 (Tamil): *"Anna, two litres curd."* AIRA (Tamil): *"Sakthi curd, fridge 2, middle shelf. Move your hand right… lower… stop."* (guidance tones) *"You are touching Sakthi curd 1 litre. Take one more."* | "Hand guidance: Gemini Robotics-ER 2 pointing plus on-device fingertip tracking" |
| 2:30–2:40 | Customer 1 pays cash | AIRA (Tamil): *"₹140. The customer gave ₹200. Give ₹60 change."* Sam shows the change. AIRA (Tamil): *"₹50 and ₹10. Correct. Sale done."* | "Change is computed in code" |
| 2:40–2:45 | Customer 2 arrives and speaks English. **Sam answers in English. AIRA switches with him, no setting touched** | Customer 2 (English): *"One litre curd please."* Sam (English): *"One litre curd."* AIRA (English): *"Sakthi curd 1 litre, ₹70. Cash or UPI?"* Customer 2: *"UPI."* | "Sam switched from Tamil to English mid-conversation. AIRA followed. No language setting. Gemini Live converses in 70 languages." |
| 2:45–2:55 | The shop's payment soundbox announces the payment | Soundbox: *"Received rupees seventy."* AIRA (English): *"₹70 UPI received. Sale complete. Stock: 24 litres."* Task card: "Sale: 1 × Sakthi curd 1 L, ₹70, UPI" | "UPI is confirmed by the shop's payment soundbox, not by the customer's screen. Stock deducted exactly once." |

### Scene 6: 2:55–3:20, Stock, alerts and reorder

| Time | Shot | Spoken lines | On-screen caption |
|---|---|---|---|
| 2:55–3:08 | Sam asks about stock. Phone A shows the Warning chip (orange) | Sam (English): *"What is low? What expires?"* AIRA (English): *"Six curd packs expire tomorrow. Sell them first. Aavin milk 500 ml is low: 8 left."* | "Low-stock and expiry alerts, spoken in the owner's session" |
| 3:08–3:20 | AIRA suggests tomorrow's order | AIRA (English): *"You sell about 26 litres of curd a day. Tomorrow's suggested order is 30 litres. Shall I prepare it?"* Sam: *"Not now."* AIRA: *"Okay. I will not send anything."* | "Demand forecast from Firestore sales. Nothing is sent without a yes." |

### Scene 7: 3:20–3:40, Proof of deployment

| Time | Shot | Spoken lines | On-screen caption |
|---|---|---|---|
| 3:20–3:32 | Laptop: the Shop Dashboard updates live as the last sale lands. Today card, Stock table with the expiry badge, Orders & Deliveries with the 28 vs 30 discrepancy in red, Audit trail | Voice-over: *"Every action is recorded with evidence: the count, the bill, the soundbox line."* | "Shop Dashboard: Firebase Hosting + Firestore realtime listeners" |
| 3:32–3:40 | Cloud Run `aira-live` revision page, then Firestore database `aira` collections. Project ID, project number, picker, URL bar and `*.run.app` URL hidden or blurred; prefer `aira-shop.web.app` and `/dashboard` | Voice-over: *"Built on Gemini, Cloud Run, Firestore and Firebase."* | "Proof of deployment: Cloud Run · Firestore · Firebase Hosting · Secret Manager" |

### Scene 8: 3:40–3:50, Numbers and a quote

| Time | Shot | Spoken lines | On-screen caption |
|---|---|---|---|
| 3:40–3:47 | Sam behind the counter | Sam: *"Now I run my shop myself."* | Two or three numbers, each **measured in bench/results** and produced by `scripts/bench_table.py`: count accuracy, invoice discrepancy catch rate, sale time with vs without AIRA. A metric still `pending` is left out |
| 3:47–3:50 | AIRA logo | (silence) | Participant quote, only with the quote box ticked; otherwise the logo alone |

Spoken-line total stays inside 3:50 with transitions. If the shoot runs long, trim the middle of the count
(scene 3) and the change scene (scene 5) first; never trim the "yes", the "2 short", the language switch or
the soundbox line.

## Fallback cut: 2:55 or less

Same footage, trimmed. The finished file must be **2:55 or less** (175 s). Scene times in seconds, then
the resulting timestamps.

| Scene | Main | Fallback | Timestamps | What stays | What goes |
|---|---|---|---|---|---|
| 1 Hook + bump guard | 25 | 15 | 0:00–0:15 | Cane, the bump guard beep and "Stop", the first "AIRA" line | Shorter walk to the shutter |
| 2 Vendor order (Telegram) | 30 | 20 | 0:15–0:35 | Read-back, the "yes", the vendor's phone, the reply spoken back | The wait for the reply |
| 3 Delivery count + invoice | 45 | 35 | 0:35–1:10 | Count reaching 28 (speed-up shown), "2 short", the bill 30 vs 28 | The recount |
| 4 Pay the vendor | 25 | 15 | 1:10–1:25 | One cash slot, the total ₹980, "Paid" | The second slot |
| 5 Guided sale + UPI | 50 | 40 | 1:25–2:05 | Hand guidance, the Tamil to English switch with the "70 languages" caption, the soundbox match | Spatial-memory save, shorter change scene |
| 6 Alerts + reorder | 25 | 15 | 2:05–2:20 | One alert and the suggested order with "Not now" | The second alert |
| 7 Proof of deployment | 20 | 10 | 2:20–2:30 | Dashboard, then one console shot (identifiers hidden) | The second console |
| 8 Numbers + quote | 10 | 5 | 2:30–2:35 | One number measured in bench/results, or the quote | The rest |

Total 155 s (2:35), plus up to 20 s of transitions, so 2:55 at most. Check it with:

```bash
python3 scripts/check_submission.py --description docs/submission/description.txt \
  --video docs/submission/aira-demo-short.mp4 --max-video 175
```

Expect `OK    all checks passed` with no WARN line. The main cut is checked with `--max-video 240` and is
expected to print one WARN, because it is over 2:59.

## Live judge demo (if there is a stage or a call)

Run it in this order, 6 to 8 minutes, from the deployed app at the Firebase Hosting URL and `/dashboard`.
If anything fails, say so and move on; never read out a result the app did not produce.

1. Open `/?demo=1`: the hero, one scenario card per scene, the "Try AIRA" button.
2. Double-tap. Say the low-stock question in Tamil, then ask a second question in English. Point at the
   captions: no settings were touched.
3. Order 30 litres of curd. Say "yes". Show the Telegram message on the vendor phone and the spoken reply.
4. Count 10 packs into the fridge. Show "Counting 7 / 10" on the chip and "not sure" on a blocked view.
5. Hold up a bill. Show the discrepancy line and the same row in red on the dashboard.
6. Guided pick: one pack, fingertip overlay on screen, "You are touching Sakthi curd 1 litre".
7. UPI: trigger the soundbox, show "Sale complete" and the stock changing once on the dashboard.
8. Walk towards a crate: bump guard vibrates and beeps, airplane mode on, to show it needs no network.
9. Close on the audit trail: each row has time, actor, action and evidence.

## Roadmap slide (shown once, 5 seconds, or spoken in the live demo close)

Title: "Built, next, and after"

- **AIRA Shop Camera (built, off by default):** a fixed CCTV or RTSP camera stream as a second pair of
  eyes for counting and finding products when the chest camera cannot see the shelf. Shipped behind a
  runtime flag that is off in the judge build; it is not shown in the video, so it is a roadmap line, not a
  demo claim.
- **More fixed-string languages:** Gemini Live already converses in 70 languages. AIRA's fixed strings
  (alerts, hints, captions, dashboard labels) ship in Tamil, Hindi and English today; Telugu, Kannada,
  Malayalam and Bengali are next, through the same `i18n` tables.
- **WhatsApp channel:** the WhatsApp Business Cloud API path is built and tested with fakes. It goes live
  for vendor orders and replies once Meta tokens exist; Telegram stays as a channel.
- Later: more shop types, barcode-assisted stocking, direct bank UPI confirmation, multi-shop owners.

## Honesty rules (every cut, every live demo)

- Every AIRA line comes from the working app. If a take fails, reshoot; never dub or fake a response.
- The vendor order caption matches the channel that really sent it: "Sent by the AIRA Telegram bot" only if
  the bot sent it; "Sent with the WhatsApp Business Cloud API" only if the Cloud API sent it; otherwise
  "Pre-typed WhatsApp message, sent with one tap (click-to-chat)", and then the spoken-reply line is cut,
  never dubbed.
- The language switch is real: Sam changes language on camera, and no one touches a setting. If the take
  needed a setting, reshoot.
- "Gemini Live converses in 70 languages" is the only language claim. AIRA's own fixed strings are claimed
  in Tamil, Hindi and English only.
- UPI is confirmed by the shop's payment soundbox. Never claim a bank integration.
- Cash: AIRA reads the total. It never judges whether a note is genuine.
- The count really is 28 of 30. The bill really says 30. Nothing is staged after the fact.
- The forecast caption says it comes from Firestore sales data.
- Numbers on screen come only from `bench/results/*.csv` through `scripts/bench_table.py`. Where a number
  is needed and not yet measured, the slot reads "measured in bench/results" in this script and is left off
  the screen in the cut.
- Only features that exist in the live app the judges will open. The Shop Camera stays on the roadmap slide.
- Console and phone shots: hide or blur the project ID, project number, project picker, URL bar, every
  `*.run.app` URL, the signed-in account and any billing page. Blur phone numbers and chat names on the
  vendor phone.
- Faces and quotes only with the matching consent box ticked. Blur anyone else.
- The persona is Sam at Sunrise Dairy in every cut, caption and slide. No older persona or shop name
  from earlier drafts appears anywhere.

## Checks before upload

- [ ] Main cut 3:50 or less and never over 4:00; fallback cut 2:55 or less. Both exported as 1080p H.264.
- [ ] Every spoken line has an English caption; Tamil lines have English subtitles.
- [ ] The Tamil to English switch and the "70 languages" caption are in both cuts.
- [ ] The Telegram order and the spoken vendor reply are in both cuts, with the matching caption.
- [ ] The bump guard, the 28 vs 30 count, the bill check, the cash total, the guided pick, the soundbox line,
      the alerts and the dashboard are in the main cut.
- [ ] The Shop Dashboard plus Cloud Run and Firestore shots are included, identifiers hidden.
- [ ] Only "Sam" and "Sunrise Dairy" in the caption file; no older persona or shop name from earlier drafts.
- [ ] All shown features exist in the live app the judges will open.
- [ ] Numbers on screen are measured in bench/results; none are estimated.
- [ ] Consent forms signed and stored off-repo.
- [ ] `scripts/check_submission.py` passes for the cut being uploaded.
