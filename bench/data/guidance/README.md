# Hand-guidance trials (time to touch)

This set measures how long it takes from the start of `product_find` guidance until the owner touches the correct pack (spec §7, target: 20 trials).

`trials.example.json` only shows the format. Its numbers are not measurements, so never report them. The scorer refuses to write deck rows from it.

## Collecting the real trials

1. Stock the demo fridge with the Sunrise Dairy range (Aavin milk, Sakthi curd, Arun ice cream), with look-alike packs next to each other.
2. The participant is a blind person or a blindfolded teammate. Say which in the deck.
3. Run 20 trials with the app bench runner (`ishwarya/03`). Mix the products and move the packs between trials.
4. For each trial, the runner records:
   - `trial`: the trial number
   - `product`: the SKU asked for, e.g. `sakthi_curd_1l`
   - `startedAtMs`: when guidance starts (the first `request_frame` with `continuous: true`)
   - `touchedAtMs`: when the app sends `fingertip_in_target` and the pack is checked, or `null` if the trial times out
   - `correctItem`: whether the pack touched is the one asked for (`false` for a timeout)
5. Save the log as `bench/data/guidance/trials.json`:

```json
{"trials": [{"trial": 1, "product": "sakthi_curd_1l", "startedAtMs": 0, "touchedAtMs": 8400, "correctItem": true}]}
```

## Scoring

```bash
python3 bench/score_guidance.py bench/data/guidance/trials.json
```

The output has `trials`, `successes`, `successRate`, `wrongItem`, `timeouts`, `p50S`, `p95S` and `meanS` (seconds, correct touches only), plus `meetsSampleSize`, which is true at 20 trials or more. Percentiles use the nearest-rank method. A malformed trial (missing field, text instead of a number, a touch before the start) stops the scorer with an error.

To put the result on the deck's benchmark slide:

```bash
python3 bench/score_guidance.py bench/data/guidance/trials.json --out bench/results --device "Pixel 7a" --build "$(git rev-parse --short HEAD)"
python3 scripts/bench_table.py
```

This writes `bench/results/guidance.csv`, which `scripts/bench_table.py` reads:
- `guidance_time_to_touch`: one row per correct touch, in ms, shown as p50 / p95
- `guidance_success_rate`: correct touches as a share of all trials, in %

Wrong-item touches and timeouts have no time. They only lower the success rate. It also writes `bench/results/guidance.json`, the summary plus device, build and timestamp.
