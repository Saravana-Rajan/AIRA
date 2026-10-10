# Counting set (bench/data/count)

This set measures how well AIRA counts packs in a crate or on a shelf. `bench/score_count.py` uses it.

**Use real photos only.** Never make up, generate, copy or edit images. Never write a count you did not take by hand.

## Take the photos

- Take **20 photos** of crates and shelves at Murugan Dairy with the **chest-mounted phone** in its usual position.
- Each photo shows **one product type** with a known number of packs, from **4 to 30**.
- Mix the products:
  - mostly Aavin milk, Sakthi curd and Arun ice-cream boxes
  - a few photos of buttermilk, ghee, paneer or butter
- In **at least 5 photos**, the packs partly overlap.
- Save each photo as **JPEG** (`.jpg`), not HEIC or PNG. The dry run rejects files that aren't JPEG.
- Keep **no people** in the frame. Keep out UPI screens, phone numbers and customer details.
- Count every photo **by hand, twice**, before you write its label.
- Name the photos `c01.jpg` … `c20.jpg` and put them in this folder.

## Write the labels

Add one row per photo to `labels.json`. It starts as an empty list `[]`.

```json
[{"file": "c01.jpg", "label": "Sakthi curd 1 L pack", "count": 12}]
```

- `label` is the item name passed to `count_in_frame`, for example "Aavin milk 500 ml pouch".
- `count` is your hand count, as a whole number.

## Run

```bash
cd services/live
uv run python ../../bench/score_count.py --dry-run               # offline: files exist, are JPEG, counts are valid
uv run python ../../bench/score_count.py --device "<phone model>"  # real Gemini calls; needs GEMINI_API_KEY
```

The live run makes one count call per photo, one at a time. It writes files to `bench/results/`, which git ignores, but **only when every call returned**:

| File | Contents |
|---|---|
| `count.csv` | Rows for the deck table (`scripts/bench_table.py`): `count_accuracy`, `count_abstain_rate`, `er2_count_latency_ms`, plus `count_fallback_latency_ms` for calls the Flash fallback answered |
| `count.json` | `n`, `exact`, `within1`, `mae`, `abstain`, `errors`, `fallbacks`, `p50_ms`, `p95_ms` |
| `rows/count.csv` | One row per photo (`truth`, `pred`, `raw_count`, `aim_hint`, `answered_by`, `ms`, `error`) |

How the numbers are counted:

- An **abstention** is a photo where AIRA asked for another view (`needs_retake`) instead of guessing. Report abstentions honestly.
- A **failed call** is not an abstention. If any call fails, the scorer writes only `rows/count.csv` and exits with an error. Fix the problem (usually `GEMINI_API_KEY`) and run it again.
