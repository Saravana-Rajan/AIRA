# Security note: one demo phone on a public URL

AIRA has **no login** in this build (build overrides §1: no Firebase Auth). The `aira-live` service is deployed
`--allow-unauthenticated`, and `AIRA_ALLOW_NO_TOKEN=1` makes every accepted `hello` land in the demo shop as its
owner. That is right for a one-user demo, and it means anyone who finds the Cloud Run URL could try to open the
WebSocket. Three layers in `services/live/aira_live/main.py` and `auth.py` keep that out without a login step.

## 1. Shared app key (`AIRA_APP_KEY` = `hello.idToken`)

The app is built with `VITE_AIRA_APP_KEY` and sends that value as `hello.idToken`. `auth.verify_hello` refuses a
hello (`error/permission`, close 4403) unless the token equals `AIRA_APP_KEY` on the service, compared with
`hmac.compare_digest`. In mock mode, or with `AIRA_APP_KEY` unset (a local run), nothing is checked.

**This is a shared demo key, not user authentication.** There is one phone and one shop, so one key is enough to
keep strangers out; it does not identify who is holding the phone, and anyone with the built app bundle can read
it. If that ever matters (a public pilot, several shops), the next step is a real login (Firebase Auth) with
`AIRA_ALLOW_NO_TOKEN=0`; the `hello.idToken` field is already in the contract for it.

| Where | Name | Value |
|---|---|---|
| Secret Manager | `aira-app-key` | a random string (`openssl rand -hex 24`) |
| `aira-live` env (from `--update-secrets` in `deploy.yml` and `infra/deploy_live.sh`) | `AIRA_APP_KEY` | `aira-app-key:latest` |
| GitHub repository secret, read by `deploy.yml` into the hosting build | `VITE_AIRA_APP_KEY` | the same string |

Create it once (the project id comes from env, never from git; the value is read from a hidden prompt):

```bash
export PROJECT_ID=<PROJECT_ID>
KEY="$(openssl rand -hex 24)"
printf '%s' "$KEY" | gcloud secrets create aira-app-key --replication-policy=automatic --data-file=- --project "$PROJECT_ID"
gcloud secrets add-iam-policy-binding aira-app-key \
  --member="serviceAccount:aira-live@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor --project "$PROJECT_ID"
printf '%s' "$KEY" | gh secret set VITE_AIRA_APP_KEY --repo Saravana-Rajan/AIRA
unset KEY
```

The next push to `main` attaches the secret to the service and bakes the key into the hosting build. Rotate it
by adding a secret version, updating the GitHub secret and pushing: the old app bundle stops working at once.
`aira_live.devtools.ws_smoke` reads `AIRA_APP_KEY` from its environment (never from the command line).

## 2. Browser Origin allowlist (`/ws` and the HTTP API)

A browser always sends the `Origin` header on a WebSocket upgrade and on cross-site `fetch`. `aira-live` refuses
an upgrade whose Origin is **missing or not on the list** (close code 4403, before the handshake completes), and
the same list is the CORS allowlist of `GET /api/forecast`.

| Env var on `aira-live` | Default |
|---|---|
| `AIRA_ALLOWED_ORIGINS` (comma-separated; **replaces** the default) | `https://aira-shop.web.app,https://aira-shop.firebaseapp.com,http://localhost:5173,http://localhost:4173` |

So another web page cannot embed or script the demo shop, and a bare script that sends no Origin is turned away
before it can even try a hello. The phone app is a Trusted Web Activity (Chrome) on the hosting site, so its
Origin is `https://aira-shop.web.app` and the owner never notices the check. For a local run, the Vite dev server
(`:5173`) and preview (`:4173`) are already allowed. `ws_smoke` sends the hosting site's Origin (`--origin`).

## 3. Session caps

`hello` is refused with `error/permission` ("AIRA is busy with other phones") when either cap is reached:

| Constant (`main.py`) | Value | Why |
|---|---|---|
| `MAX_SESSIONS_PER_IP` | 3 | one address cannot take the whole service |
| `MAX_SESSIONS` | 6 | the service runs on one instance; this bounds what Gemini Live can cost in total |

The address is the **last** `X-Forwarded-For` hop: Cloud Run's frontend appends the address it saw the
connection come from, while anything before it is whatever the client chose to send and could be forged. With
no forwarded header (a local run) the peer address is used.

A phone that reconnects with `hello.resume` replaces its own session, so it never counts twice. The counts are
in-process (`aira_live.sessions`), which is enough because `--max-instances 1`.

## What this is not

- It is not authentication of a person. Anything that commits stock, money or state still goes through
  `confirm.propose` and the owner's spoken "yes", and the model never writes directly.
- The Firestore rules (`infra/firestore.rules`) are a separate guard: public **read** of the dashboard's
  collections of the demo shop only, no client writes, and the server-only collections (suppliers, proposals,
  contact and message indexes, ledger, memory, events) are closed.

## Vendor channel (Telegram)

`POST /webhooks/telegram` accepts only updates carrying the `X-Telegram-Bot-Api-Secret-Token` header that
matches `TELEGRAM_WEBHOOK_SECRET`, and refuses everything while the secret is unset. `POST /webhooks/whatsapp`
is the same, fail-closed, on `WHATSAPP_APP_SECRET` (not mounted yet: WhatsApp comes later).

Supplier ids are public (`content/catalog`), so every vendor deep link carries an unguessable tag:
`?start=<supplier_id>.<tag>` with `tag = HMAC-SHA256(TELEGRAM_WEBHOOK_SECRET, supplier_id)[:20]`, checked with
`hmac.compare_digest`. A `/start` without the right tag is ignored, **including the first one** for a supplier.
The link is the credential: send it to the vendor privately; a vendor who changes phones opens the same link
again and the old chat is unlinked (`docs/ops/telegram-setup.md` §5). There is no first-come window and no
separate re-link code.
