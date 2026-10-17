# Security note: one demo phone on a public URL

The phone app has **no login** in this build (build overrides §1: no Firebase Auth); the dashboard's live feed is
the one exception (§2a: a signed-in, listed Google account). The `aira-live` service is deployed
`--allow-unauthenticated`, and `AIRA_ALLOW_NO_TOKEN=1` makes every accepted `hello` land in the demo shop as its
owner. That is right for a one-user demo, and it means anyone who finds the Cloud Run URL could try to open the
WebSocket. Three layers in `backend/aira_live/main.py` and `auth.py` keep that out without a login step.

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
| `aira-live` env (from `--update-secrets` in `deploy.yml` and `shared/infra/deploy_live.sh`) | `AIRA_APP_KEY` | `aira-app-key:latest` |
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

## 2a. The dashboard's live feed (`/monitor`): open by default, Google sign-in as an opt-in

**Default: no authentication.** This is the owner's explicit decision, taken after being told the privacy risk
twice: with `AIRA_MONITOR_AUTH` unset on the `aira-live` service, **anyone who can open the dashboard address can
watch the live camera, hear and read the conversations and see every box AIRA draws.** No key, no sign-in, no
viewer list. What still applies: the Origin allowlist below (which is **not** authentication: a script can send
any Origin it likes), the shop binding (`?shop=` is optional; in tokenless / demo mode only the demo shop may be
named) and `MAX_MONITORS_PER_SHOP`. The socket is subscribed as soon as it is open; an `{t:"auth", idToken}`
message sent anyway is accepted and ignored.

**Opt-in: `AIRA_MONITOR_AUTH=1`** on the `aira-live` service, plus `AIRA_MONITOR_VIEWERS`, the Firebase setup
in *Owner setup* below, and the dashboard built with `VITE_MONITOR_AUTH=1`, turns the Google sign-in gate back
on exactly as described in the rest of this section: a viewer signs in with Google on the dashboard (Firebase
Authentication, in the same Firebase project and with the same `VITE_FIREBASE_*` config the dashboard already
uses for Firestore) and the server verifies that account itself. There is **no key and no shared secret** in
either mode (the monitor key was removed by the owner's decision).

How the sign-in works (with `AIRA_MONITOR_AUTH=1`):

1. The browser opens `wss://<aira-live>/monitor?shop=<id>` from the hosting site (the Origin allowlist below).
2. Its **first message, within 5 s**, is `{t:"auth", idToken}` with the signed-in user's Firebase ID token. The
   token never goes in the URL, in storage or in a log line.
3. `aira_live.auth.verify_firebase_user` checks it server-side with the official Google library
   (`google.oauth2.id_token.verify_firebase_token`: RS256 signature against Google's `securetoken` certificates,
   which are cached in process for their `Cache-Control: max-age`; `aud` = the Firebase project; `exp` / `iat`)
   and then `iss` (`https://securetoken.google.com/<project>`), a non-empty `sub` and an `auth_time` that is not
   in the future. The project is `AIRA_FIREBASE_PROJECT`, else `GCP_PROJECT`.
4. `aira_live.auth.monitor_viewer_allowed` checks the account against **`AIRA_MONITOR_VIEWERS`**: comma-separated
   emails (matched lowercased, and only when the token says the email is verified, which a Google sign-in does)
   or `uid:<Firebase uid>` entries. **Unset or empty = nobody may watch.**
5. Only then is the socket subscribed: nothing (no session list, no frame, no transcript, no WebRTC signalling)
   is sent or relayed before. A missing, late, malformed, expired or otherwise invalid token closes the socket
   with **4401**; a valid token for an account that is not listed closes it with **4403**.
6. An ID token lives at most an hour. The dashboard refreshes it about five minutes early and sends a fresh
   `{t:"auth", idToken}` on the same socket; the server verifies it again (same account required) and extends
   the deadline. Without a refresh the server closes the socket with 4401 at `exp` (+ 30 s grace). A refresh
   naming a different account closes it too, and every reconnect authenticates from scratch.

The WebRTC signalling relay (`rtc.py`) and the phone's preview stream only ever reach a monitor that passed this
check (a monitor exists in the relay only after `monitor.subscribe`, which runs after `monitor.authenticate`), so
the peer-to-peer video and the JPEG frames are behind the same gate. Logs carry a SHA-256 prefix of the uid and
the outcome, never the token or the email (`diag` also redacts `idToken` / `id_token` at any depth).

**Hearing a session (`AIRA_MONITOR_AUDIO=1`; the owner's explicit decision, on in production).** With this flag
the Live page offers a **Listen** button: the monitor sends `{t:"listen", session}` and the server relays that
one session's audio to it as it happens: the **owner's microphone** (the phone's 16 kHz chunks, so also the
customer and anyone near the phone) and **AIRA's voice** (24 kHz). The relay goes only to a monitor that asked,
only for the session it named, and stops on `{t:"listen", session: null}`, on a switch to another session, when
the monitor disconnects or when the session ends; a room camera's session is never relayed; a monitor whose
socket is backed up loses chunks (bounded queue, counted in the close log line), nothing is buffered without
limit and nothing is stored. The flag sits **behind the same access as the feed itself**: with
`AIRA_MONITOR_AUTH` unset (the default) **anyone who can open the Live page can also hear the owner's microphone
and AIRA**; the Origin check is not authentication. With `AIRA_MONITOR_AUTH=1` only a listed viewer can. With
`AIRA_MONITOR_AUDIO` unset the server ignores every listen request and tells the dashboard so (`ready` line,
`audio: false`), which hides the button.

### Owner setup for the opt-in sign-in (one time; nothing in the repo can do this for you)

Only needed with `AIRA_MONITOR_AUTH=1`. In that mode, until all of these are done the Live page **fails closed**:
the server refuses every viewer (4401 / 4403) and the dashboard says sign-in is not configured or not enabled.

1. **Enable Firebase Authentication** for the project: Firebase console → *Build* → *Authentication* → *Get
   started* → *Sign-in method* → enable the **Google** provider (pick the support email) → Save. (Identity Toolkit
   currently answers `CONFIGURATION_NOT_FOUND` for this project: this step has not been done yet.)
2. **Authorized domains** (same page → *Settings* → *Authorized domains*): add `aira-shop.web.app` (the hosting
   site; `aira-shop.firebaseapp.com` is there by default) and `localhost` for a local dashboard.
3. **List the viewers** on the `aira-live` Cloud Run service (your approval is needed for a service change):
   `AIRA_MONITOR_VIEWERS=you@gmail.com,someone@yourshop.in` (or `uid:<uid>` from *Authentication* → *Users*).
   Set `AIRA_FIREBASE_PROJECT` only if the Firebase project differs from `GCP_PROJECT` (it does not today).
   Removing an entry stops that account at its next reconnect or token refresh (within an hour at most).
4. **Dashboard build** with `VITE_MONITOR_AUTH=1`. The Live page then uses the `VITE_FIREBASE_API_KEY`,
   `VITE_FIREBASE_AUTH_DOMAIN` and `VITE_FIREBASE_PROJECT_ID` that `deploy.yml` already bakes in for Firestore.
   Without them the page shows "Live Monitor sign-in isn't configured". The sign-in opens a popup and falls back
   to a redirect (mobile Safari).

| Env var on `aira-live` | Default | Meaning |
|---|---|---|
| `AIRA_MONITOR_AUTH` | unset = **no authentication** (the owner's decision) | `1` = the live feed requires the Google sign-in and the viewer list below |
| `AIRA_MONITOR_VIEWERS` | unset = **nobody** (with `AIRA_MONITOR_AUTH=1`; ignored otherwise) | the Google accounts (emails or `uid:<uid>`) that may watch the live feed |
| `AIRA_MONITOR_AUDIO` | unset = **no audio** (listen requests ignored, no button) | `1` = a monitor that asks hears the owner's microphone and AIRA's voice, behind the same access as the feed (set to `1` in production by the owner) |
| `AIRA_FIREBASE_PROJECT` | `GCP_PROJECT` | the Firebase project whose ID tokens are accepted (`aud` / `iss`) |
| `AIRA_MONITOR_ORIGINS` (comma-separated; **replaces** the default; `:*` = any port) | `https://aira-shop.web.app,https://aira-shop.firebaseapp.com` | the browser Origin allowlist below |

### Origin allowlist: defence in depth only

`monitor.origin_allowed` still refuses an upgrade whose Origin is missing or off `AIRA_MONITOR_ORIGINS` (the socket
is accepted and closed with 4403 at once, nothing sent, before any token is read; the code rather than an HTTP 403 so the
dashboard can say "this address isn't allowed" instead of retrying forever). Localhost is **not** on the default list: for a local dashboard
set `AIRA_MONITOR_ORIGINS=https://aira-shop.web.app,https://aira-shop.firebaseapp.com,http://localhost:*,http://127.0.0.1:*`
(see `shared/.env.example`). This is a cross-site-WebSocket guard, not authentication: a tool that sets its own
`Origin` passes it, and passing it admits nobody; the token check above is the gate only with
`AIRA_MONITOR_AUTH=1` (by default there is no gate: passing the Origin check is all it takes). The shop is still bound
(`?shop=` is optional; in tokenless / demo mode only the demo shop may be named) and `MAX_MONITORS_PER_SHOP`
still caps the watchers.

### What is and is not checked (honestly)

- **Default mode (`AIRA_MONITOR_AUTH` unset): nothing identifies the viewer.** Origin, shop and the cap only.
- With `AIRA_MONITOR_AUTH=1`, **checked**: signature, issuer, audience, expiry, issued-at, auth time, a verified email for an email entry,
  and the allowlist; again on every refresh and every reconnect.
- **Not checked: revocation.** `google-auth` verifies the token itself; it does not ask Firebase whether the
  session was revoked or the user disabled or deleted (that needs `firebase-admin`'s
  `verify_id_token(check_revoked=True)`, which is not installed). A revoked or deleted account keeps watching
  until its current token expires, at most an hour, and cannot get a new one. To cut someone off at once, remove
  them from `AIRA_MONITOR_VIEWERS` (the next refresh, within the hour, is refused with 4403) and, for a lost
  device, revoke their sessions in the console.
- The phone's own `/ws` is unchanged and out of this scope: it has no user login (§1: the shared `AIRA_APP_KEY`
  as `hello.idToken`, the §2 Origin allowlist and the §3 caps).

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
- The Firestore rules (`shared/infra/firestore.rules`) are a separate guard: public **read** of the dashboard's
  collections of the demo shop only, no client writes, and the server-only collections (suppliers, proposals,
  contact and message indexes, ledger, memory, events) are closed.

## Vendor channel (Telegram)

`POST /webhooks/telegram` accepts only updates carrying the `X-Telegram-Bot-Api-Secret-Token` header that
matches `TELEGRAM_WEBHOOK_SECRET`, and refuses everything while the secret is unset. `POST /webhooks/whatsapp`
is the same, fail-closed, on `WHATSAPP_APP_SECRET` (not mounted yet: WhatsApp comes later).

Supplier ids are public (`shared/catalog`), so every vendor deep link carries an unguessable tag:
`?start=<supplier_id>.<tag>` with `tag = HMAC-SHA256(TELEGRAM_WEBHOOK_SECRET, supplier_id)[:20]`, checked with
`hmac.compare_digest`. A `/start` without the right tag is ignored, **including the first one** for a supplier.
The link is the credential: send it to the vendor privately; a vendor who changes phones opens the same link
again and the old chat is unlinked (`shared/docs/ops/telegram-setup.md` §5). There is no first-come window and no
separate re-link code.
