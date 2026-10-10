# AIRA ops: monitoring and runtime switches

This is the minimal setup: one shop, the Cloud Run service `aira-live` in `asia-south1`, and Firestore database `aira`. Nothing here is automated yet. These are the checks to add once the demo build is stable. The project ID is never written in the repo, so every command reads it from `$GCP_PROJECT`.

## 1. Uptime check on `aira-live` (add later)

**What:** an HTTPS uptime check on `GET /healthz` of `aira-live`. A healthy service returns `200` with `{"ok":true,"mock":false}`.

**Why:** the demo link must never silently break. `aira-live` runs `--min-instances 1 --max-instances 1`, so a crash-looping revision means AIRA is down for the shop.

Create it in the console under **Monitoring → Uptime checks → Create**, or with gcloud (check the flags against the [`gcloud monitoring uptime create`](https://cloud.google.com/sdk/gcloud/reference/monitoring/uptime/create) reference before running):

```bash
LIVE_HOST=$(gcloud run services describe aira-live --region asia-south1 --project "$GCP_PROJECT" \
  --format='value(status.url)' | sed 's#https://##')

gcloud monitoring uptime create aira-live-healthz --project "$GCP_PROJECT" \
  --resource-type=uptime-url \
  --resource-labels="host=${LIVE_HOST},project_id=${GCP_PROJECT}" \
  --path=/healthz --protocol=https --period=5 \
  --matcher-content='"ok":true' --matcher-type=contains-string
```

Recommended settings:

| Setting | Value |
|---|---|
| Path | `/healthz` |
| Period | 5 min (1 min during the judging window) |
| Timeout | 10 s |
| Content match | contains `"ok":true` |
| Regions | default (global) |

**Also check `"mock":false`.** If the deployed service loses its `GEMINI_API_KEY` secret, it falls back to mock mode and still answers `200`. Add a second check whose content matcher is `"mock":false` so that this case alerts too.

**Hosting:** you can also add a check on the Hosting site `aira-shop`, at path `/` and at `/dashboard/`.

## 2. Alert policy (add with the uptime check)

Create one policy: **"AIRA uptime check failing"**.

- **Condition:** metric `monitoring.googleapis.com/uptime_check/check_passed`, resource `uptime_url`, filter `metric.label.check_id = "aira-live-healthz"`.
- **Trigger:** the check fails from more than one region for 2 minutes.
- **Notification channel:** the owner's email. Add a phone/SMS channel during the judging window.
- **Documentation field:** link to this file, section 4.

Log-based metrics to add later (Logs Explorer → Create metric), filtered on `resource.labels.service_name="aira-live"`:

| Signal | Log filter (`textPayload` / `jsonPayload.message`) | Why |
|---|---|---|
| Runtime config unreadable | `"runtime config refresh failed"` | Firestore permission error or outage. AIRA keeps the last good switches and retries after 10 s |
| Bad model id in config | `"ignoring unknown model"` | Someone typed a model id that is not listed in `aira_live/models.py` |
| Server errors | `severity>=ERROR` | General health |

## 3. Runtime switches: Firestore `config/runtime`

There is no Remote Config. Runtime switches live in one Firestore document, `config/runtime`, in database `aira`. `aira_live/remote_config.py` reads it and caches it for 60 s. A missing document means "use the defaults".

| Field | Type | Effect |
|---|---|---|
| `pointing_model` | string | Model for `point` (`point_to`). It must be listed in `aira_live/models.py` (`DEFAULTS` or `FALLBACKS`); otherwise it is ignored |
| `count_model` | string | Model for `count` (`count_in_frame`). Same rule |
| `live_model` | string | Gemini Live voice model. Same rule |
| `whatsapp_enabled` | bool | `false` → supplier orders use the click-to-chat fallback |

**Model priority:** env `AIRA_MODEL_<ROLE>` > `config/runtime` > `models.DEFAULTS`. An env override on the Cloud Run service always wins, so remove it before relying on the Firestore switch.

**Access:** only the server writes this doc, using the Admin SDK. The Firestore rules (`infra/firestore.rules`) deny every client read and write outside `shops/murugan_dairy`.

**Refresh:** the first time a switch is read in a running event loop, a background refresh starts, and after that the cache refreshes whenever it is older than 60 s. Starting `remote_config.run_refresher()` in the app lifespan keeps the cache warm from boot.

## 4. Runbook: ER 2 is slow or failing → switch to the fallback model, no redeploy

1. **Switch.** Run these from `services/live`, with ADC and `GCP_PROJECT` set:

   ```bash
   uv run python -m aira_live.remote_config set pointing_model fallback
   uv run python -m aira_live.remote_config set count_model fallback
   ```

   `fallback` writes the role's model from `models.FALLBACKS`. You can also edit the field in the Firebase console (Firestore → database `aira` → `config/runtime`). Any model id you type there must be listed in `aira_live/models.py`.

2. **Wait up to 60 s.** The running instance picks up the change on its next cache refresh.

3. **Verify.** Ask AIRA to find a product. The `aira-live` logs show the fallback model id on the `point_to` call.

4. **Switch back** once ER 2 recovers. `default`, or an empty value, clears the override:

   ```bash
   uv run python -m aira_live.remote_config set pointing_model default
   uv run python -m aira_live.remote_config set count_model default
   uv run python -m aira_live.remote_config show
   ```

**Turn WhatsApp off** (for example, the token expired or is rate limited):

```bash
uv run python -m aira_live.remote_config set whatsapp_enabled false
```

## 5. Not set up (by decision)

- **No Cloud Scheduler.** Call `POST /jobs/expiry`, `/jobs/day-summary` and `/jobs/forecast-refresh` by hand or from the app's morning check, with header `X-AIRA-Job-Token` equal to env `AIRA_JOB_TOKEN`. When that env var is unset, the routes refuse every call.
- **No budget alerts.** The billing account is shared.
- **No Pub/Sub, BigQuery, Firebase Auth, Remote Config or FCM.** See `docs/superpowers/plans/2026-10-10-00-build-overrides.md`.
