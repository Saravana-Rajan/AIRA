# Demo media for "Try AIRA" (judges)

These files are NOT in git yet. A person must record them; nothing here may be generated or faked.
`frontend/app/src/demo/scenarios.ts` (`DEMO_MEDIA_FILES`) lists the same 8 paths. Until a file exists,
the `/try` page says "not recorded yet" on its stage, and AIRA falls back to the laptop camera
(for photos and clips) or skips the soundbox clip.

Record on the test phone in the demo dairy shop.
- Use fictional invoices only, with no real GSTIN or phone numbers.
- No faces in frame.
- Each file must be ≤ 8 MB.

| File | Content | Spec |
|---|---|---|
| `delivery_one_by_one.mp4` | Chest-camera view: 10 Sakthi curd 1 L pouches. Each is held towards the camera ~1.5 s, then placed into the fridge | 1280×720, H.264, ≤25 s, no audio |
| `crate_sakthi_curd.jpg` | Top-down photo of one crate holding exactly **10** Sakthi curd 1 L pouches (the same delivery), none overlapping more than half | ≤1600 px long side |
| `invoice_ok.jpg` | Printed mock invoice, "Sakthi Dairy Distributor": one line, Sakthi curd 1 L, qty **10**, unit price **₹50**, line total **₹500**; bill total **₹500** | Flat, well lit, ≤1600 px |
| `invoice_wrong_qty.jpg` | The same invoice but qty **12**, unit price **₹50**, line total **₹600**, bill total **₹600**, to trigger a discrepancy against the 10 counted | Same as above |
| `cash_vendor_500.jpg` | Exactly one ₹500 note, flat in an open hand, chest-camera view. Nothing else in the hand | ≤1600 px |
| `sale_guidance.mp4` | Chest-camera view reaching into fridge 2, middle shelf, left, taking one Sakthi curd 1 L pouch | 1280×720, ≤15 s, no audio |
| `cash_customer_120.jpg` | Exactly one ₹100 note and one ₹20 note side by side in an open hand, not overlapping, both values fully visible. Nothing else in the hand | ≤1600 px |
| `soundbox_upi_60.wav` | Recording of a UPI payment soundbox announcing a ₹60 receipt ("received rupees sixty" or the soundbox's own wording), as it is heard in the shop | WAV PCM16, mono or stereo, 16–48 kHz, ≤6 s |

### Why these amounts

AIRA checks every amount in code against the catalog (`shared/catalog/sunrise_dairy.json`, Sakthi curd 1 L:
sells at ₹60, costs ₹50), so a photo or clip with any other amount makes its scene fail.

- Scene 1 orders 10 L. Scenes 2 and 3 both count the same 10 pouches; a crate with more would be recorded as
  more stock and break the invoice check.
- Scenes 4 and 5: the vendor bills at the agreed price, ₹50 per litre. A ₹60 unit price is a price mismatch.
- Scene 6: the vendor is owed what was counted at the agreed price, 10 × ₹50 = ₹500. AIRA asks for one ₹500
  note and says "I see 1000 rupees, but it should be 500" if it sees more.
- Scene 8: the scene 7 customer buys 2 L, 2 × ₹60 = ₹120. Exactly ₹120 means no change is due, so one photo is
  enough. Less is "short"; more would need a second photo of the change.
- Scene 9 is a new customer buying 1 L by UPI, 1 × ₹60 = ₹60. The soundbox must say exactly that amount.

`frontend/app/test/demo.money.test.ts` pins these amounts to the catalog.

## Check after copying the files here

Run from `frontend/app`:

```bash
node -e "const f=['delivery_one_by_one.mp4','crate_sakthi_curd.jpg','invoice_ok.jpg','invoice_wrong_qty.jpg','cash_vendor_500.jpg','sale_guidance.mp4','cash_customer_120.jpg','soundbox_upi_60.wav'];const fs=require('fs');const miss=f.filter(x=>!fs.existsSync('public/demo/'+x));const big=f.filter(x=>fs.existsSync('public/demo/'+x)&&fs.statSync('public/demo/'+x).size>8e6);console.log(JSON.stringify({miss,big}))"
```

Expected: `{"miss":[],"big":[]}`
