# Cash photos for the cash benchmark

`shared/bench/score_cash.py` measures how well AIRA totals the cash a customer hands over:

| Metric | Meaning |
|---|---|
| `cash_total_accuracy` | Share of photos where both reads agreed and the total is right |
| `cash_abstain_rate` | Share of photos where AIRA asked to see the cash again (`agreed=false`), for example "spread the notes" |
| `cash_wrong_when_confident` | Share of photos where AIRA agreed on a **wrong** total. This is the dangerous case, so keep it near 0. |
| `cash_read_latency` | Time for one cash read (two independent reads + the checks in code), p50 / p95 |

AIRA reads denominations and adds them up in code. It does **not** check whether a note is genuine, and nothing in this benchmark measures that.

**Only real photos.** Never make up, generate or edit images, and never write a label for a photo you have not taken.

## What to shoot: 40 photos

| Kind | About |
|---|---|
| A single note | 8 |
| Several notes, spread out | 12 |
| Notes and coins together | 8 |
| Low light (evening, tube light) | 4 |
| **Overlapping or stacked notes** | **8** |

- Use current notes (₹10, 20, 50, 100, 200, 500) and coins (₹1, 2, 5, 10, 20).
- Hold the cash in the hand or on the counter, the way a customer hands it over.

## How to shoot

- Use the **chest-mounted phone** in its usual position.
- Save each photo as a **JPEG** (`.jpg`) under **1 MB**. HEIC and PNG files fail the dry run.
- Keep **no faces** in the frame, and no UPI screens or customer details. If you can't avoid them, put the photo in `shared/bench/data/private/`, which git ignores.
- Name the files `cash_001.jpg` to `cash_040.jpg`, in this folder.

## Labels

Write one row per photo in `shared/bench/data/cash/labels.json`. It starts as an empty list `[]`, and `labels.example.json` shows the format:

```json
[
  {"file": "cash_001.jpg", "notes": [{"denomination": 500, "count": 2}], "total": 1000, "overlapping": false}
]
```

- `notes` lists every note **and coin** in the photo, as `denomination` (rupees) and `count`.
- `total` is the true amount in whole rupees, and it must equal the sum of `notes`. The dry run checks this.
- Set `overlapping` to `true` when notes overlap or are stacked so that some are partly hidden. On those photos AIRA should ask Sam to spread the notes, not guess.
- `file` and `total` are required.

## Check, then run

From `backend`:

```bash
uv run python ../shared/bench/score_cash.py --dry-run          # offline: files exist, are JPEG, totals add up
uv run python ../shared/bench/score_cash.py --device "Pixel 7a" --build "$(git rev-parse --short HEAD)"
uv run python ../shared/bench/score_cash.py --predictions ../shared/bench/results/cash.json   # re-score offline
python3 ../shared/scripts/bench_table.py                        # deck table from shared/bench/results/*.csv
```

- The live run needs `GEMINI_API_KEY` and makes 2 Gemini calls per photo.
- It writes `shared/bench/results/cash.json` (metrics, every prediction with its `hint_key`, per-photo latency) and `shared/bench/results/cash.csv` (rows for `shared/scripts/bench_table.py`). Both are git-ignored.
