# Product photos for the identify benchmarks

Two photo sets measure how well AIRA tells the 10 Sunrise Dairy products apart:

| Set | Folder | Photos | Used by |
|---|---|---|---|
| A. ER 2 spike | `shared/bench/data/packs/` | 20 (2 per SKU) | `shared/bench/spike_er2.py`: latency + accuracy, ER 2 vs Flash |
| B. Golden set | `shared/bench/data/identify/` | 30 (3 per SKU) | `shared/bench/score_identify.py`: accuracy for the deck |

**Only real photos.** Never make up, generate, copy or edit images, and never write a label for a photo you have not taken. Set B must be different photos from set A.

## How to shoot

- Use the **chest-mounted phone** in its usual position, with the pack held up **40 to 60 cm** in front of the camera, the way Sam holds it for the customer.
- Save each photo as **JPEG** (`.jpg`), not HEIC or PNG. On an iPhone, go to Settings › Camera › Formats and choose "Most Compatible". The dry run rejects files that aren't JPEG.
- Keep **no people** in the frame: no faces, and nothing beyond the hand holding the pack. Keep out UPI screens, phone numbers and customer details. If you can't avoid them, put the photo in `shared/bench/data/private/`, which git ignores.
- Use a **real pack** from the shop. If the 1 L pouch is out of stock, wait until it is back in.
- Commit labels and code only. Photos go into git only if the team agrees to publish them.

## Checklist: every SKU

The SKU IDs must match `candidates.json` and `shared/catalog/sunrise_dairy.json` exactly.

| # | SKU | Product | Set A (2) | Set B (3) | Easily confused with |
|---|---|---|---|---|---|
| 1 | `aavin_milk_500ml` | Aavin Milk 500 ml pouch | [ ] [ ] | [ ] [ ] [ ] | milk 1 L, buttermilk 200 ml |
| 2 | `aavin_milk_1l` | Aavin Milk 1 L pouch | [ ] [ ] | [ ] [ ] [ ] | milk 500 ml |
| 3 | `sakthi_curd_500ml` | Sakthi Curd 500 ml pack | [ ] [ ] | [ ] [ ] [ ] | curd 1 L |
| 4 | `sakthi_curd_1l` | Sakthi Curd 1 L pack | [ ] [ ] | [ ] [ ] [ ] | curd 500 ml |
| 5 | `aavin_buttermilk_200ml` | Aavin Buttermilk 200 ml pouch | [ ] [ ] | [ ] [ ] [ ] | milk 500 ml |
| 6 | `aavin_ghee_200ml` | Aavin Ghee 200 ml jar | [ ] [ ] | [ ] [ ] [ ] | ghee 500 ml |
| 7 | `aavin_ghee_500ml` | Aavin Ghee 500 ml jar | [ ] [ ] | [ ] [ ] [ ] | ghee 200 ml |
| 8 | `aavin_paneer_200g` | Aavin Paneer 200 g pack | [ ] [ ] | [ ] [ ] [ ] | butter 100 g |
| 9 | `aavin_butter_100g` | Aavin Butter 100 g pack | [ ] [ ] | [ ] [ ] [ ] | paneer 200 g |
| 10 | `arun_icecream_box` | Arun Ice Cream family box | [ ] [ ] | [ ] [ ] [ ] | (none) |

**What each photo of a SKU shows:**

- **Set A (2 per SKU):**
  1. The label faces the camera, in daylight.
  2. The pack is tilted about 30° or partly turned, in normal shop lighting (tube light, evening).
- **Set B (3 per SKU):**
  1. The label faces the camera.
  2. The pack is tilted or partly turned.
  3. Shop lighting or a busy background, such as the shelf or the crate behind the pack.

**For the confusion pairs** (milk 500 ml / buttermilk 200 ml, milk 500 ml / 1 L, curd 500 ml / 1 L, ghee 200 / 500 ml, paneer / butter), make at least one photo of each SKU hard: the size marking hidden or turned away. These photos test whether AIRA says "show me the label" (`vision.aim.show_label`) instead of guessing.

## Labels

**Set A:** name the photos `p01.jpg` to `p20.jpg` in `shared/bench/data/packs/`. Add one row per photo to `shared/bench/data/packs/labels.json` (it starts as an empty list `[]`):

```json
[
  {"file": "p01.jpg", "sku": "aavin_milk_500ml"},
  {"file": "p02.jpg", "sku": "aavin_milk_500ml"}
]
```

**Set B:** name the photos `i01.jpg` to `i30.jpg` in `shared/bench/data/identify/`. Rows go in `shared/bench/data/identify/labels.json`, in the same format. Every SKU needs at least 2 rows, and 3 is the target.

Write the SKU you actually photographed, read off the real pack, not the one you meant to shoot.

## Check, then run the spike

```bash
cd backend
uv run python ../shared/bench/spike_er2.py --dry-run   # offline: files exist, are JPEG, SKUs known, coverage per SKU
uv run python ../shared/bench/spike_er2.py             # real Gemini calls; needs GEMINI_API_KEY
```

The live run makes 2 × N identify calls: an ER 2 pass, then a pass forced to the Flash fallback. It writes `shared/bench/results/spike_er2.csv` (one row per call) and `shared/bench/results/spike_er2.json` (summary + decision). Both are git-ignored. It prints:

```
ER 2  : {'n': 20, 'p50_ms': ..., 'p95_ms': ..., 'accuracy': ..., 'abstain': ..., 'wrong': ..., 'errors': ..., 'fallbacks': ...}
Flash : {...}
DECISION: KEEP ... | SWITCH ...
```

- `model` is the model the pass asked for. `answered_by` is the model that actually replied. When ER 2 times out, the same call falls back to Flash; `fallbacks` counts those calls, and the script warns when there are any.
- The spike CSV is not in the deck's benchmark format, so `shared/scripts/bench_table.py` skips it with a note. That is expected.

**Decision rule** (`spike_er2.decide`):

- **SWITCH** the `point` role (identify + point) to the Flash fallback if ER 2's p50 is over **3000 ms** and Flash's p50 is lower, or if Flash is at least **10 points** more accurate.
- Otherwise **KEEP** ER 2. If ER 2 is over 3000 ms but Flash is slower, the script keeps ER 2 and prints a note.
- **NO DECISION** (exit 1) if every ER 2 call failed, or if ER 2 is too slow but every Flash call failed. The script never recommends a model that did not answer. It also warns when every Flash call failed, because Flash is ER 2's fallback in production.
- Counting (role `count`) stays on ER 2 either way.

To switch, set `pointing_model` in the Firestore doc `config/runtime` (database `aira`), for example with `uv run python -m aira_live.remote_config set pointing_model fallback`, or set env `AIRA_MODEL_POINT` on `aira-live`. Post the two summary lines and the decision in the team chat.

## Score set B for the deck

```bash
cd backend
uv run python ../shared/bench/score_identify.py --dry-run               # offline: same checks; every SKU needs 2+ photos
uv run python ../shared/bench/score_identify.py --device "<phone model>"  # real Gemini calls; needs GEMINI_API_KEY
```

The live run makes one identify call per photo with the configured `point` model. It writes files to `shared/bench/results/`, but **only when every call returned**:

| File | Contents |
|---|---|
| `identify.csv` | The deck table row (`shared/scripts/bench_table.py`): `product_id_accuracy` |
| `identify.json` | `n`, `accuracy`, `abstain`, `wrong_confident`, `pairs` (truth->pred for confident mistakes), `errors`, `fallbacks`, `p50_ms`, `p95_ms` |
| `rows/identify.csv` | One row per photo (`truth`, `pred`, `confidence`, `alternatives`, `answered_by`, `ms`, `error`) |

- An **abstention** (`sku` = null) is AIRA asking to see the label instead of guessing. Report it honestly.
- `wrong_confident` counts photos where AIRA named the wrong product. This is the number that must stay near zero.
- If any call fails, the scorer writes only `rows/identify.csv` and exits with an error. Fix the problem and run it again.
