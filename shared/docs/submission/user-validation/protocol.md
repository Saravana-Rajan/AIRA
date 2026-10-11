# AIRA user study protocol (Oct 15)

**Pack:** `outreach.md` (send Oct 11) · `consent-form.md` (read aloud before starting) · this protocol ·
`data-sheet.csv` (filled during the session). Nothing in this pack is sent automatically.

**Participants:** 1–5 blind or low-vision adults (18+), target 3. At least one runs or wants to run a shop.

**Setting:** a mock or real dairy counter:
- one fridge (2 shelves) with Aavin milk 500 ml pouches, Sakthi curd 1 L packs and Arun ice cream boxes
- a cashbox with separate note slots, holding the team's own cash, counted before and after each session
- a UPI payment soundbox, or a phone replaying announcements recorded from a real soundbox
- the chest-mounted test phone running AIRA, and one earphone
- a laptop showing the Shop Dashboard (`/dashboard`) for the facilitator

The build tag goes in the data sheet. The "vendor" is a teammate's phone. Its number lives in `.env`,
never in git.

**Team roles:**
- One sighted facilitator stays beside the participant at all times.
- One teammate times each task and fills in the data sheet.
- One teammate plays the vendor and the customer.

**Before each participant:**
1. Re-run the seed so stock starts the same:
   `uv run --project backend python shared/scripts/seed_firestore.py`.
2. Read the consent form aloud in the participant's language (English or Tamil). Offer the
   large-print copy. Record the verbal "I agree" and the time on the form.
3. Do 5 minutes of practice: double-tap = talk, long-press = stop.

**Demo amounts** (catalog prices in `shared/catalog/sunrise_dairy.json`; recompute if the catalog
changes):
- Sakthi curd 1 L sells at ₹60 and costs ₹50.
- The vendor's bill is 30 L × ₹50 = ₹1,500.
- For the 28 L received, the shop pays 28 × ₹50 = ₹1,400.
- A 2 L sale costs ₹120. Change from ₹200 is ₹80.

## Tasks

| ID | Task (spec §2 step) | Setup by team | Success = |
|---|---|---|---|
| T1 | Order 30 L Sakthi curd by voice (step 1) | Sakthi vendor saved | The vendor phone receives the order for 30 L Sakthi curd. With the WhatsApp Cloud API, the vendor's reply is also spoken |
| T2 | Stock the delivery, one by one (step 2) | Team hands over **28** packs | AIRA reports 28 and flags 2 short |
| T3 | Check the vendor's bill (step 3) | Bill printed for **30 L × ₹50 = ₹1,500** | AIRA flags bill 30 vs counted 28 |
| T4 | Pay the vendor in cash for the 28 packs received (step 4) | Cashbox stocked with the team's cash | ₹1,400 handed over; AIRA confirms the total |
| T5 | "This is the Sakthi curd shelf" (step 5) | none | Location saved; read back |
| T6 | Customer asks "2 litres curd" (step 6) | A teammate plays the customer | 2 × 1 L packs handed over; hand guidance used; price ₹120 stated |
| T7a | Customer pays cash; give change (step 7) | Customer gives ₹200 | ₹80 change given; AIRA confirms |
| T7b | A second customer buys 1 L curd and pays by UPI (step 7) | Soundbox plays "₹60 received" | Sale confirmed only after the soundbox amount matches |
| B6 | **Baseline:** T6 **without AIRA**, using the participant's usual method | same as T6 | completion + time recorded |
| B7 | **Baseline:** T7a **without AIRA**, using the participant's usual method | same as T7a | completion + time recorded |
| T8 | *Optional:* "How much Sakthi curd is left?" (step 8) | none | Spoken amount matches the dashboard Stock card |
| T9 | *Optional:* "How was today?" (step 9) | none | Spoken totals match the dashboard Today card |

**T1 channel:**
- Until WhatsApp Cloud API tokens exist, AIRA uses the **click-to-chat** fallback. It opens WhatsApp
  with the order pre-typed, and the participant double-taps Send.
- Write the channel used (Cloud API or click-to-chat) in the session notes.
- If the facilitator helps with the Send tap, count it as an assist.

**T7b evidence check (if time allows):** before the right amount, play a wrong one ("₹50 received").
AIRA must not confirm the sale. Count it in `errors_caught` if AIRA catches it, or in `aira_wrong` if
it confirms.

**Order:**
- Odd IDs (P1, P3, P5) do B6–B7 just before T6.
- Even IDs (P2, P4) do B6–B7 just after T7b.

This alternation stops practice from always favouring one method.

**Timing** (stopwatch, whole seconds):
- T6 / B6: from the end of the customer's request until both packs are in the customer's hand.
- T7a / B7: from the cash reaching the participant until the change is in the customer's hand.
- Other tasks: from the instruction until the success condition, or until the participant stops.

## Measures (one data-sheet row per task run)

| Column | Value |
|---|---|
| `participant_id` | P1, P2, … (never a name) |
| `date` | YYYY-MM-DD |
| `build` | git short SHA of the deployed build |
| `task_id` | T1, T2, T3, T4, T5, T6, T7a, T7b, B6, B7, T8, T9 |
| `with_aira` | Y for T tasks, N for B6 and B7 |
| `completed` | Y, N, or H (completed with facilitator help) |
| `time_s` | seconds, per the timing rules |
| `errors_caught` | errors AIRA caught (short delivery, bill mismatch, wrong UPI amount, …) |
| `aira_wrong` | wrong answers AIRA gave (wrong count, amount or product) |
| `aira_unsure` | times AIRA said "not sure" or asked for another view (allowed; not an error) |
| `assists` | times the facilitator helped |
| `confidence_1_5` | the participant's answer to "How sure are you this went right? 1 to 5" |
| `quote` | words spoken about AIRA, verbatim, **only** if the quote box on the consent form is "yes" |

A task that was not run gets no row. Never fill in a value that was not observed.

## Safety

- The stop word is "stop" (or "நிறுத்து"). It ends any task at once.
- The participant keeps their cane or usual mobility aid at all times. The bump guard is not a tested
  task, and it never replaces the cane.
- The facilitator clears crates and closes the fridge and freezer doors between tasks.
- Only the team's cash is used, counted before and after. AIRA counts notes; it does not judge whether
  a note is genuine.
- No real customer data.
- Breaks on request. Water is available.

## After the session

- Fill in `data-sheet.csv`. Commit it with participant IDs only.
- Signed consent forms stay on paper with the team. Never scan them into git.
- Delete any recording that has no video consent within 24 h.
- Use quotes only when the consent form allows it, and without names.
- Report every participant, including tasks that failed or stopped. Do not drop a session.

## Convert to benchmark rows (manual, about 5 minutes)

Append to `shared/bench/results/user_study.csv`, one line per participant per metric. That file is the one
exception to the git-ignored `shared/bench/results/` folder (`!shared/bench/results/user_study.csv` in `.gitignore`): commit it,
so the slide 12 numbers trace back to committed data on every laptop. Write the header line first if the file
is new:

```csv
metric,value,unit,device,build,ts
sale_time_with_aira,<T6 time_s + T7a time_s>,s,<test phone model>,<build>,<ISO-8601 UTC>
sale_time_baseline,<B6 time_s + B7 time_s>,s,<test phone model>,<build>,<ISO-8601 UTC>
task_completion,<100 × tasks done without help ÷ 8>,%,<test phone model>,<build>,<ISO-8601 UTC>
```

**Rules:**
- `sale_time_with_aira`: write it only when both T6 and T7a were completed (Y or H).
- `sale_time_baseline`: write it only when both B6 and B7 were completed (Y or H).
- `task_completion` = 100 × (number of T1, T2, T3, T4, T5, T6, T7a, T7b rows with `completed` = Y and
  `assists` = 0) ÷ 8.
  - The divisor is always 8. A task that was not run or was stopped counts as not done. Write the
    reason in the session notes.
  - T8 and T9 are optional and never counted.
- `build` comes from the data sheet. `ts` is the session end in UTC, for example
  `2026-10-15T09:30:00Z`.
- Copy the numbers; never round in AIRA's favour.

Then regenerate the deck table:
`python3 shared/scripts/bench_table.py > shared/docs/submission/bench-table.md`.
It reports the mean and `n` per metric. Metrics that have no rows stay "pending".
