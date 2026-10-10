# AIRA — Build Overrides (these WIN over any plan file)

The plans were written before the team made its final setup decisions. When a plan says something different from this file, **follow this file**. Everything else in the plans and in `2026-10-10-00-interfaces.md` stays as written.

## 1. Minimal setup (one user, one shop)

| Plan says | Build this instead |
|---|---|
| Firestore `(default)` database | Database id **`aira`** everywhere. `aira_live.db.db()` returns `firestore.AsyncClient(project=GCP_PROJECT, database=os.getenv("FIRESTORE_DATABASE", "aira"))`. Firebase SDK clients use `getFirestore(app, "aira")`. Firestore rules/indexes target database `aira` only. **Never touch `(default)`** |
| Firebase Auth (anonymous auth, ID tokens) | **No Firebase Auth.** The app sends `hello` without `idToken`. The server accepts it when `AIRA_ALLOW_NO_TOKEN=1` (set in the deployed service) and uses `AIRA_DEMO_SHOP=murugan_dairy`, role `owner`. Keep `auth.verify_hello` but make "no token + AIRA_ALLOW_NO_TOKEN=1" return `AuthResult(uid, "murugan_dairy", "owner")` |
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

**The project ID is NEVER committed.** Scripts and code read `GCP_PROJECT` from env or `.env`. The demo shop id is `murugan_dairy`.

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
3. **Deploy the backend:** `google-github-actions/setup-gcloud@v2` → `gcloud run deploy aira-live --source services/live --region asia-south1 --project ${{ vars.GCP_PROJECT }}` with the §1 flags, `--set-secrets GEMINI_API_KEY=aira-gemini-api-key:latest` and `--set-env-vars FIRESTORE_DATABASE=aira,AIRA_DEMO_SHOP=murugan_dairy,AIRA_ALLOW_NO_TOKEN=1,GCP_PROJECT=${{ vars.GCP_PROJECT }}`.
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
- A clean header with the logo, "Murugan Dairy", and a live indicator dot.
- Cards: **Today** (sales, revenue, top product) · **Stock** (table with low-stock and expiry badges) · **Orders & Deliveries** (state timeline, vendor replies, discrepancies in red) · **Audit trail**.
- Responsive: 1 column on a phone, 2–3 on a laptop.
- WCAG AA contrast, keyboard focus rings, Lighthouse accessibility ≥ 95.

**Landing / judge demo page (`/` before Start, or `/?demo=1`):**
- A hero with the full logo, the tagline "A blind shopkeeper runs his own shop. Alone.", and a "Try AIRA" button.
- One scenario card per demo scene, plus a link to `/dashboard`.

**UI agents:** follow the `frontend-design` skill's guidance for a distinctive, polished, non-template look within these tokens.
