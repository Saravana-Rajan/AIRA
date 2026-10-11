# Real-model tests (opt-in)

These tests call the real Gemini models through the app's own code paths. They are **skipped** unless both
environment variables are set, so `uv run pytest -q` and CI stay offline:

```sh
cd services/live
AIRA_LIVE_TESTS=1 GEMINI_API_KEY=... uv run pytest tests/live_real -q -s
```

`-s` shows the per-call latency lines (`[latency] ...`) and what the models returned; a latency table is also
printed in the terminal summary at the end of the run.

| File | Models (`aira_live/models.py`) | What it checks |
|---|---|---|
| `test_live_language.py` | `live` (gemini-3.8-live) via `LiveBridge` + `AdkLiveBackend` | A Tamil text turn is answered in Tamil script, an English turn in English (language follows the speaker). Records connect, first-audio and turn latency. |
| `test_er2_point_count.py` | `count` / `point` (gemini-robotics-er-2-preview) via `vision.count` / `vision.point` | On a synthetic image of N drawn circles: `count_in_frame` returns N (±1), raw points land on the drawn centres, `point_to` finds the one red square. The raw-points test fails if the Flash fallback answered instead of ER 2. |
| `test_invoice_two_read.py` | `read` + `verify` via `vision.invoice.read_invoice` | On a PIL-rendered bill with correct arithmetic: both reads agree on the total, no arithmetic issue, three lines read. |

Notes

- Every image is drawn inside the test (`conftest.py`: `draw_items`, `draw_bill`) and carries a "SYNTHETIC
  TEST IMAGE" banner. Nothing here is evidence about real shelves, packs or bills.
- Models come from `models.DEFAULTS` or an explicit `AIRA_MODEL_<ROLE>` env var; the Firestore remote-config
  override is disabled for these tests, so no GCP project is needed.
- These tests cost API calls and a minute or so of wall-clock time; they are not part of the default run.
