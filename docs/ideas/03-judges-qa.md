# AIRA: judges' Q&A preparation

## Honest weaknesses and mitigations

| Weakness | Mitigation |
|---|---|
| Counting can be wrong (packs overlap, the fridge is crowded) | Count one by one or by crate. Low confidence → "show me again". The final count is read back and confirmed, and only verified quantities enter stock |
| Preview models (Gemini Robotics-ER 2) may be slow or change | Day-1 latency spike. **Remote Config switch** to `gemini-3.8-flash`. Model IDs live in one file |
| Noisy shop, with customer and owner talking at once | Double-tap before speaking. AIRA reads every order back before acting. Built-in mic close to the mouth |
| UPI cannot be fully verified without bank APIs | Confirmed **only** by the shop's payment soundbox (bank-generated announcement, amount matched in code) or explicit owner confirmation. Never by a customer's screenshot |
| Cash recognition isn't perfect | Two independent reads plus confidence. Change is always computed in code. **No counterfeit claims** |
| WhatsApp business template approval can be slow | The Meta test number covers the hackathon. Click-to-chat link as fallback; Telegram as an option |
| Needs internet | Spoken offline status. Sales queue locally and sync later with the same idempotency keys |
| No large user study yet | At least one blind participant runs the full loop. We report completion, errors, time and quotes honestly |

## Likely judge questions

**Why not just a simple POS app?**
A POS records sales someone has *already seen*. A blind owner's problem is everything before the record:
- Did 30 packs actually arrive?
- Does the bill match?
- Which pack is in my hand?
- Is this note ₹200?
- Did the UPI payment really land?

AIRA closes that physical-to-digital gap.

**Why does this need AI?**
Counting packs, identifying the right product, guiding a hand to a shelf, and reading a handwritten or printed vendor bill are *visual and spatial* tasks. Gemini Robotics-ER 2 does them, and Gemini Live makes it hands-free voice. The arithmetic, stock and states are deliberately **not** AI: they're deterministic code.

**What if AIRA counts wrong?**
It never guesses:
- Low confidence → asks for another view or a crate count.
- Every final count is read back and confirmed.
- Only verified quantities enter stock, and a discrepancy is recorded rather than hidden.

We publish count accuracy **and** abstain rate.

**Can a customer fool it with a fake UPI screenshot?**
No. UPI is confirmed only by the soundbox announcement, matched to the sale amount in code, or by the owner's explicit confirmation. Screenshots are never trusted.

**Can it detect counterfeit notes?**
We don't claim that. AIRA identifies denominations with a confidence level to help the owner, and computes the change in code.

**What about a noisy shop?**
The owner double-taps to speak, and AIRA confirms orders and amounts by read-back before anything is committed. Consequential actions always need a spoken "yes".

**You use preview models. What if they break during judging?**
A Remote Config switch moves counting and pointing to `gemini-3.8-flash` in seconds. Cloud Run stays warm with min instances, and uptime checks alert us.

**Privacy?**
- Images are kept only for invoices and evidence, for 30 days in Cloud Storage.
- Faces are blurred on the phone before upload.
- No customer personal data is stored.
- The paid Gemini tier means shop images aren't used for training.

**Cost per sale?**
We measure it and report it in ₹ on the benchmark slide. The voice session, one or two ER 2 calls and a Firestore transaction per sale keep it to a few paise up to under ₹1 (to be confirmed by measurement).

**Does it scale beyond one dairy shop?**
- Products, aliases, suppliers and shelf zones are data in Firestore, so a kirana store, vegetable shop or medical shop is a new catalog, not new code.
- Cloud Run autoscales, Firestore is multi-tenant per shop, and BigQuery forecasting works per shop and SKU.

**What's the business model?**
- Free for blind owners, funded via NHFDC and state disability self-employment schemes plus CSR.
- Dairy brands and distributors sponsor it, because AIRA auto-reorders their products.
- Banks and UPI soundbox partners.
- Later, a paid tier for non-disabled small shopkeepers.

**Isn't this done already? (prior art)**

| Prior art | What it does | What it lacks |
|---|---|---|
| Voice billing for blind (Devfolio, Blind Relief Association café) | Voice billing | Stock, delivery, guidance |
| Revel POS Accessibility Mode | Accessible billing | AI, physical tasks |
| Britannia A-Eye | Helps blind *shoppers* | Nothing for owners |
| Be My Eyes | General visual help | No business workflow |

Pieces exist, but no system runs the whole owner loop.

**Why a chest-mounted phone?**
Both hands stay free to stock, pick and handle cash. The camera always faces where the hands work, and it needs no extra hardware: just the owner's phone in a cheap mount.

**What if the network drops?**
AIRA says so aloud. Sales queue on the phone and sync later, without duplicates, thanks to the idempotency keys. Vision checks wait until the connection returns, and AIRA never pretends to have verified anything.

## Must-do evidence (makes these answers true)

1. **Blind participant:** at least one blind person (ideally a real shop owner via an NHFDC office or a blind association) runs the full loop in a real or mock dairy shop. Record completion, errors, time, assistance needed and quotes.
2. **Benchmarks:**
   - count accuracy and abstain rate
   - invoice discrepancy catch rate
   - cash-total accuracy
   - product-identification accuracy
   - soundbox parse accuracy
   - p50/p95 latency
   - cost per sale
   - uptime
3. **Baseline comparison:** the same sale and delivery tasks **with AIRA vs without** (time, errors, help needed).
4. **Reliability tests:** missing goods, an uncertain count, a duplicate tool call, network loss mid-sale, an unverified payment, and an ER 2 timeout falling back to Flash.
5. **Live demo:** the judge demo mode with sample media, plus the Shop Dashboard, always up through Nov 6.
