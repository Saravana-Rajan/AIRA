# AIRA voice smoke test (manual)

This check proves that a question spoken to AIRA reaches Gemini Live and comes back as voice. Run it locally first, then against Cloud Run, then on the phone. It takes about 15 minutes.

Two developer tools in `services/live` do the work:

- `aira_live.devtools.ws_smoke` connects to `/ws` like the phone, asks one question, and reports what comes back and how fast.
- `aira_live.devtools.call_tool` runs one Live tool from the shell, the way a Live call runs it (section 6).

The project ID is never written in the repo. Every command reads it from `$GCP_PROJECT`.

## 1. What you need

- `services/live` set up with `uv sync`.
- For real mode:
  - `GEMINI_API_KEY` in your shell. It's stored in Secret Manager as `aira-gemini-api-key`. Never commit it.
  - `GCP_PROJECT` in your shell or in the repo's `.env`.
  - Firestore access through gcloud ADC (`gcloud auth application-default login`). AIRA uses database `aira` only.
  - The demo shop seeded: `uv run --project services/live python scripts/seed_firestore.py`.
- `ffmpeg`, to convert the recording.

## 2. Record the question

Record one question on your phone or laptop: **"How much Sakthi curd do we have?"** Record it in Tamil and Hindi as well if you can.

Convert each recording to 16 kHz mono PCM16. `ws_smoke` refuses any other format.

```bash
ffmpeg -i question.m4a -ar 16000 -ac 1 -sample_fmt s16 question_16k.wav
```

Keep the recordings out of git. They are a real person's voice.

## 3. Step 1: mock mode, local (no key)

This checks the socket and the protocol only. Gemini isn't called.

```bash
cd services/live && AIRA_ALLOW_NO_TOKEN=1 uv run uvicorn aira_live.main:app --port 8080
curl -s localhost:8080/health
cd services/live && uv run python -m aira_live.devtools.ws_smoke ws://localhost:8080/ws --text "How much Sakthi curd do we have?" --idle 2
```

Expected:

- `/health` returns `{"ok":true,"mock":true}`.
- `ws_smoke` prints `{"t": "ready", ..., "mock": true}` and an `aira` transcript that echoes the question.
- `ws_smoke` prints the line `the server is in MOCK mode`, and the run ends `OK` (exit code 0).

## 4. Step 2: real mode, local

```bash
cd services/live && AIRA_ALLOW_NO_TOKEN=1 GEMINI_API_KEY=$GEMINI_API_KEY GCP_PROJECT=$GCP_PROJECT \
  uv run uvicorn aira_live.main:app --port 8080
curl -s localhost:8080/health
cd services/live && uv run python -m aira_live.devtools.ws_smoke ws://localhost:8080/ws question_16k.wav
cd services/live && uv run python -m aira_live.devtools.ws_smoke ws://localhost:8080/ws --text "How much Sakthi curd do we have?"
```

Expected:

- `/health` returns `{"ok":true,"mock":false}`.
- `{"t": "ready", ..., "mock": false}`.
- WAV only: a `user` transcript with your question.
- An `aira` transcript with the answer. If `stock_query` isn't merged yet, AIRA says it can't check stock. That still passes this test.
- `run 1: received N bytes of 24 kHz audio (~S s); first audio M ms; 0 errors; OK`, with N > 0.
- No `error` messages.
- A `cue` of `warn` means the safety check caught a money or stock number that no tool returned. Write down the transcript.

Then ask the Tamil and Hindi recordings with `--lang ta` and `--lang hi`. The answer should come back in the same language.

## 5. Step 3: the deployed service (Cloud Run)

```bash
WS_HOST=$(gcloud run services describe aira-live --region asia-south1 --project "$GCP_PROJECT" \
  --format='value(status.url)' | sed 's#https://##')
curl -s https://$WS_HOST/health
cd services/live && uv run python -m aira_live.devtools.ws_smoke wss://$WS_HOST/ws question_16k.wav --runs 5
```

Expected:

- `/health` returns `{"ok":true,"mock":false}`. Use `/health`, not `/healthz`: Cloud Run's frontend reserves some paths that end in `z`.
- All 5 runs end `OK`.
- A final line `p50 first-audio latency: M ms over 5 runs`. Copy it into the results table (section 8).

`ws_smoke` sends `hello` without a token, as the app does. The deployed service accepts it because it runs with `AIRA_ALLOW_NO_TOKEN=1`. The service always serves the demo shop `murugan_dairy`, whatever `shopId` the client sends.

## 6. Step 4: the phone

Use the hosted app on the chest-mounted phone, with one earphone in.

- [ ] Start AIRA. The status chip shows Ready, then Listening.
- [ ] Ask "How much Sakthi curd do we have?" in English, then in Tamil, then in Hindi. Each answer is spoken in that language.
- [ ] Long-press while AIRA is answering. The audio stops at once. Ask again, and AIRA answers normally.
- [ ] Ask AIRA to order 30 Sakthi curd. AIRA asks you to confirm. Say "no". Nothing is sent. (Order tools must be merged.)

## 7. Tool harness: `call_tool`

`call_tool` runs one Live tool from the shell. It uses the same wrapper, session and idempotency keys as a Live call. Feed it photos with `--image`. When the tool asks for camera frames, the harness answers with those photos, like the phone would.

- stdout: every server message the tool sends (one JSON per line), then the tool's result (one line).
- stderr: the mode. Mock mode runs without `GEMINI_API_KEY`. In REAL mode the tool calls Gemini and reads and writes Firestore database `aira`.

```bash
cd services/live && uv run python -m aira_live.devtools.call_tool stock_query --args '{"product": "Sakthi curd"}'
cd services/live && uv run python -m aira_live.devtools.call_tool delivery_count --args '{"sku": "sakthi_curd_1l", "mode": "crate"}' --image crate.jpg
cd services/live && uv run python -m aira_live.devtools.call_tool product_identify --image pack.jpg --purpose identify --lang ta
cd services/live && uv run python -m aira_live.devtools.call_tool order_create --args '{"supplier": "Sakthi vendor", "items": [{"name": "curd 1 litre", "qty": 30}]}' --confirm no
```

- `--confirm yes|no` answers a proposal through `confirm_action`, as if the owner said it.
- **In real mode `--confirm yes` commits for real.** A confirmed order can reach the vendor by WhatsApp. Use mock mode or `--confirm no` unless you mean it.
- To check that a retried call commits once, run the same command twice with a fixed `--session-id` and `--call-id`. Both runs get the same idempotency key, so the second commit must not create a second order or payment.

```bash
cd services/live && uv run python -m aira_live.devtools.call_tool order_create --args '{"supplier": "Sakthi vendor", "items": [{"name": "curd 1 litre", "qty": 30}]}' --session-id replay1 --call-id c1 --confirm yes
```

## 8. Record the results

Write down only numbers you measured. Leave a cell empty if you didn't run that check.

| Date | Build (git SHA) | Where | Language | Runs | p50 first audio (ms) | Pass? | Notes |
|---|---|---|---|---|---|---|---|
| | | local | en | | | | |
| | | Cloud Run | en | | | | |
| | | Cloud Run | ta | | | | |
| | | Cloud Run | hi | | | | |

## 9. Automated version (opt-in)

One pytest runs the typed question against an in-process server with real Gemini Live and Firestore. It's skipped by default:

```bash
cd services/live && AIRA_LIVE_TESTS=1 GEMINI_API_KEY=$GEMINI_API_KEY GCP_PROJECT=$GCP_PROJECT \
  uv run pytest tests/core/test_ws_smoke.py -q
```

The rest of `tests/core/test_ws_smoke.py` runs offline in every `uv run pytest`.

## 10. If it fails

| Symptom | Likely cause |
|---|---|
| `error` `permission` right after `hello`, then the socket closes | The server runs in real mode without `AIRA_ALLOW_NO_TOKEN=1` |
| `ready` with `"mock": true` on Cloud Run | `GEMINI_API_KEY` isn't mounted (secret `aira-gemini-api-key`) |
| `error` `network` right after `ready` | Live didn't start. Check `GEMINI_API_KEY`, Firestore access (`GCP_PROJECT`, ADC, database `aira`) and the server log |
| `no ready from the server` | Wrong URL. Use the `/ws` path, and `wss://` for Cloud Run |
| No `user` transcript for a WAV | The recording is silent or very quiet. Re-record closer to the mic |
| `ws_smoke` refuses the WAV | It isn't 16 kHz mono PCM16. Convert it with the `ffmpeg` command in section 2 |
