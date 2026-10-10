# AIRA: the 98-point playbook

**Goal:** score as close to **98/100** as possible on the official rubric: Technical 40 · Impact 25 · Innovation 25 · UX 10.

**Honest note:** 98 means being near-perfect on every criterion, and the result also depends on the other teams. Every item below is something judges can **see or measure**. If an item isn't shown in the video, deck, live link or repo, it earns zero points.

| Outcome | Expected score |
|---|---|
| Built well, plus a real blind participant and benchmarks | ~89–95 |
| Everything in this playbook | 95–98 |
| No real users and no measured numbers | ceiling ~88–90 |

---

## 1. Technical merit + Gen AI + Google Cloud: target 39/40

| Must show | Proof the judges see | Owner |
|---|---|---|
| **AI is essential, not decoration** | An "AI on vs AI off" slide, e.g. counting accuracy without Robotics-ER (manual spoken count) vs with it, and invoice errors caught with vs without the two-read check | Sarmitha + Kanish |
| **Every number is measured** (spec §7) | See the target table below | Each owner, for their own area |
| **It never breaks** | Shown live in the video or a short clip, see the list below | Saravana + Kanish |
| **Real engineering** | A green CI badge in the README, 150+ automated tests, `infra/` deploy scripts, and a Cloud Monitoring dashboard visible in the video | Saravana |
| **Scales** | The multi-shop Firestore design (`shops/{shopId}`), cost per sale (₹) and per shop per month, and a roadmap from 1 → 1,000 shops (multi-instance session registry) | Saravana |
| **Every one of the ~20 Google services has a visible job** | The architecture slide plus a 10-second console montage: Cloud Run, Firestore, BigQuery, Pub/Sub | Saravana |

**Benchmark targets:**

| Metric | Target | Test set |
|---|---|---|
| Count accuracy | ≥ 95% | 200 packs |
| Invoice discrepancy catch | ≥ 90% | 30 bills, 10 with errors |
| Cash total accuracy | ≥ 95% | 40 images |
| Product identification | ≥ 95% | all 10 SKUs; milk vs buttermilk, ghee 200 vs 500 ml |
| Soundbox UPI parse | ≥ 98% | 20 clips |
| First voice reply | p50 < 1.0 s | |
| Bump alert | p50 < 150 ms | |
| Hand guidance, time to touch | median < 8 s | 20 trials |

**What "never breaks" means:**

- Retrying the same sale does **not** deduct stock twice (idempotency).
- If the network drops mid-sale, AIRA says so and recovers.
- If Robotics-ER times out, AIRA switches to Flash (Remote Config fallback).
- An unclear count makes AIRA ask for another view instead of guessing.

## 2. Problem fit + impact: target 25/25

| Must show | Proof | Owner |
|---|---|---|
| **A real blind shop owner runs AIRA for 2–3 days** | A logged pilot, e.g. "86 sales, helper needed 0 times, 3 wrong bills and 1 wrong change caught" | Saravana (arranges), all |
| **With vs without AIRA** | Time per sale, errors, how often a sighted helper was needed, compared with the same tasks without AIRA | All |
| **An outside voice** | A support letter from an NHFDC office, a state disability-welfare office or a blind association | Saravana |
| **A scale story** | ~12M kirana stores · NHFDC self-employment loans · cost per shop per month · UN SDG 8 (decent work) and SDG 10 (reduced inequalities) | Saravana |
| **A business model** | Free for blind owners (NHFDC/state schemes + CSR) · dairy brand/distributor sponsorship · banks / UPI soundbox partners · later, a subscription for sighted small shops | Saravana |

## 3. Innovation and creativity: target 24–25/25

- **The one line judges remember:** *"Google built Robotics-ER for robot hands. AIRA uses it to guide a blind shopkeeper's hands, and to run his whole shop."*
- **An honest prior-art table** (see `docs/research/aira-research.md`): voice billing for blind staff, accessible tablet checkout, Britannia A-Eye (for shoppers) and Be My AI / Lookout, vs AIRA's full owner loop: order → live count → invoice check → pay vendor → guided sale → cash/UPI check → stock → reorder.
- **Three "wow" moments, shown live in the video:**
  1. The invoice mismatch is caught: "Bill says 32 L, only 30 arrived."
  2. The hand is guided to the right pack, telling milk from buttermilk apart.
  3. The vendor's WhatsApp reply is spoken: "Selvam confirmed: delivery at 6 pm."
- **Bonus:** the shop-floor **bump guard** (vibration + beep before he hits a crate or an open fridge door).

## 4. UX and design: target 10/10

- **Co-designed with a blind user:** a System Usability Scale score of **≥ 80**, plus their own words in the video.
- **Accessible on the phone:** works with **TalkBack**; natural **Tamil** voice (with Hindi and English); only **double-tap** to talk and **long-press** to stop; short sentences; can be interrupted.
- **Dashboard:** Lighthouse accessibility **≥ 95**; keyboard and contrast verified.
- **Judge "Try AIRA" demo mode:** works on a laptop with no sign-in, using sample media for every scene.

## 5. Presentation (multiplies every score above)

| Item | Requirement |
|---|---|
| **Video** | A real dairy shop and a real blind owner in the **first 20 seconds**; English captions for every line; a proof-of-deployment shot (Cloud Run console + dashboard). **Within the allowed length**: get written approval for 4:00 from support@hack2skill.com, otherwise upload the ≤ 2:55 cut |
| **Deck (PDF, ≤ 5 MB, official template)** | Uses the rubric's own words ("meaningful use of Gen AI", "technically sound", "scalability", "problem alignment", "impact", "originality", "usability"). Includes the architecture diagram, benchmark slide, AI on/off slide, business model, privacy and safety, and **honest limitations** |
| **Description (≤ 1024 characters)** | Names exactly how **Firebase, Firestore, Cloud Run and Gemini** are used |
| **Live link** | Warm instances (`--min-instances 1`), uptime alerts, and a script that resets the demo data. Test it **daily from Oct 19 to Nov 6**; there are no retries |
| **Repo** | A clean README with the logo, architecture and setup steps, a green CI badge, and a clear license |

## 6. Timeline

| When | Do |
|---|---|
| **Oct 10–11** | Contact a blind association or NHFDC office **now** and find a real small dairy shop to film in. Email support about the video length. Confirm all 4 team members on Hack2skill by Oct 11, 23:59 |
| Oct 11–14 | Build the core loop with tests and CI from day 1 (the team plans) |
| **Oct 15–16** | 2–3 day pilot with the blind owner; run all benchmarks and the AI on/off comparison; take the usability survey |
| Oct 17 | Shoot the video in the real shop (footage for both the 4:00 and 2:55 cuts); build the deck with the real numbers |
| Oct 18 | Deploy freeze at 18:00 · run `scripts/check_submission.py` · submit by **20:00 IST** |

## 7. Score-killers to avoid

- A video over the allowed length **without written approval**.
- The live link down at any point between Oct 19 and Nov 6.
- Mockups or scripted fake responses in the video.
- Claiming "first ever" without the prior-art table, or claiming counterfeit-note detection.
- Five half-working features instead of one flawless core loop.
