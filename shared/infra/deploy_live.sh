#!/usr/bin/env bash
# Deploy the aira-live Cloud Run service from source (backend), then smoke-check it.
#
# Uses the same flags as .github/workflows/deploy.yml plus --allow-unauthenticated: the phone
# app and the dashboard connect straight from the browser, so the service must be public.
# --update-* (not --set-*) keeps env vars and secrets added on the service later.
# Run it by hand once after shared/infra/bootstrap.sh. After that every push to main redeploys through
# GitHub Actions, and the public-access binding carries over to new revisions.
#
# Least Cloud Run configuration (owner request 2026-10-11): --min-instances 0 --max-instances 1
# --cpu 1 --memory 512Mi --no-cpu-boost --cpu-throttling. No warm instance: the first connect after
# an idle period pays a cold start. --max-instances 1 stays because the live-session registry
# (sessions.notify) is in-process; the smoke check fails if maxScale is not 1. If the real app
# OOMs at 512Mi after a deploy, raise --memory to 1Gi only.
#
# Usage: GCP_PROJECT=<project-id> bash shared/infra/deploy_live.sh   (GCP_PROJECT may come from .env)
# Needs: gcloud, curl, python3, and shared/infra/bootstrap.sh already run (service account aira-live,
#        secret aira-gemini-api-key with at least one version).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REGION="asia-south1"
SERVICE="aira-live"

# Print KEY's value from the repo's .env (empty when the file or key is absent).
dotenv_get() {
  local file="$ROOT/.env" line
  [ -f "$file" ] || return 0
  line="$(grep -E "^$1=" "$file" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%\"}"
  line="${line#\"}"
  printf '%s' "$line"
}

GCP_PROJECT="${GCP_PROJECT:-$(dotenv_get GCP_PROJECT)}"
if [ -z "$GCP_PROJECT" ]; then
  echo "error: set GCP_PROJECT (environment or .env) to the Google Cloud project id" >&2
  exit 1
fi

cd "$ROOT"
gcloud run deploy "$SERVICE" \
  --source backend \
  --region "$REGION" \
  --project "$GCP_PROJECT" \
  --service-account "aira-live@${GCP_PROJECT}.iam.gserviceaccount.com" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 1 \
  --cpu 1 \
  --memory 512Mi \
  --no-cpu-boost \
  --cpu-throttling \
  --timeout 3600 \
  --session-affinity \
  --update-secrets GEMINI_API_KEY=aira-gemini-api-key:latest,AIRA_JOB_TOKEN=aira-job-token:latest,WHATSAPP_VERIFY_TOKEN=aira-whatsapp-verify-token:latest,TELEGRAM_WEBHOOK_SECRET=aira-telegram-webhook-secret:latest,AIRA_APP_KEY=aira-app-key:latest \
  --update-env-vars "FIRESTORE_DATABASE=aira,AIRA_DEMO_SHOP=sunrise_dairy,AIRA_ALLOW_NO_TOKEN=1,AIRA_MOCK=0,GCP_PROJECT=${GCP_PROJECT}" \
  --quiet

DESCRIBE_JSON="$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$GCP_PROJECT" --format=json)"

# Print one field of the service description: "url" or "maxScale".
service_field() {
  printf '%s' "$DESCRIBE_JSON" | python3 -c '
import json, sys
svc = json.load(sys.stdin)
if sys.argv[1] == "url":
    print(svc.get("status", {}).get("url", ""))
else:
    key = "autoscaling.knative.dev/maxScale"
    template = svc.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {})
    service = svc.get("metadata", {}).get("annotations", {})
    print(template.get(key) or service.get(key) or "")
' "$1"
}

URL="$(service_field url)"
MAX_SCALE="$(service_field maxScale)"
if [ -z "$URL" ]; then
  echo "error: $SERVICE has no URL" >&2
  exit 1
fi
if [ "$MAX_SCALE" != "1" ]; then
  echo "error: $SERVICE maxScale is '$MAX_SCALE', expected 1 (in-process session registry)" >&2
  exit 1
fi

# Cloud Run reserves some paths ending in "z", so the health route is /health.
echo "==> health"
curl -fsS "$URL/health"
echo
echo "aira-live: $URL"
echo "WebSocket: wss://${URL#https://}/ws"
