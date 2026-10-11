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
| `crate_sakthi_curd.jpg` | Top-down photo of one crate, 12 pouches visible, none overlapping more than half | ≤1600 px long side |
| `invoice_ok.jpg` | Printed mock invoice, "Sakthi Dairy Distributor": 10 × Sakthi curd 1 L, unit price and total consistent | Flat, well lit, ≤1600 px |
| `invoice_wrong_qty.jpg` | The same invoice but qty **12** (total adjusted), to trigger a discrepancy against the 10 counted | Same as above |
| `cash_vendor_500x2.jpg` | Two ₹500 notes fanned in an open hand, chest-camera view | ≤1600 px |
| `sale_guidance.mp4` | Chest-camera view reaching into fridge 2, middle shelf, left, taking one Sakthi curd 1 L pouch | 1280×720, ≤15 s, no audio |
| `cash_customer_100.jpg` | One ₹100 note in an open hand | ≤1600 px |
| `soundbox_upi_60.wav` | Recording of a UPI payment soundbox announcing a ₹60 receipt, as it is heard in the shop | WAV PCM16, mono or stereo, 16–48 kHz, ≤6 s |

## Check after copying the files here

Run from `frontend/app`:

```bash
node -e "const f=['delivery_one_by_one.mp4','crate_sakthi_curd.jpg','invoice_ok.jpg','invoice_wrong_qty.jpg','cash_vendor_500x2.jpg','sale_guidance.mp4','cash_customer_100.jpg','soundbox_upi_60.wav'];const fs=require('fs');const miss=f.filter(x=>!fs.existsSync('public/demo/'+x));const big=f.filter(x=>fs.existsSync('public/demo/'+x)&&fs.statSync('public/demo/'+x).size>8e6);console.log(JSON.stringify({miss,big}))"
```

Expected: `{"miss":[],"big":[]}`
