# Telegram setup for AIRA (the vendor channel)

Telegram is the channel AIRA uses to send supplier orders and to hear the vendors' replies. In the demo catalog
(`content/catalog/murugan_dairy.json`) the Aavin and Sakthi vendors have `channel: "telegram"`; the Arun
distributor is phoned (`channel: "call"`). WhatsApp Cloud API stays in the code for later
(`docs/ops/whatsapp-setup.md`).

It takes about 15 minutes. Nothing here needs Meta approval.

**What you end up with**

| Env var on `aira-live` | Comes from | Stored as |
|---|---|---|
| `TELEGRAM_TOKEN` | BotFather (step 1) | secret `aira-telegram-token` |
| `TELEGRAM_WEBHOOK_SECRET` | already created and attached | secret `aira-telegram-webhook-secret` |

**Rules**

- Never commit the token, a chat id, a phone number or the project id. Use `.env` locally (git-ignored) and
  Secret Manager in the cloud.
- Never paste a secret into a command line, a chat or a doc. The commands below read secrets from stdin.
- `TELEGRAM_TOKEN` is **not** in `.github/workflows/deploy.yml` or `infra/deploy_live.sh`. Attach it on the
  service once (step 3); both deploys use `--update-secrets`, so later deploys keep it. Add it to `deploy.yml`
  only after the secret exists, or the deploy fails.

**Before you start**

- `gcloud` logged in as a project owner.
- The `aira-live` service is deployed and public (`bash infra/deploy_live.sh` or a push to `main`).
- Each demo vendor has Telegram on their phone. Teammates' phones are fine.

```bash
export PROJECT_ID=<PROJECT_ID>   # your Google Cloud project id. Never commit it.
```

---

## 1. Create the bot with BotFather

1. In Telegram, open **@BotFather** and send `/newbot`.
2. Name: `AIRA Murugan Dairy`. Username: anything ending in `bot`, for example `AiraMuruganDairyBot`.
3. BotFather replies with the bot token (`123456789:AA...`). Keep it on the clipboard only until step 2.
4. Optional: `/setdescription` → "Orders from Murugan Dairy. Press Start to receive them here."

## 2. Store the token in Secret Manager

The value is read from a hidden prompt and piped in on stdin. Paste it, press Enter; nothing is echoed.

```bash
read -rs VALUE && printf '%s' "$VALUE" | gcloud secrets create aira-telegram-token \
  --replication-policy=automatic --data-file=- --project "$PROJECT_ID"; unset VALUE
```

If the secret already exists, add a version instead:

```bash
read -rs VALUE && printf '%s' "$VALUE" | gcloud secrets versions add aira-telegram-token \
  --data-file=- --project "$PROJECT_ID"; unset VALUE
```

Let the runtime service account read it:

```bash
gcloud secrets add-iam-policy-binding aira-telegram-token \
  --member="serviceAccount:aira-live@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor --project "$PROJECT_ID"
```

`aira-telegram-webhook-secret` already exists and is attached to the service as `TELEGRAM_WEBHOOK_SECRET`
(see `infra/deploy_live.sh`). The webhook refuses every update until that secret is set.

## 3. Attach the token to `aira-live`

```bash
gcloud run services update aira-live --region asia-south1 --project "$PROJECT_ID" \
  --update-secrets TELEGRAM_TOKEN=aira-telegram-token:latest
```

Check it took: `GET /health` still answers `{"ok":true,...}`, and the new revision lists `TELEGRAM_TOKEN` under
**Variables & Secrets** in the console.

## 4. Point the bot at AIRA and print the vendor links

`scripts/telegram_setup.py` calls `setWebhook` (URL `<aira-live>/webhooks/telegram`, the secret header,
`allowed_updates=["message"]`), then `getMe`, and prints one deep link per Telegram supplier in the catalog.
It never prints the token.

```bash
URL="$(gcloud run services describe aira-live --region asia-south1 --project "$PROJECT_ID" --format='value(status.url)')"
TELEGRAM_TOKEN="$(gcloud secrets versions access latest --secret aira-telegram-token --project "$PROJECT_ID")" \
TELEGRAM_WEBHOOK_SECRET="$(gcloud secrets versions access latest --secret aira-telegram-webhook-secret --project "$PROJECT_ID")" \
python3 scripts/telegram_setup.py "$URL"
```

Expected output:

```
Webhook set: https://<aira-live host>/webhooks/telegram (allowed_updates: message, secret header on)
Bot: @AiraMuruganDairyBot
Send each vendor their link. They open it and press Start; AIRA then messages them orders there:
  Aavin vendor (aavin_vendor): https://t.me/AiraMuruganDairyBot?start=aavin_vendor.3f9a1c2b7d4e
  Sakthi curd vendor (sakthi_vendor): https://t.me/AiraMuruganDairyBot?start=sakthi_vendor.8c2d0e5f6a1b
```

Run it again whenever the service URL changes. Setting the webhook turns `getUpdates` off, which is fine:
AIRA only uses the webhook.

## 5. Onboard each vendor (deep link)

1. Send each vendor **their own** link from the output above, by any channel (SMS, WhatsApp, in person).
2. The vendor opens it and presses **Start**. Telegram sends `/start <supplier_id>.<tag>` to the webhook. The tag
   is an HMAC of the supplier id under `TELEGRAM_WEBHOOK_SECRET`, so a bare `/start <supplier_id>` links nobody
   (the supplier ids are in the public catalog).
3. `aira-live` checks that `<supplier_id>` is a supplier of the demo shop, stores the sender's chat id as
   `telegramChatId` on `shops/murugan_dairy/suppliers/<supplier_id>` (database `aira`), and replies
   "AIRA: you will receive Murugan Dairy orders here." Unknown ids are ignored, silently.
4. A vendor who changes phones just opens the same link again: the same supplier link may overwrite its own
   chat id. No other link can.

Check in the Firestore console that the supplier doc now has `telegramChatId`. Never copy that id anywhere.

## 6. Smoke test

1. In the AIRA app say "Order 30 litres Sakthi curd" and answer "yes".
2. The vendor phone receives the order from the bot.
3. On the vendor phone, reply (a plain message is matched to that vendor's latest open order; a swipe-to-reply
   on the order message matches it directly).
4. AIRA says "Sakthi curd vendor replied: ...".

If the vendor has not pressed Start yet, or the token is not attached, AIRA says
"<vendor> is not on the AIRA Telegram bot yet", then opens the pre-typed click-to-chat message instead and
asks whether you sent it. Nothing fails silently; the order stays `DRAFT` until you say it was sent.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `setWebhook failed: ... HTTPS url must be provided` | Pass the `https://` service URL, not the host. |
| Vendor presses Start and gets no reply | `TELEGRAM_TOKEN` is not attached (step 3), or the webhook secret on the service differs from the one `telegram_setup.py` sent. Run step 4 again. |
| Webhook logs show `403` | The `X-Telegram-Bot-Api-Secret-Token` header does not match `TELEGRAM_WEBHOOK_SECRET`, or the secret is unset. Run step 4 again after fixing the service. |
| Order falls back to click-to-chat | Listen to AIRA: "not on the AIRA Telegram bot yet" means the vendor has not opened their link. Check `telegramChatId` on the supplier doc. |
| Replies never reach the owner | The reply must come from the chat that pressed Start. Check the Cloud Run logs for `POST /webhooks/telegram` with status `200`. |

Sources: Telegram Bot API [setWebhook](https://core.telegram.org/bots/api#setwebhook),
[deep linking](https://core.telegram.org/bots/features#deep-linking),
[sendMessage](https://core.telegram.org/bots/api#sendmessage). Checked on 2026-10-11.
