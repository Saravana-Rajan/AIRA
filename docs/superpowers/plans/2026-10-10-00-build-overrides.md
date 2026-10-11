# AIRA — Build Overrides (these WIN over any plan file)

The plans were written before the team made its final setup decisions. When a plan says something different from this file, **follow this file**. Everything else in the plans and in `2026-10-10-00-interfaces.md` stays as written.

## 1. Minimal setup (one user, one shop)

| Plan says | Build this instead |
|---|---|
| Firestore `(default)` database | Database id **`aira`** everywhere. `aira_live.db.db()` returns `firestore.AsyncClient(project=GCP_PROJECT, database=os.getenv("FIRESTORE_DATABASE", "aira"))`. Firebase SDK clients use `getFirestore(app, "aira")`. Firestore rules/indexes target database `aira` only. **Never touch `(default)`** |
| Firebase Auth (anonymous auth, ID tokens) | **No Firebase Auth.** The app sends `hello` without `idToken`. The server accepts it when `AIRA_ALLOW_NO_TOKEN=1` (set in the deployed service) and uses `AIRA_DEMO_SHOP=sunrise_dairy`, role `owner`. Keep `auth.verify_hello` but make "no token + AIRA_ALLOW_NO_TOKEN=1" return `AuthResult(uid, "sunrise_dairy", "owner")` |
| Firebase Remote Config | **No Remote Config.** Keep the module `aira_live/remote_config.py` and its function names (`flag`, `get_override`, `override_for_role`, `refresh`). They read the Firestore doc `config/runtime` in database `aira` (fields `pointing_model`, `count_model`, `live_model`, `whatsapp_enabled`) with a 60 s cache, falling back to defaults when the doc is missing |
| Pub/Sub topics, BigQuery, BigQuery ML, Pub/Sub→BigQuery subscriptions | **None.** Keep `aira_live.analytics.events.publish(topic, payload)`; it appends `{topic, payload, ts}` to Firestore `shops/{shop_id}/events/{autoId}` and returns the doc id (returns `"mock"` in mock mode). `analytics.forecast.daily_demand(shop_id, sku)` = the 7-day moving average of SALE ledger deltas from Firestore (None if fewer than 3 days of data). `analytics.summary.today` reads Firestore |
| Cloud Scheduler jobs | **None.** Keep the `/jobs/expiry`, `/jobs/day-summary` and `/jobs/forecast-refresh` endpoints, callable manually or by the app at startup ("morning check"). There's no OIDC requirement: protect them with header `X-AIRA-Job-Token` = env `AIRA_JOB_TOKEN` |
| FCM family alerts, `/fcm/register`, family role UI | **Skip.** Alerts are spoken in the owner's live session via `sessions.notify` |
| App Check | Skip |
| Cloud Build `cloudbuild.yaml` | **GitHub Actions** auto-deploy instead (§3) |
| Budget alerts | Skip (shared billing account) |
| Two Hosting targets (app, dashboard, portal) | **ONE Hosting site: `aira-shop`.** App at `/`; dashboard built with Vite `base: "/dashboard/"` into `dist/dashboard/`. `firebase.json` has a single hosting entry, `"site": "aira-shop"`, `"public": "dist/hosting"`, with rewrites `/dashboard/**` → `/dashboard/index.html` and `**` → `/index.html`. A script `scripts/build_hosting.sh` builds both apps into `dist/hosting/` |
| Secrets `GEMINI_API_KEY` etc. | Secret Manager name **`aira-gemini-api-key`**, mounted as env `GEMINI_API_KEY`. WhatsApp secrets are optional; when they're absent, the click-to-chat fallback is used |
| Region | `asia-south1` |
| Cloud Run | Service `aira-live`, `--min-instances 1 --max-instances 1 --timeout 3600 --session-affinity`, service account `aira-live@<project>.iam.gserviceaccount.com` |
| Bubblewrap APK build | Write `apps/app/twa/twa-manifest.json` + `apps/app/twa/README.md` (build steps). **Do not run Bubblewrap**: no JDK is installed yet |

**The project ID is NEVER committed.** Scripts and code read `GCP_PROJECT` from env or `.env`. The demo shop id is `sunrise_dairy`.

## 2. Folder structure (keep it clean — no stray files)

```
.github/workflows/      ci.yml (tests on PRs) · deploy.yml (push to main → test + deploy)
apps/app/               phone app (Vite + TS + Preact): src/{core,voice,camera,guidance,ui,demo,i18n,net,skills}/, test/, public/, twa/
apps/dashboard/         Shop Dashboard (Vite + TS + Preact): src/{pages,components,lib}/, test/
packages/contracts/     src/, fixtures/, test/
services/live/          pyproject.toml, uv.lock, Dockerfile, .python-version (3.12)
  aira_live/            core modules + tools/ vision/ memory/ business/ messaging/ analytics/ devtools/ i18n/ steps/
  tests/                mirrors the package: tests/{core,tools,vision,memory,business,messaging,analytics}/
content/                catalog/, steps/, lessons/, schemas/
bench/                  data/<set>/labels.json (no fabricated data), score_*.py, spike_er2.py, publish.py
infra/                  bootstrap.sh, deploy_live.sh, firestore.rules, firestore.indexes.json, setup_github_deploy.sh
scripts/                seed_firestore.py, build_hosting.sh, check_submission.py
docs/                   unchanged layout
```

- No files at the repo root except: README.md, CLAUDE.md, package.json, package-lock.json, firebase.json, .firebaserc (use a placeholder project alias resolved from env in CI), .gitignore, .env.example.
- Tests sit next to their workspace: `apps/*/test`, `packages/contracts/test`, `services/live/tests`.

## 3. Auto-deploy on push to `main` (GitHub Actions + Workload Identity Federation)

**`.github/workflows/ci.yml`** runs on pull requests and on pushes to non-main branches:
- `npm ci && npm test` (all workspaces)
- `cd services/live && uv sync --frozen && uv run pytest -q`

**`.github/workflows/deploy.yml`**, on push to `main`:
1. **Run the tests**, the same as `ci.yml`.
2. **Authenticate:** `google-github-actions/auth@v2` with `workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}` and `service_account: ${{ vars.GCP_DEPLOY_SA }}`. No JSON key.
3. **Deploy the backend:** `google-github-actions/setup-gcloud@v2` → `gcloud run deploy aira-live --source services/live --region asia-south1 --project ${{ vars.GCP_PROJECT }}` with the §1 flags, `--set-secrets GEMINI_API_KEY=aira-gemini-api-key:latest` and `--set-env-vars FIRESTORE_DATABASE=aira,AIRA_DEMO_SHOP=sunrise_dairy,AIRA_ALLOW_NO_TOKEN=1,GCP_PROJECT=${{ vars.GCP_PROJECT }}`.
4. **Build the hosting bundle:** `bash scripts/build_hosting.sh`, with `VITE_AIRA_LIVE_WS` = `wss://<cloud-run-host>/ws` read from `gcloud run services describe`, plus the `VITE_FIREBASE_*` repository variables.
5. **Deploy hosting:** `npx firebase-tools deploy --only hosting:aira-shop --project ${{ vars.GCP_PROJECT }} --non-interactive`, using the same WIF credentials.

The repository **variables** (not secrets) `GCP_PROJECT`, `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA` and `VITE_FIREBASE_*` are set by the maintainer.

`infra/setup_github_deploy.sh` documents and creates the GCP side:
- workload identity pool `aira-github-pool` and provider `aira-github`, with attribute condition `assertion.repository=='Saravana-Rajan/AIRA' && assertion.ref=='refs/heads/main'`
- service account `aira-deployer`
- the minimum roles for Cloud Run source deploy + Firebase Hosting

All resources are named `aira-*`.

## 4. Commits

- **NEVER** add `Co-Authored-By`, `Claude-Session`, "Generated with Claude" or any AI attribution to commit messages or files. This is a hard requirement from the repo owner.
- Use plain conventional messages (`feat(business): …`, `test(vision): …`).
- Commit only inside your assigned worktree path. Never push. The integrator pushes.

## 5. Things the build cannot fake

**Real data and real-world steps:**
- No fabricated benchmark data. Create scorers + `labels.json` formats + README instructions for collecting the photos and clips.
- No real WhatsApp/Telegram tokens: code + tests with fakes. The live demo uses click-to-chat until tokens exist.
- The user study, the video and the deck PDF are human tasks. `saravana/03` produces drafted text, scripts and checklists only.

**Network use during the build:**
- Real Gemini calls are allowed only in explicitly marked spike/bench scripts and opt-in tests (`AIRA_LIVE_TESTS=1`).
- The default test suite must pass offline with fakes.

## 3a. Auto-deploy: FINAL setup (already created in GCP and GitHub; deploy.yml MUST match this)

**GitHub repository SECRETS** (not variables; the repo is public, and secrets are masked in logs):
- `GCP_PROJECT`, `GCP_WIF_PROVIDER`, `GCP_DEPLOY_SA`
- `VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_AUTH_DOMAIN`, `VITE_FIREBASE_PROJECT_ID`, `VITE_FIREBASE_STORAGE_BUCKET`, `VITE_FIREBASE_MESSAGING_SENDER_ID`, `VITE_FIREBASE_APP_ID`

Use `${{ secrets.X }}` everywhere. **Never** use `vars.`.

**`.github/workflows/deploy.yml`**, on push to `main`; permissions `contents: read`, `id-token: write`:
1. **test job:** `npm ci && npm test`; `cd services/live && uv sync --frozen && uv run pytest -q`.
2. **deploy job** (`needs: test`):
   - `google-github-actions/auth@v2` with `workload_identity_provider: ${{ secrets.GCP_WIF_PROVIDER }}`, `service_account: ${{ secrets.GCP_DEPLOY_SA }}`, `create_credentials_file: true`.
   - `google-github-actions/setup-gcloud@v2`, then `gcloud auth configure-docker asia-south1-docker.pkg.dev --quiet`.
   - **Build the image in the runner (NOT Cloud Build):**
     `docker build -t asia-south1-docker.pkg.dev/${{ secrets.GCP_PROJECT }}/aira/aira-live:${{ github.sha }} services/live`
     Then `docker push` the same tag.
   - **Deploy:** `gcloud run deploy aira-live --image <that tag> --region asia-south1 --project ${{ secrets.GCP_PROJECT }} --service-account aira-live@${{ secrets.GCP_PROJECT }}.iam.gserviceaccount.com --min-instances 1 --max-instances 1 --timeout 3600 --session-affinity --allow-unauthenticated --set-secrets GEMINI_API_KEY=aira-gemini-api-key:latest --set-env-vars FIRESTORE_DATABASE=aira,AIRA_DEMO_SHOP=sunrise_dairy,AIRA_ALLOW_NO_TOKEN=1,GCP_PROJECT=${{ secrets.GCP_PROJECT }}`
   - **Hosting:**
     - `WS_HOST=$(gcloud run services describe aira-live --region asia-south1 --project ... --format='value(status.url)' | sed 's#https://##')`.
     - `VITE_AIRA_LIVE_WS=wss://$WS_HOST/ws` + the `VITE_FIREBASE_*` secrets → `bash scripts/build_hosting.sh`.
     - `npx firebase-tools@latest deploy --only hosting:aira-shop --project ${{ secrets.GCP_PROJECT }} --non-interactive`. firebase-tools uses `GOOGLE_APPLICATION_CREDENTIALS` from the auth step.

**Who can deploy (owner's rule):** ANY push to `main` of **`Saravana-Rajan/AIRA`**, by any teammate or collaborator. No other repo or branch can deploy.
- The GCP provider condition is already applied: `assertion.repository=='Saravana-Rajan/AIRA' && assertion.ref=='refs/heads/main'`.
- `deploy.yml` gates the deploy job with `if: github.repository == 'Saravana-Rajan/AIRA' && github.ref == 'refs/heads/main'`. There's no actor check.
- Integration may push the tested result to `main` as a fast-forward.
- Never touch any other git repo or GCP project.

**Workflow security** (from the automated security review; mandatory in both `ci.yml` and `deploy.yml`):
- **Permissions:** top-level `permissions: { contents: read }` only. Put `id-token: write` **only** on the `deploy` job (`jobs.deploy.permissions: { contents: read, id-token: write }`). The `test` job never gets `id-token`.
- **Pin every action to a full commit SHA**, with the tag as a comment:

| Action | Pinned SHA |
|---|---|
| `actions/checkout` | `11d5960a326750d5838078e36cf38b85af677262` (v4) |
| `actions/setup-node` | `49933ea5288caeca8642d1e84afbd3f7d6820020` (v4) |
| `astral-sh/setup-uv` | `d0cc045d04ccac9d8b7881df0226f9e82c39688e` (v6) |
| `google-github-actions/auth` | `c200f3691d83b41bf9bbd8638997a462592937ed` (v2) |
| `google-github-actions/setup-gcloud` | `e427ad8a34f8676edf47cf7d7925499adf3eb74f` (v2) |

- **No `pull_request_target`.** `ci.yml` uses `pull_request` with no secrets.
- **Concurrency:** `deploy.yml` sets `concurrency: { group: deploy-main, cancel-in-progress: false }`.

**Already created in GCP** (don't recreate):

| Resource | Notes |
|---|---|
| Service account `aira-live` | Runtime. `datastore.user` limited by IAM condition to database `aira`; `secretAccessor` on `aira-gemini-api-key` only |
| Service account `aira-deployer` | `workloadIdentityUser` for the repo; `artifactregistry.writer` on repo `aira`; `serviceAccountUser` on `aira-live`; `run.developer` conditioned to `aira-*` services; `firebasehosting.admin`; `serviceUsageConsumer` |
| Artifact Registry repo `aira` | `asia-south1` |
| Workload identity | Pool `aira-github-pool`, provider `aira-github` (repo `Saravana-Rajan/AIRA`, ref `refs/heads/main` only) |

`infra/setup_github_deploy.sh` documents exactly these commands; it's re-runnable and idempotent.

## 7. Models: final choice (verified against the project's model list, 2026-10-10)

`aira_live/models.py` defaults. Env `AIRA_MODEL_<ROLE>` overrides them, then the Firestore `config/runtime` doc does.

| Role | Model | Status | Why |
|---|---|---|---|
| `live` | `gemini-3.8-live` | Stable | Lowest-latency voice agent; barge-in; Tamil/Hindi |
| `point`, `count` | `gemini-robotics-er-2-preview` | Preview | The only spatial/counting model |
| fallback for `point`, `count` | `gemini-3.8-flash` | Stable | |
| `read` (invoice/cash read A) | `gemini-3.8-flash` | Stable | Google's "most intelligent Flash" |
| `verify` (independent read B + safety number check) | `gemini-3.6-flash` | Stable | A **different model generation** than read A, so the two reads aren't correlated |

**Do not use:**
- `gemini-3.8-live-extended-thinking`: slower; async calls only.
- `gemini-3.1-pro-preview`: preview and slower.
- `gemini-robotics-er-1.6-preview`: shut down.
- `gemini-2.5-computer-use-preview`.
- Any image or video generation models.

## 6. Visual design: LIGHT THEME ONLY

The owner uses AIRA by voice, but judges, family and helpers see the screen. UX is 10% of the score, and there's a Best UI/UX prize. **Light theme only.** No dark mode, no `prefers-color-scheme: dark` styles.

**Design tokens** live in `apps/app/src/ui/tokens.css`. The dashboard has its own copy at `apps/dashboard/src/styles/tokens.css`. Colours come from the AIRA logo:

| Token | Value | Use |
|---|---|---|
| `--bg` | `#FFFFFF` | page background |
| `--surface` | `#F5F8FD` | cards |
| `--border` | `#E3E9F4` | dividers |
| `--text` | `#0B1F4B` (logo navy) | primary text |
| `--text-muted` | `#4A5A7A` | secondary text (contrast ≥ 4.5:1 on white) |
| `--brand-blue` | `#1F6FFF` | primary accent, links, focus ring |
| `--brand-cyan` | `#22C3E6` | listening state |
| `--brand-purple` | `#7B4DFF` | thinking state |
| `--brand-orange` | `#FF8A3D` | warnings |
| `--ok` | `#16A34A` | match / done |
| `--danger` | `#DC2626` | mismatch / bump guard |
| `--gradient` | `linear-gradient(135deg, #22C3E6, #1F6FFF 40%, #7B4DFF 70%, #FF8A3D)` | logo accents only |

**Typography:**
- Fonts: `"Inter", "Noto Sans Tamil", "Noto Sans Devanagari", system-ui, sans-serif`, loaded from Google Fonts.
- Body text 18 px on the phone and 16 px on the dashboard.
- Captions 28–32 px.
- Status text 22 px semibold.

**Phone app screen** (still ONE full-screen gesture surface; nothing small to tap):
- **Top bar:** the small AIRA logo (`assets/logo/aira-logo-192.png`) and a status chip that changes colour by state:
  - Ready (blue)
  - Listening (cyan, with a pulse animation)
  - Thinking (purple)
  - Counting 12 / 30 (blue)
  - Warning (orange)
  - Stop! (red, bump guard)
- **Centre:** a large animated voice orb / waveform reflecting the mic and speaker level.
- **Live caption area:** the last thing the user said and the last thing AIRA said, in large high-contrast text (28 px+). This doubles as captions in the demo video.
- **Bottom:** the current task card, e.g. "Sale: 2 × Sakthi curd 1 L — ₹120", "Delivery: 28 / 30 counted", "Order sent to Sakthi vendor ✓".
- The whole screen keeps `aria-label` "AIRA. Double-tap to talk. Long-press to stop."
- Respect `prefers-reduced-motion`.

**Dashboard (`/dashboard`):**
- A clean header with the logo, "Sunrise Dairy", and a live indicator dot.
- Cards: **Today** (sales, revenue, top product) · **Stock** (table with low-stock and expiry badges) · **Orders & Deliveries** (state timeline, vendor replies, discrepancies in red) · **Audit trail**.
- Responsive: 1 column on a phone, 2–3 on a laptop.
- WCAG AA contrast, keyboard focus rings, Lighthouse accessibility ≥ 95.

**Landing / judge demo page (`/` before Start, or `/?demo=1`):**
- A hero with the full logo, the tagline "A blind shopkeeper runs his own shop. Alone.", and a "Try AIRA" button.
- One scenario card per demo scene, plus a link to `/dashboard`.

**UI agents:** follow the `frontend-design` skill's guidance for a distinctive, polished, non-template look within these tokens.
