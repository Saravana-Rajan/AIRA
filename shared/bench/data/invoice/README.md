# Supplier bill photos for the invoice benchmark

`shared/bench/score_invoice.py` measures how well AIRA catches a wrong supplier bill before Sam pays it:

| Metric | Meaning |
|---|---|
| `invoice_catch_rate` | Share of bills with a planted error that AIRA flagged (`agreed=false`) |
| `invoice_false_alarm_rate` | Share of correct bills that AIRA flagged anyway |
| `invoice_clean_total_accuracy` | Among correct bills AIRA accepted, the share where it read the right total |
| `invoice_check_latency` | Time for one full check (two independent reads + the checks in code), p50 / p95 |

Read the catch rate together with the false-alarm rate. A reader that flags every bill would "catch" every error.

**Only real photos.** Never make up, generate or edit images, and never write a label for a photo you have not taken.

## What to shoot: 30 bills

- **20 correct bills.** Mix printed and handwritten bills in the formats real suppliers use (Sakthi, Aavin and similar), with 1 to 6 lines each.
- **10 bills with a planted error.** Write or print them yourself, each with one deliberate mistake:
  - a wrong line total (for example 30 × ₹33 written as ₹1,000)
  - a wrong grand total
  - a quantity that disagrees with what was delivered

## How to shoot

- Use the **chest-mounted phone** in its usual position, with the bill held up flat about 30 to 40 cm away.
- Use normal shop light. Make a few shots at an angle or in dim evening light.
- Save each photo as a **JPEG** (`.jpg`) under **1 MB**. HEIC and PNG files fail the dry run.
- Keep **no faces**, phone numbers or customer details in the frame. If you can't avoid them, put the photo in `shared/bench/data/private/`, which git ignores.
- Name the files `inv_001.jpg` to `inv_030.jpg`, in this folder.

## Labels

Write one row per photo in `shared/bench/data/invoice/labels.json`. It starts as an empty list `[]`, and `labels.example.json` shows the format:

```json
[
  {"file": "inv_001.jpg", "supplier": "Sakthi Dairy", "total": 990, "has_error": false, "error_fields": [],
   "lines": [{"name": "Sakthi Curd 1L", "qty": 30, "unit": "L", "unit_price": 33, "line_total": 990}]}
]
```

- `total`, `unit_price` and `line_total` are whole rupees, **exactly as printed or written on the bill, even when they are wrong**.
- `has_error` is `true` only for the 10 bills with a planted error. `error_fields` says what is wrong, for example `["line 1 line_total"]` or `["total"]`.
- `file`, `total` and `has_error` are required. The dry run warns about a correct bill whose line totals do not add up to its total: either the label is wrong or the bill really has an error.

## Check, then run

From `backend`:

```bash
uv run python ../shared/bench/score_invoice.py --dry-run          # offline: files exist, are JPEG, labels valid
uv run python ../shared/bench/score_invoice.py --device "Pixel 7a" --build "$(git rev-parse --short HEAD)"
uv run python ../shared/bench/score_invoice.py --predictions ../shared/bench/results/invoice.json   # re-score offline
python3 ../shared/scripts/bench_table.py                              # deck table from shared/bench/results/*.csv
```

- The live run needs `GEMINI_API_KEY` and makes 2 Gemini calls per bill.
- Pillow (a dependency of `backend`, installed by `uv sync`) gives the second read its enhanced copy of the photo, as in production. If it is somehow missing, the scorer warns and both reads see the same photo.
- The live run writes `shared/bench/results/invoice.json` (metrics, every prediction, per-bill latency) and `shared/bench/results/invoice.csv` (rows for `shared/scripts/bench_table.py`). Both are git-ignored.
- A rate is written to the CSV only when there is data for it. Without any bill with a planted error, for example, the deck shows the catch rate as "pending", not 0%.
