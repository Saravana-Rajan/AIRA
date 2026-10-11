# WhatsApp setup for AIRA (Meta Cloud API test number)

Telegram is the vendor channel for now (`shared/docs/ops/telegram-setup.md`); WhatsApp Cloud API stays in the code for later. This guide gets the credentials AIRA needs to send orders on WhatsApp and to hear the replies. It takes about 30 minutes, plus up to 24 hours for Meta to approve the order template.

**What you end up with**

| Env var on `aira-live` | Comes from | Stored as |
|---|---|---|
| `WHATSAPP_TOKEN` | System User token (step 3) | secret `aira-whatsapp-token` |
| `WHATSAPP_APP_SECRET` | App settings → Basic (step 4) | secret `aira-whatsapp-app-secret` |
| `WHATSAPP_VERIFY_TOKEN` | already created | secret `aira-whatsapp-verify-token` |
| `WHATSAPP_PHONE_ID` | API Setup page (step 1) | plain env var |
| `WHATSAPP_TEMPLATE` | optional, default `order_request` | plain env var |
| `WHATSAPP_GRAPH_VERSION` | optional, default `v21.0` | plain env var |
| `SUPPLIER_AAVIN_PHONE`, `SUPPLIER_SAKTHI_PHONE`, `SUPPLIER_ARUN_PHONE` | the demo vendors' phones, E.164 | plain env vars |
| `TELEGRAM_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | optional (step 9) | secrets `aira-telegram-token`, `aira-telegram-webhook-secret` |

**Rules**

- Never commit a token, a phone number or the project id. Use `.env` locally (it is gitignored) and Secret Manager in the cloud.
- Never paste a secret into a command line, a chat or a doc. The commands below read secrets from stdin.

**Before you start**

- A Facebook account, with access to a Meta business portfolio (you can create one during step 1).
- `gcloud` logged in as a project owner.
- The `aira-live` service is deployed and public (`bash shared/infra/deploy_live.sh`).
- For each demo vendor, a phone with WhatsApp installed. Teammates' phones are fine.

Set the project once in your terminal. Every command below uses it.

```bash
export PROJECT_ID=<PROJECT_ID>   # your Google Cloud project id. Never commit it.
```

---

## 1. Create the Meta app and get the test number

1. Go to <https://developers.facebook.com/apps> and click **Create app**.
2. Pick the use case **Connect with customers through WhatsApp**. On older dashboards, pick the app type **Business**, then add the **WhatsApp** product.
3. Name the app `AIRA`. Pick your business portfolio, or create one when asked.
4. Open **WhatsApp → API Setup**. On the use-case dashboard it is under **Use cases → Customize**.
5. Meta has created a free **test phone number** for you. It is shown in the **From** field.
6. Copy these two IDs from the same page into a private note:
   - **Phone number ID**. This becomes `WHATSAPP_PHONE_ID`.
   - **WhatsApp Business Account ID** (WABA ID). You need it only for troubleshooting.

The Phone number ID is a long number. It is not the phone number itself.

## 2. Add the vendors as test recipients

A test number can only message numbers you have verified. You can add up to 5.

1. On **API Setup**, open the **To** field and choose **Manage phone number list**.
2. Add each vendor phone in full international form, for example `+91XXXXXXXXXX`.
3. WhatsApp sends a code to that phone. Type the code in to verify the number.
4. Repeat for every demo vendor:
   - the Aavin milk vendor (`SUPPLIER_AAVIN_PHONE`)
   - the Sakthi curd vendor (`SUPPLIER_SAKTHI_PHONE`)
   - the Arun ice cream distributor (`SUPPLIER_ARUN_PHONE`)
   - your own phone, for testing

The demo catalog sets the Arun distributor's channel to `call`, so AIRA phones Arun instead of messaging him. Registering his number still does no harm.

## 3. Create a permanent access token (System User)

**Why the temporary token is not enough.** The **Generate access token** button on API Setup gives a token that expires within about a day. The demo runs over many days: building, recording and judging. When that token expires, every send fails with HTTP 401. AIRA then quietly falls back to click-to-chat, and you would have to replace the secret every day. A System User token can be set never to expire.

1. Open **Business Settings** at <https://business.facebook.com/settings> and pick the same business portfolio.
2. Go to **Users → System users → Add**. Name the user `aira-live` and give it the **Admin** role.
3. Select the new user and click **Assign assets**:
   - **Apps → AIRA**: turn on full control (Manage app).
   - **WhatsApp accounts → your test WhatsApp account**: turn on full control.
4. Click **Generate new token**, then:
   - Pick the **AIRA** app.
   - Set **Token expiration** to **Never**.
   - Tick these permissions: `whatsapp_business_messaging`, `whatsapp_business_management` and `business_management`. Meta's guide lists all three.
5. Copy the token. Keep it on your clipboard only until step 6, where you store it.

## 4. Find the App Secret

The App Secret lets AIRA check that webhook calls really come from Meta. It is used for the `X-Hub-Signature-256` check.

1. In the app dashboard, open **App settings → Basic**.
2. Next to **App secret**, click **Show** and enter your password.
3. Copy it for step 6.

## 5. Message templates

WhatsApp only lets a business start a chat with an approved **template**. AIRA uses two:

| Template | Use | Status |
|---|---|---|
| `hello_world` | first smoke test (language `en_US`, no parameters) | already approved on the test account |
| `order_request` | real orders (the default in the code) | you must create it |

Create `order_request`:

1. Open **WhatsApp Manager → Account tools → Message templates → Create template**.
2. Fill it in exactly as below. The code depends on the name, the language and the two parameters.
   - **Category:** Utility
   - **Name:** `order_request`
   - **Language:** English (code `en`). Do not pick "English (US)": the code sends `en`.
   - **Body:** `New order from {{1}}: {{2}}. Please confirm delivery time.`
   - **Sample for {{1}}:** `Sunrise Dairy`
   - **Sample for {{2}}:** `30 L Sakthi Curd 1 L, 25 pack Aavin Paneer 200 g`
3. Submit it. Review can take up to 24 hours.

Until `order_request` is approved, choose one of these:

- Set `WHATSAPP_TEMPLATE=hello_world` (step 6). AIRA's sends will work, but vendors receive "Hello World" instead of the order.
- Or set `whatsapp_enabled` to `false` in the Firestore doc `config/runtime` (database `aira`). AIRA then uses the click-to-chat link (step 9).

## 6. Store the secrets and attach them to `aira-live`

Do this before the webhook step. Meta checks the webhook straight away, so the service must already know the verify token.

**6a. Create the secrets.** Each value is read from a hidden prompt and piped in on stdin. Paste the value, then press Enter. Nothing is echoed to the screen.

```bash
read -rs VALUE && printf '%s' "$VALUE" | gcloud secrets create aira-whatsapp-token \
  --replication-policy=automatic --data-file=- --project "$PROJECT_ID"; unset VALUE

read -rs VALUE && printf '%s' "$VALUE" | gcloud secrets create aira-whatsapp-app-secret \
  --replication-policy=automatic --data-file=- --project "$PROJECT_ID"; unset VALUE
```

If a secret already exists, add a new version instead:

```bash
read -rs VALUE && printf '%s' "$VALUE" | gcloud secrets versions add aira-whatsapp-token \
  --data-file=- --project "$PROJECT_ID"; unset VALUE
```

`aira-whatsapp-verify-token` already exists. Read its value (you need it in step 7) with:

```bash
gcloud secrets versions access latest --secret aira-whatsapp-verify-token --project "$PROJECT_ID"
```

**6b. Let the runtime service account read them.** The service account is `aira-live@<PROJECT_ID>.iam.gserviceaccount.com`.

```bash
for s in aira-whatsapp-token aira-whatsapp-app-secret aira-whatsapp-verify-token; do
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:aira-live@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor --project "$PROJECT_ID"
done
```

**6c. Attach them to the service.** Replace the `<...>` placeholders with the real values in your terminal only. Drop `WHATSAPP_TEMPLATE=hello_world` once `order_request` is approved.

```bash
gcloud run services update aira-live --region asia-south1 --project "$PROJECT_ID" \
  --update-secrets WHATSAPP_TOKEN=aira-whatsapp-token:latest,WHATSAPP_APP_SECRET=aira-whatsapp-app-secret:latest,WHATSAPP_VERIFY_TOKEN=aira-whatsapp-verify-token:latest \
  --update-env-vars WHATSAPP_PHONE_ID=<PHONE_NUMBER_ID>,WHATSAPP_TEMPLATE=hello_world,SUPPLIER_AAVIN_PHONE=<+91XXXXXXXXXX>,SUPPLIER_SAKTHI_PHONE=<+91XXXXXXXXXX>,SUPPLIER_ARUN_PHONE=<+91XXXXXXXXXX>
```

When `order_request` is approved, switch back to the default template:

```bash
gcloud run services update aira-live --region asia-south1 --project "$PROJECT_ID" \
  --remove-env-vars WHATSAPP_TEMPLATE
```

**Do redeploys keep these?** Yes. Both `.github/workflows/deploy.yml` and `shared/infra/deploy_live.sh` use `--update-secrets` and `--update-env-vars`, which add to what is already on the service, and a test in `backend/tests/infra` fails if anyone switches them to `--set-*` (which would replace everything).

**Rotating a secret later.** Add a new version (6a), then start a new revision so the running instance picks it up:

```bash
gcloud run services update aira-live --region asia-south1 --project "$PROJECT_ID" \
  --update-env-vars AIRA_SECRET_ROLL="$(date +%s)"
```

**How AIRA finds the vendor numbers.** Orders go to the `phone` field of each supplier doc in Firestore (database `aira`, `shops/sunrise_dairy/suppliers/<id>`). `shared/scripts/seed_firestore.py` fills that field from the env var named by the catalog's `phoneEnv`: `SUPPLIER_AAVIN_PHONE`, `SUPPLIER_SAKTHI_PHONE` and `SUPPLIER_ARUN_PHONE`, the same names as above and in `.env.example`. If the shop is already seeded, type each number into the supplier doc's `phone` field in the Firestore console instead of re-seeding, because re-seeding resets the stock.

## 7. Connect the webhook (vendor replies)

AIRA receives vendor replies at **`/webhooks/whatsapp`**:

- `GET` handles Meta's verify check.
- `POST` receives the messages.
- Telegram, if you use it, posts to `/webhooks/telegram`.

1. Get the service URL:

   ```bash
   URL="$(gcloud run services describe aira-live --region asia-south1 --project "$PROJECT_ID" --format='value(status.url)')"
   echo "$URL/webhooks/whatsapp"
   ```

2. Check that the route is live. It should print the number `12345`:

   ```bash
   VERIFY="$(gcloud secrets versions access latest --secret aira-whatsapp-verify-token --project "$PROJECT_ID")"
   curl -sS "$URL/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=$VERIFY&hub.challenge=12345"; echo
   ```

   - **`404`:** the webhook router is not deployed yet. `messaging/webhooks.py` must be merged and mounted in `main.py`.
   - **`403`:** the service does not have `WHATSAPP_VERIFY_TOKEN` (go back to step 6c).

3. In the Meta app dashboard, open **WhatsApp → Configuration**. On the use-case dashboard it is under **Use cases → Customize → Configuration**.
4. Under **Webhook**, click **Edit** and enter:
   - **Callback URL:** the `.../webhooks/whatsapp` URL printed above.
   - **Verify token:** the value of `aira-whatsapp-verify-token`.
5. Click **Verify and save**.
6. Under **Webhook fields**, find **messages** and click **Subscribe**.

## 8. Smoke test

**8a. Send `hello_world` to your own phone.** Use a phone you added in step 2.

```bash
WHATSAPP_TOKEN="$(gcloud secrets versions access latest --secret aira-whatsapp-token --project "$PROJECT_ID")"
curl -sS -X POST "https://graph.facebook.com/v21.0/<PHONE_NUMBER_ID>/messages" \
  -H "Authorization: Bearer $WHATSAPP_TOKEN" -H "Content-Type: application/json" \
  -d '{"messaging_product":"whatsapp","to":"91XXXXXXXXXX","type":"template","template":{"name":"hello_world","language":{"code":"en_US"}}}'
unset WHATSAPP_TOKEN
```

The response should contain `"id":"wamid...."`, and the "Hello World" message should arrive on the phone. `v21.0` is the code's default (`WHATSAPP_GRAPH_VERSION`). Meta's changelog lists it as supported until 21 January 2027. Meta's own examples now use newer versions, and those work too.

**8b. Reply from the phone.** Send something like "OK, 6 pm", then check that the webhook received it:

```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="aira-live" AND httpRequest.requestUrl:"/webhooks/whatsapp"' \
  --project "$PROJECT_ID" --freshness 10m --limit 10 \
  --format 'value(timestamp,httpRequest.requestMethod,httpRequest.status)'
```

You should see a `POST` with status `200`. A `403` means the App Secret does not match this app.

A reply to a test message is not linked to any order, so AIRA says nothing. That is expected.

**8c. Full loop.**

1. Say "Order 30 litres Sakthi curd" and answer "yes".
2. On the vendor phone, **swipe right on the order message to reply to it**. A quoted reply matches the order directly. A plain message is matched to that vendor's latest open order.
3. AIRA should say "Sakthi curd vendor replied: ...".

## 9. Free fallbacks

**Click-to-chat (`wa.me`) needs no setup.** AIRA opens `https://wa.me/<number>?text=<order>` with the order already typed in. It does this when:

- the WhatsApp settings are missing,
- a send fails, or
- `whatsapp_enabled` is `false`.

The owner double-taps **Send**, then tells AIRA the order was sent. The order stays `DRAFT` until then. The vendor's reply lands in the owner's own WhatsApp, so AIRA does not speak it.

**Telegram bot: the vendor channel for now.** Full steps, including the `/start` deep links that link each vendor automatically, are in `shared/docs/ops/telegram-setup.md`. The short form:

1. In Telegram, message **@BotFather**, send `/newbot`, and pick a name and a username ending in `bot`. BotFather replies with the token.
2. Store the token, plus a random webhook secret:

   ```bash
   read -rs VALUE && printf '%s' "$VALUE" | gcloud secrets create aira-telegram-token \
     --replication-policy=automatic --data-file=- --project "$PROJECT_ID"; unset VALUE
   openssl rand -hex 32 | tr -d '\n' | gcloud secrets create aira-telegram-webhook-secret \
     --replication-policy=automatic --data-file=- --project "$PROJECT_ID"
   for s in aira-telegram-token aira-telegram-webhook-secret; do
     gcloud secrets add-iam-policy-binding "$s" \
       --member="serviceAccount:aira-live@${PROJECT_ID}.iam.gserviceaccount.com" \
       --role=roles/secretmanager.secretAccessor --project "$PROJECT_ID"
   done
   gcloud run services update aira-live --region asia-south1 --project "$PROJECT_ID" \
     --update-secrets TELEGRAM_TOKEN=aira-telegram-token:latest,TELEGRAM_WEBHOOK_SECRET=aira-telegram-webhook-secret:latest
   ```

3. Have the vendor open the bot and press **Start**. Then read their chat id. Do this **before** setting the webhook, because `getUpdates` stops working once a webhook is set.

   ```bash
   TG="$(gcloud secrets versions access latest --secret aira-telegram-token --project "$PROJECT_ID")"
   curl -sS "https://api.telegram.org/bot$TG/getUpdates" | python3 -m json.tool | grep -A2 '"chat"'
   ```

4. Point the bot at AIRA:

   ```bash
   TGS="$(gcloud secrets versions access latest --secret aira-telegram-webhook-secret --project "$PROJECT_ID")"
   curl -sS "https://api.telegram.org/bot$TG/setWebhook" \
     -d url="$URL/webhooks/telegram" -d secret_token="$TGS" -d 'allowed_updates=["message"]'
   unset TG TGS
   ```

5. In Firestore, on that vendor's supplier doc, set `channel` to `telegram` and `telegramChatId` to the chat id.

## 10. Costs (short)

- Since 1 July 2025, Meta charges **per delivered template message**. There is no charge per conversation.
- **Vendor replies are free.** So are AIRA's messages sent while the vendor's 24-hour reply window is open, including utility templates.
- A utility template sent outside that window is charged at the India utility rate. See the rate card: <https://business.whatsapp.com/products/platform-pricing#rates>.
- **The test number** can only message the 5 verified numbers. Meta's pricing page does not list a charge for it. For demo volumes the cost is close to zero either way. Check **WhatsApp Manager → Billing** after the smoke test if you need to be sure.
- Telegram and click-to-chat are free.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Send returns `401` / "Session has expired" | You used the temporary token. Make the System User token (step 3) and add it as a new secret version. |
| Send returns `131030` "Recipient phone number not in allowed list" | Add and verify the number (step 2). |
| Send returns `132001` "Template name does not exist in the translation" | `order_request` is not approved yet, or its language is not `en`. Use `WHATSAPP_TEMPLATE=hello_world` for now. |
| **Verify and save** fails | Run the check in step 7.2. The service needs `WHATSAPP_VERIFY_TOKEN` and the deployed router. |
| Replies never reach the webhook | Make sure **messages** is subscribed (step 7.6). Then subscribe the app to the account: `curl -sS -X POST "https://graph.facebook.com/v21.0/<WABA_ID>/subscribed_apps" -H "Authorization: Bearer $WHATSAPP_TOKEN"`. If it still fails, switch the app to **Live** mode (top bar; it needs a privacy policy URL in **App settings → Basic**). Meta warns that some webhooks are not sent in Dev mode. |
| WhatsApp settings gone after a push to `main` | The deploy used `--set-*`. See the note in step 6c, then rerun 6c. |

Sources: Meta's [Cloud API get-started guide](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started), [system user tokens](https://developers.facebook.com/docs/whatsapp/business-management-api/get-started), [webhooks](https://developers.facebook.com/docs/whatsapp/cloud-api/guides/set-up-webhooks), [message templates](https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates), [pricing](https://developers.facebook.com/docs/whatsapp/pricing), [Graph API versions](https://developers.facebook.com/docs/graph-api/changelog/versions), [Telegram setWebhook](https://core.telegram.org/bots/api#setwebhook). Checked on 2026-10-10. Meta renames dashboard menus often, so follow the closest match.
