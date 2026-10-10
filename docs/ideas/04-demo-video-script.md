# AIRA — Demo Video Script ("Murugan's Day")

**Length:** 4:00 maximum, **target 3:50**. The rules text says "3 to 4 minute video demonstration", but the submission form's field label says "up to 3 minutes" and the deck template says "3 Minutes". The team chose 4 minutes. **Confirm with support@hack2skill.com before uploading**, and keep a 2:59 cut ready in case the organiser asks for it.

> ⚠️ **The official T&C say videos must be "strictly below three minutes", and the T&C override the dashboard text.** Plan (`saravana/03-submission.md`):
> 1. Email support on **Oct 11** asking for written approval of a 4:00 video.
> 2. **If there's no written OK by Oct 16, 18:00, upload a ≤ 2:55 cut.** It keeps the hook, WhatsApp order, delivery count + invoice mismatch, guided sale + UPI soundbox, and the dashboard/proof shot; vendor payment and expiry become 5-second shots.
>
> Shoot enough footage for both cuts on Oct 17.

**One story, real footage.** Murugan is blind and runs a small dairy shop (Aavin milk, Sakthi curd, Arun ice cream). Every scene shows the working app. No mockups, no scripted fake responses, and no feature montage.

## Shot list

| Time | Shot | Spoken lines (all get English captions) | On-screen caption | What it shows |
|---|---|---|---|---|
| 0:00–0:12 | Early morning. Murugan unlocks the shutter with his white cane in hand. Phone on a chest mount, one earphone in | Murugan: *"Every morning I open my shop alone."* | "Murugan is blind. He runs Murugan Dairy." | Hook: a blind owner, a real shop |
| 0:12–0:25 | He double-taps the phone on his chest | AIRA: *"Good morning Murugan. Sakthi curd is low, only 3 litres left."* | "AIRA: a shop assistant that sees, remembers and checks" | Low-stock memory |
| 0:25–0:40 | He speaks to AIRA | Murugan: *"Order 30 litres Sakthi curd from the Sakthi vendor."* AIRA: *"30 litres of Sakthi curd from Selvam, on WhatsApp. Shall I send it?"* Murugan: *"Yes."* | "The model proposes. Murugan confirms. Then the backend acts." | Voice order, confirm before acting |
| 0:40–0:55 | Phone B: the vendor's phone receives the WhatsApp message. The vendor replies. Cut back to Murugan | Vendor (typed): "OK, 6 pm." AIRA: *"Selvam confirmed. Delivery at 6 pm."* | "Sent automatically via the WhatsApp Business Cloud API. The reply is spoken back." | WhatsApp order + webhook reply |
| 0:55–1:20 | Evening. The vendor brings a crate. Murugan puts the curd packs into the fridge one by one | AIRA: *"1… 2… 3…"* *"…27, 28."* *"I counted 28 packs. The order was 30. Are 2 missing?"* Murugan: *"Count again."* AIRA: *"Still 28. I've recorded 28 received and 2 short."* | "Live counting with Gemini Robotics-ER 2. Only verified stock is recorded." | Live count, `DISCREPANCY` |
| 1:20–1:40 | Murugan holds up the vendor's paper bill | AIRA: *"The bill says 30 litres, ₹1,050. Only 28 arrived. The bill should be ₹980."* Murugan (to vendor): *"Two packs are short, brother."* Vendor: *"Sorry, I'll correct it."* | "Two independent reads. The arithmetic is checked in code." | Invoice check |
| 1:40–2:05 | Murugan takes cash from the cashbox | AIRA: *"₹500 notes are in the left slot. Take one… now a ₹500 and some smaller notes."* He shows the notes. AIRA: *"₹500, ₹200, ₹200, ₹50, ₹20, ₹10. That's ₹980 ✓."* | "Cash checked with confidence. No counterfeit claims." | Supplier payment, cash read |
| 2:05–2:20 | He holds one pack and says where it goes | Murugan: *"This is the Sakthi curd shelf."* AIRA: *"Saved: Sakthi curd, fridge 2, middle shelf, left."* | "Spatial memory, confirmed by the user" | Location memory |
| 2:20–2:45 | Next morning. A customer arrives | Customer: *"Anna, 2 litres curd."* AIRA: *"Sakthi curd is in fridge 2, middle shelf. Move your hand right… lower… stop."* (guidance tones) *"You're touching Sakthi curd 1 litre ✓. Take one more."* | "Hand guidance: ER 2 pointing + fingertip tracking" | Guided product pick |
| 2:45–3:00 | Customer pays cash | AIRA: *"₹70. The customer gave ₹100. Give ₹30 change."* Murugan shows the change. AIRA: *"₹20 and ₹10. Correct ✓."* | "Change is computed in code" | Cash sale |
| 3:00–3:15 | A second customer pays by UPI. The shop's payment soundbox speaks | Soundbox: *"Received rupees seventy."* AIRA: *"₹70 UPI received ✓. Sale complete. Stock: 24 litres."* | "UPI is confirmed by the shop's payment soundbox, not by the customer's screen" | Soundbox UPI check, stock deducted exactly once |
| 3:15–3:25 | Murugan asks about expiry and tomorrow's order | AIRA: *"6 curd packs expire tomorrow. Sell them first. You sell about 26 litres a day, so tomorrow's suggested order is 30 litres."* | "BigQuery demand forecast + expiry scan" | Expiry + forecast |
| 3:25–3:40 | Screen capture: the Shop Dashboard (live stock, sales, the discrepancy, the audit trail), then 3 seconds of the Cloud Run console and Firestore | Voice-over: *"Every action is recorded with evidence. Built on Gemini, Cloud Run, Firestore and Firebase."* | "Proof of deployment: Cloud Run · Firestore · Firebase Hosting" | Dashboard + Google Cloud proof |
| 3:40–3:50 | Murugan behind the counter, smiling. Numbers on screen | Murugan: *"Now I run my shop myself."* | Benchmark numbers (count accuracy, invoice errors caught, sale time with vs without AIRA), then "AIRA" | Proof + emotion |

The ₹ amounts above are placeholders. Use the shop's real prices on filming day and keep the arithmetic consistent across the scenes.

## Filming method

- **Phone A**, on Murugan's chest, runs AIRA and screen-records with **internal audio**, so AIRA's voice is captured clearly.
- **Phone B**, held by a teammate, films from the side so viewers can see Murugan's hands, the fridge, the bill and the cashbox.
- **Edit:** split screen (Phone A view + Phone B view) for the counting, guidance and cash scenes. Full frame for the hook and the closing.
- **Location:** a real small dairy shop with real **Aavin, Sakthi and Arun** products. Check product names and packaging on filming day.
- **Captions:** English for every spoken line, including Tamil lines, since all submission materials must be in English. Big, high-contrast text.
- **Real footage only.** Every AIRA response in the video comes from the working app. If a take fails, reshoot; never fake it.

## People, consent and safety

- The main participant is a blind person. A real blind shop owner is ideal; otherwise a blind participant in a cooperating shop. Get **written consent** for filming and publishing.
- Customers and the vendor are teammates or consenting volunteers. No bystanders' faces without consent; blur them otherwise.
- The shop owner's written permission to film on the premises.

## Schedule

| When | What |
|---|---|
| By Oct 15 | Shop, participant and permissions confirmed. Products and prices noted |
| Oct 16 | Rehearsal run through the whole story in the shop |
| **Oct 17** | **Shoot** (morning and evening light; the "next morning" scenes can be shot the same day) |
| Oct 17–18 | **Edit** in CapCut, DaVinci Resolve or Google Vids. Add captions and numbers |
| Oct 18 | Upload to **YouTube** (public or unlisted), check the link plays logged out, then paste it into the form |

## Checks before upload

- [ ] Length 3:50 (and a 2:59 cut ready)
- [ ] Every spoken line has an English caption
- [ ] The Cloud Run console + Firestore + Shop Dashboard shot is included
- [ ] All shown features exist in the live app the judges will open
- [ ] Consent forms signed and stored
