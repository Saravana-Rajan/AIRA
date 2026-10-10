# AIRA — Research Notes (what we verified, and where)

These are the facts the spec and plans rely on, checked between 2026-10-09 and 2026-10-10. Re-verify anything marked *preview* before relying on it in the demo.

## 1. Prior art: tools for blind shop owners (for the deck's "how is this different" slide)

| Prior art | What it does | Gap AIRA fills |
|---|---|---|
| **Voice Automated Billing System for Blind People** (Devfolio hackathon project, Blind Relief Association café, Delhi): https://devfolio.co/projects/voice-automated-billing-system-for-blind-people-dc49 | Voice billing and daily sales for blind café staff | No supplier orders, delivery counting, invoice check, hand guidance, cash/UPI verification or stock alerts |
| **Revel Systems "Accessibility Mode"** (2014): https://www.mactech.com/2014/04/30/revel-systems-launches-ipad-pos-accessibility-bundle-for-the-visually-impaired/ | iPad POS with audio feedback for blind staff | Old, needs an iPad, no AI, no physical-world tasks |
| **Britannia A-Eye** (2025, Gemini Live pilot): https://indianretailer.com/news/britannia-pilots-ai-based-retail-solution-visually-impaired-shoppers | Helps blind **shoppers** in a store | Nothing for the **owner** |
| **Cuanto Tengo**: https://bfaglobal.com/?p=1703 | Counts shelf stock from a photo | Built for sighted owners |
| **AudiVision**: https://indiaai.gov.in/startup/audivision | Reads product names and expiry dates | One sub-task only |
| **"Pretty Much an Advocate as Well"**, interviews with 14 self-employed visually impaired people: https://mdsoar.org/items/d106519e-5f6c-48fa-8acc-be27f9dea69f | Documents inaccessible business tools and reliance on third parties | Research only, no system |
| Be My Eyes / Be My AI, Seeing AI, Google Lookout, Gemini Live "Guided Vision" | General scene, text and object help for blind *consumers* | No shop workflow, ledger, orders or payments |

**Verdict:** no product, paper or hackathon entry combines the whole owner loop of order → live delivery count → invoice check → paying the vendor → guided sale → cash/UPI verification → stock → reorder. A web search budget limit cut this check short, so before claiming "first" in the final deck, run one CHI/ASSETS 2022–26 search.

## 2. India context

- **NHFDC self-employment loans** (via state agencies): for people with 40% or more disability, aged 18–60, at about 5–6% interest, for small business or trade. https://apdascac.ap.gov.in/assets/doc/Schemes/NHFDC%20Loans.pdf · https://pbscfc.punjab.gov.in/?q=node/30
- India has about **12 million kirana stores**, per the Britannia article above.
- The **RPwD Act 2016** sets India's accessibility obligations.
- We found no verified figure for blind self-employment. Don't quote one without a source.

## 3. Google technology facts (checked 2026-10-09)

| Item | Fact |
|---|---|
| `gemini-3.8-live` | GA. Bidirectional voice that can be interrupted; supports tools and Google Search grounding. Video input ≤ 1 FPS. Audio+video sessions **end at 2 min without context compression**, and connections last about 10 min, so use compression, session resumption and GoAway handling. Supports Tamil and Hindi. |
| `gemini-3.8-flash` | GA. Used for structured invoice and cash reads (two independent passes). |
| `gemini-robotics-er-2-preview` | **Preview**, called through `generateContent`. Pointing returns `[y,x]` normalised 0–1000; boxes `[ymin,xmin,ymax,xmax]`. Counting is strongest with thinking set to "high". Also does instrument reading (zoom + code execution) and success detection. Paid pricing is $1 input / $5 output per 1M tokens until 2026-12-31. |
| `gemini-robotics-er-2-streaming-preview` | Text output only, ≤ 1 FPS, and video frames alone don't trigger a turn. **Not used for counting or pointing.** |
| `gemini-robotics-er-1.6-preview` | **Shut down 2026-08-31.** Don't use it. |
| Vertex AI | Renamed **Gemini Enterprise Agent Platform** (April 2026). Use the current name in the deck. |
| Gemini API free tier | Data may be used to improve products. **Use the paid tier**, so shop images aren't used for training. |
| MediaPipe Hand Landmarker (web) | GPU means WebGL2 (not WebGPU). Estimated 15–25 FPS on a mid-range Android phone; benchmark it. The model is about 7.8 MB. |
| Phone audio | A Bluetooth headset mic switches Android to mono; **use the phone's built-in mic**. Headset-button control via Media Session needs a playing `<audio>` element. TalkBack takes over single-finger gestures, so use double-tap and long-press only. |
| Installable app → APK | Bubblewrap TWA (Node 18+, JDK 17, assetlinks.json on Firebase Hosting). Camera, mic and GPS stop when the app is in the background, so keep the screen on with Wake Lock. |

## 4. Messaging channels for supplier orders

| Channel | Facts |
|---|---|
| **WhatsApp Business Cloud API** (Meta) | Sends automatically, and vendor replies arrive via webhook. A **free test number** can message a small set of registered recipients, which is enough for the demo. Business-initiated messages need an approved **template**. Production charges per message, so verify current India pricing. Docs: https://developers.facebook.com/docs/whatsapp/cloud-api |
| **Telegram Bot API** | Free and automatic, but the vendor must first start a chat with the bot. Few Indian dairy vendors use Telegram. https://core.telegram.org/bots/api |
| **Click-to-chat** | `https://wa.me/<number>?text=<order>` opens WhatsApp with the message pre-typed; the owner double-taps Send. Free, no setup. |

## 5. Hackathon rules

See `docs/hackathon/`. Key points:

- **Mandatory technology:** Firebase, Firestore, Cloud Run and Gemini must all be named in the description.
- **Prototype:** a live deployed link that stays up through evaluation (Oct 19 – Nov 6).
- **Video length:** the T&C say *"strictly below three minutes"*. The team targets 4:00, keeps a ≤ 2:55 backup cut, and is emailing support for written approval.
