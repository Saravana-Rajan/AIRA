# AIRA: the pitch

**One line:** AIRA lets a blind person run a small shop alone, with no computer, no screen and no sighted helper.

**Tagline:** *"Not eyes. A business partner."*

## The problem

- India has about **12 million kirana and small shops**. Running one is a daily chain of visual tasks:
  - placing stock orders
  - checking deliveries
  - reading vendor bills
  - paying vendors
  - finding products
  - checking cash and UPI payments
  - tracking stock and expiry
- The government already backs disabled entrepreneurs through **NHFDC and state self-employment loans**, so a blind person can *open* a shop.
- But **running it alone** is the gap. Blind owners depend on a family member or helper for:
  - counting stock
  - reading bills
  - checking money
  - finding items
- That dependence costs income, privacy and dignity, and exposes them to errors and cheating.
- Today's assistive apps (Be My Eyes, Seeing AI, Lookout) help a blind person *see a picture*. None of them helps a blind person *run a business*.

## The solution: one loop, end to end

Murugan runs **Murugan Dairy**, selling Aavin milk, Sakthi curd and Arun ice cream. He wears a **chest-mounted phone** and one earphone. AIRA covers the full day:

1. **Order stock by voice.** "Order 30 litres Sakthi curd." AIRA confirms and sends a **WhatsApp order**, then speaks the vendor's reply.
2. **Check the delivery live.** As packs go into the fridge, AIRA **counts** them and records only what's verified: "28 counted, 2 short."
3. **Check the vendor's bill.** Two independent reads, then **code** compares the bill with the order and the count: "Bill says 32 L, 30 arrived."
4. **Pay the vendor.** AIRA guides him to the cashbox and checks the notes in his hand. The total is computed in code.
5. **Remember where things are.** "This is the curd shelf" is saved as spatial memory.
6. **Guided sale.** A customer says "2 litres curd". AIRA guides Murugan's **hand** to the right pack and verifies it ("Sakthi curd 1 L ✓"), then gives the price.
7. **Payment.**
   - **Cash:** AIRA checks the notes and calculates the change in code.
   - **UPI:** AIRA **listens to the payment soundbox** ("₹60 received") and matches the amount.
8. **Low stock and expiry.** "Only 2 L curd left. Order 30 L?" and "6 packs expire tomorrow."
9. **End of day.** Sales, top seller and a suggested order for tomorrow, from the BigQuery forecast.

**Rule:** the AI proposes and the backend commits. Stock, money and states change only in deterministic code, inside Firestore transactions, with evidence and a spoken "yes".

## Why now

- **Gemini Robotics-ER 2** (2026) brings robot-grade spatial skills to a phone camera:
  - counting packs
  - pointing to a product
  - checking that the right item is in hand
- **Gemini Live** (`gemini-3.8-live`) gives natural, interruptible voice in Tamil, Hindi and English, with tool calls.
- **Google Cloud** runs the business backbone: Cloud Run, Firestore, Firebase, Pub/Sub, BigQuery and Cloud Storage.

## Honest prior-art comparison

| | Voice billing for blind (Devfolio, Blind Relief Association café) | Revel POS Accessibility Mode (2014) | Britannia A-Eye (2025) | Be My Eyes / Be My AI | **AIRA** |
|---|---|---|---|---|---|
| Built for | Blind café staff | Blind POS staff | Blind **shoppers** | Blind people, general use | **Blind shop owners** |
| Voice billing | ✓ | ✓ (audio) | ✗ | ✗ | ✓ |
| Supplier orders | ✗ | ✗ | ✗ | ✗ | ✓ WhatsApp + vendor reply |
| Live delivery count | ✗ | ✗ | ✗ | ✗ | ✓ ER 2 |
| Invoice check vs order and count | ✗ | ✗ | ✗ | ✗ | ✓ two reads + code |
| Hand guidance to the product | ✗ | ✗ | ✗ | Human volunteer | ✓ ER 2 + fingertip tracking |
| Cash and UPI check | ✗ | ✗ | ✗ | Partial (photo Q&A) | ✓ cash read + soundbox |
| Stock, expiry, reorder forecast | ✗ | Partial | ✗ | ✗ | ✓ Firestore + BigQuery |

**Our claim:** individual pieces exist, but **no system runs the whole owner loop** (order, count, invoice, pay, guided sale, payment check, stock, reorder) for a blind owner.

## Business model

AIRA is **free for blind owners**. It is funded by:

1. **Government and CSR:** bundled with NHFDC and state disability self-employment schemes, and funded by corporate CSR (Indian companies must spend 2% of profit on social causes).
2. **Dairy brands and distributors:** sponsorship, because AIRA turns low-stock alerts into **automatic reorders of their products**, which means higher sell-through and less stockout.
3. **Banks and UPI soundbox providers:** partnerships, since AIRA increases digital payments in small shops and gives blind merchants trusted payment confirmation.
4. **Later:** a small monthly subscription for **non-disabled small shopkeepers**, who also want hands-free stock, orders and forecasting.

## Impact metrics we will measure

| Area | Metric |
|---|---|
| Independence | Share of the daily shop loop completed **without a sighted helper** |
| Accuracy | Count accuracy and abstain rate · invoice discrepancy catch rate · cash-total accuracy · product-identification accuracy · soundbox parse accuracy |
| Speed | End-to-end sale time, with AIRA vs without |
| Money protected | ₹ value of short deliveries and wrong bills caught |
| Reliability and cost | p50/p95 latency · uptime · cost per sale (₹) |
| User voice | Blind participant feedback and quotes |
