# AIRA demo video checklist (shoot Oct 16, edit Oct 17, upload Oct 18)

This is a plan for people to follow. Nothing here records, uploads or sends anything.

**Two cuts from one shoot:**

| Cut | Length | File (git-ignored) | When it is submitted |
|---|---|---|---|
| Main | 3:50 (230 s), never over 4:00 | `shared/docs/submission/aira-demo.mp4` | Only with written approval from support |
| Fallback | 2:55 or less (175 s), strictly under 3:00 | `shared/docs/submission/aira-demo-short.mp4` | The default |

**Sources:**
- Lines and captions: `shared/docs/ideas/04-demo-video-script.md`.
- Scene timings: spec §8.
- Where they differ, this checklist wins:
  - Timings follow spec §8.
  - The forecast caption is "Demand forecast from Firestore sales".
  - Spec §8 calls scene 2 the "WhatsApp order". Aavin and Sakthi now order over Telegram (`shared/catalog/sunrise_dairy.json`), so the scene is the "Vendor order", and its caption depends on how the order was really sent (see Honesty rules).
  - The cash caption is "Cash total checked in code".
  - The shoot is on Oct 16.

## Decision rule (Oct 16, 18:00 IST)

Check the support thread from `shared/docs/submission/support-email.md`.

| Situation at Oct 16, 18:00 IST | Action |
|---|---|
| Support confirmed **in writing** that up to 4:00 is allowed | Submit the 3:50 cut, `shared/docs/submission/aira-demo.mp4`. Save the email as a PDF next to the submission confirmation, off-repo |
| No written answer, or support says under 3:00 | **Submit the 2:55 cut**, `shared/docs/submission/aira-demo-short.mp4` |

- [ ] Decision written in the support-email log, with the time.
- [ ] Edit both cuts anyway. If the approval later turns out to be unclear, the fallback is ready.

## Before the shoot

- [ ] Consent recorded for everyone on camera, using `shared/docs/submission/user-validation/consent-form.md`. Show a face only when the face box is ticked. Use a quote only when the quote box is ticked.
- [ ] The shop owner's written permission to film on the premises.
- [ ] Location: a real small dairy shop, or the user-study mock counter. Stock: Aavin milk 500 ml, Sakthi curd 1 L, Arun ice cream boxes, a cashbox and a UPI soundbox.
- [ ] Note the real prices on filming day. Keep every ₹ amount consistent across scenes.
- [ ] Deployed build tagged; note `git describe --tags`. Film only the real deployed app: no mockups and no scripted responses.
- [ ] Vendor channel decided and tested once, end to end, the day before. Check the Sakthi vendor's `channel` in `shared/catalog/sunrise_dairy.json`:
  - `telegram` (the current setting): `TELEGRAM_TOKEN` is attached to `aira-live`, `shared/scripts/telegram_setup.py` has set the webhook, the vendor phone has opened its Telegram deep link `t.me/<bot>?start=<supplier_id>` and pressed **Start**, and the supplier doc has a `telegramChatId` (steps in `shared/docs/ops/telegram-setup.md`).
  - `whatsapp`: the Cloud API tokens are live and the vendor phone is a registered test recipient.
  - Otherwise click-to-chat: a pre-typed WhatsApp message sent with one tap.
  - Write down the channel used. The order caption depends on it.
- [ ] Delivery props: 28 curd packs, and a printed bill for 30 (the discrepancy moment).
- [ ] Phone A (on the chest): runs AIRA with screen recording and internal audio.
- [ ] Phone B (teammate): films hands, shelf, bill and cashbox from the side.
- [ ] Both phones charged, storage free, Do Not Disturb on.
- [ ] Shoot every scene long enough for the main cut. The fallback cut only trims.

## Shot list: main cut (3:50)

| Time | Scene | Must be visible or audible |
|---|---|---|
| 0:00–0:25 | Hook: a blind owner opens the shop alone | White cane, chest phone, shutter, the first "AIRA" and the low-stock line |
| 0:25–0:55 | Vendor order by voice | Order read-back, the spoken "yes", the vendor phone receiving it in Telegram, the vendor reply spoken back (needs the Telegram bot set up per `shared/docs/ops/telegram-setup.md`; WhatsApp Cloud API is ready and next, not filmed) |
| 0:55–1:40 | Delivery count + invoice check | Count reaching 28, "2 short", the bill for 30 vs 28 received flagged |
| 1:40–2:05 | Pay the vendor | Cash slot guidance, the notes shown, the total read back as correct |
| 2:05–2:55 | Customer sale | Hand guided to the curd, pack confirmed, change computed, then a UPI payment matched to the soundbox announcement |
| 2:55–3:20 | Low stock, expiry and forecast reorder | Spoken alerts and the suggested order |
| 3:20–3:40 | Proof of deployment | Shop Dashboard updating live, then the Cloud Run and Firestore consoles and the Firebase Hosting URL. Before filming the consoles, hide or blur the project picker, the browser URL bar, the project ID, the project number and every `*.run.app` URL; prefer showing `aira-shop.web.app` and `/dashboard` |
| 3:40–3:50 | Numbers and a quote | 2–3 measured numbers from slide 12 and a participant quote |

## Fallback cut (2:55 or less)

Same footage, trimmed. Scene times are in seconds.

| Scene | Main cut | Fallback cut | What to trim |
|---|---|---|---|
| Hook | 25 | 15 | Shorter walk to the shutter |
| Vendor order | 30 | 20 | Keep the read-back, the "yes" and the reply; cut the wait for the reply |
| Delivery count + invoice | 45 | 35 | Speed up the middle of the count (show the speed-up); keep "28" and the bill check |
| Pay the vendor | 25 | 15 | One cash slot, then the total |
| Customer sale (cash + UPI) | 50 | 40 | Keep the hand guidance and the soundbox match; shorter change scene |
| Alerts + reorder | 25 | 15 | One alert and the suggested order |
| Proof of deployment | 20 | 10 | Dashboard, then one console shot (project ID, project number and run.app URL hidden or blurred) |
| Numbers + quote | 10 | 5 | One number or the quote |

Total: 155 s (2:35), plus up to 20 s for transitions. The finished file must be **2:55 or less**.

## Edit (Oct 17)

- [ ] Split screen (Phone B view + Phone A screen) for the count, guidance and cash scenes. Full frame for the hook and the close.
- [ ] English captions for every spoken line. Tamil speech gets English subtitles. Large, high-contrast text.
- [ ] At least one scene switches language mid-conversation (Tamil, then English, then Tamil) and AIRA follows without being asked. No language is picked anywhere on screen.
- [ ] Numbers on screen come only from `shared/bench/results/*.csv` through `shared/scripts/bench_table.py`. A metric shown as `pending` is left out of the video; never put a guessed number on screen.
- [ ] If no measured numbers exist yet, the last scene shows the quote or the AIRA logo, not placeholder numbers.
- [ ] Console and phone shots: crop or blur the project ID, the project number, the project picker, the browser URL bar, every `*.run.app` URL, the signed-in account email and any billing page. Blur phone numbers and chat names on the vendor phone.
- [ ] Export both cuts as 1080p H.264 MP4: `shared/docs/submission/aira-demo.mp4` and `shared/docs/submission/aira-demo-short.mp4`.
- [ ] Check the main cut (should pass with one WARN, because it is over 2:59):

```bash
python3 shared/scripts/check_submission.py --description shared/docs/submission/description.txt \
  --video shared/docs/submission/aira-demo.mp4 --max-video 240
```

- [ ] Check the fallback cut. Expect `OK    all checks passed`, with no FAIL and no WARN line:

```bash
python3 shared/scripts/check_submission.py --description shared/docs/submission/description.txt \
  --video shared/docs/submission/aira-demo-short.mp4 --max-video 175
```

## Upload (Oct 18, before the 18:15 link check in `shared/docs/submission/freeze-runbook.md`)

- [ ] Upload the cut chosen by the decision rule to YouTube as **Public**.
- [ ] Title: "AIRA: a blind owner runs his shop alone (Google Cloud AI Builder Cup 2026, Team Techjays)".
- [ ] Upload the English caption file. Put https://github.com/Saravana-Rajan/AIRA in the video description.
- [ ] Open the link in a private window (logged out). It must play without sign-in.
- [ ] Use that URL as `VIDEO_URL` for `shared/docs/submission/links.json`.

## Honesty rules (every cut)

- [ ] Every AIRA line comes from the working app. If a take fails, reshoot; never dub or fake a response.
- [ ] Console shots: hide or blur the project ID and the project number before filming the Cloud Run and Firestore consoles.
- [ ] The vendor order caption matches the channel that really sent it:
  - "Sent by the AIRA Telegram bot", only if the Telegram bot sent it.
  - "Sent with the WhatsApp Business Cloud API", only if the Cloud API sent it.
  - Otherwise "Pre-typed WhatsApp message, sent with one tap (click-to-chat)".
- [ ] With click-to-chat the vendor's reply lands in the owner's own WhatsApp, so AIRA does not speak it: cut the spoken-reply line and never dub it.
- [ ] UPI is confirmed by the shop's payment soundbox. Never claim a bank integration.
- [ ] Cash: AIRA reads the total; it never judges whether a note is genuine.
- [ ] The forecast caption says it comes from Firestore sales data.
- [ ] Only features that exist in the live app the judges will open.
- [ ] Faces and quotes only with the matching consent box ticked. Blur anyone else.
