# Security note: tokenless mode on a public URL

AIRA has **no login** in this build (build overrides §1: no Firebase Auth). The `aira-live` service is deployed
`--allow-unauthenticated`, and `AIRA_ALLOW_NO_TOKEN=1` makes every `hello` land in the demo shop as its owner.
That is right for a one-user demo, and it means anyone who finds the Cloud Run URL could open the WebSocket.
Two small guards in `services/live/aira_live/main.py` keep that cheap and bounded without adding a login step.

## 1. Browser Origin allowlist (`/ws` and the HTTP API)

A browser always sends the `Origin` header on a WebSocket upgrade and on cross-site `fetch`. `aira-live` refuses
an upgrade whose Origin is not on the list (close code 4403, before the handshake completes), and the same list
is the CORS allowlist of `GET /api/forecast`.

| Env var on `aira-live` | Default |
|---|---|
| `AIRA_ALLOWED_ORIGINS` (comma-separated; **replaces** the default) | `https://aira-shop.web.app,https://aira-shop.firebaseapp.com,http://localhost:5173,http://localhost:4173` |

So another web page cannot embed or script the demo shop. A request **without** an Origin (curl, a script) is
not a web page: nothing in it can be checked, and it goes on to the hello check like any other client. That is
why the second guard exists.

The phone app is a Trusted Web Activity (Chrome) on the hosting site, so its Origin is `https://aira-shop.web.app`
and the owner never notices the check. For a local run, the Vite dev server (`:5173`) and preview (`:4173`)
are already allowed.

## 2. Session caps

`hello` is refused with `error/permission` ("AIRA is busy with other phones") when either cap is reached:

| Constant (`main.py`) | Value | Why |
|---|---|---|
| `MAX_SESSIONS_PER_IP` | 3 | one address (first `X-Forwarded-For` entry, set by Cloud Run's frontend) cannot take the whole service |
| `MAX_SESSIONS` | 6 | the service runs on one instance; this bounds what Gemini Live can cost in total |

A phone that reconnects with `hello.resume` replaces its own session, so it never counts twice. The counts are
in-process (`aira_live.sessions`), which is enough because `--max-instances 1`.

## What this is not

- It is not authentication. Anything that commits stock, money or state still goes through `confirm.propose`
  and the owner's spoken "yes", and the model never writes directly.
- A non-browser client that knows the URL can still open a session until it hits the caps. If that ever matters
  (a public pilot, several shops), the next step is a real login (Firebase Auth) and `AIRA_ALLOW_NO_TOKEN=0`; the
  `hello.idToken` field is already in the contract for it.
- The Firestore rules (`infra/firestore.rules`) are a separate guard: public **read** of the dashboard's
  collections of the demo shop only, no client writes, and the server-only collections (suppliers, proposals,
  contact and message indexes, ledger, memory, events) are closed.

## Vendor channel (Telegram)

`POST /webhooks/telegram` accepts only updates carrying the `X-Telegram-Bot-Api-Secret-Token` header that
matches `TELEGRAM_WEBHOOK_SECRET`, and refuses everything while the secret is unset. `POST /webhooks/whatsapp`
is the same, fail-closed, on `WHATSAPP_APP_SECRET` (not mounted yet: WhatsApp comes later).

Supplier ids are public (`content/catalog`), so a vendor's chat is linked **first come**: once a chat has pressed
Start for a supplier, another chat cannot take it over. Moving a vendor to a new phone needs the one-time code
`AIRA_TELEGRAM_LINK_CODE` set on the service and the link form `/start <supplier_id>-<code>`
(`docs/ops/telegram-setup.md` §5).
