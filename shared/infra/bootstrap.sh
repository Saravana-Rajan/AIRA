#!/usr/bin/env bash
# AIRA: one-time Google Cloud + Firebase bootstrap. Idempotent: safe to re-run.
#
# What it sets up (region asia-south1, every resource named aira-*):
#   1. Enables only the APIs AIRA uses.
#   2. Firestore Native database "aira". The "(default)" database is never touched.
#   3. Runtime service account aira-live, with Firestore read/write (roles/datastore.user).
#   4. Secret Manager secret aira-gemini-api-key, readable by aira-live. A new version is
#      added only when $GEMINI_API_KEY is set and differs from the latest version.
#   5. Lets the Cloud Build service account build Cloud Run source deploys (roles/run.builder).
#   6. Adds Firebase to the project, creates the Hosting site aira-shop and deploys the
#      Firestore rules + indexes (shared/infra/firestore.rules, shared/infra/firestore.indexes.json) to "aira".
#   7. Optional, SEED=1: seeds the demo shop sunrise_dairy with shared/scripts/seed_firestore.py.
#      Off by default because re-seeding resets the demo stock.
#
# Deliberately NOT set up: Pub/Sub, BigQuery, Cloud Scheduler, Remote Config, Firebase Auth, FCM.
#
# Usage (from anywhere; GCP_PROJECT and GEMINI_API_KEY may also come from the repo's .env):
#   GCP_PROJECT=<project-id> GEMINI_API_KEY=<key> bash shared/infra/bootstrap.sh
#   SEED=1 GCP_PROJECT=<project-id> bash shared/infra/bootstrap.sh
#
# Needs: gcloud logged in as a project owner (gcloud auth login), billing enabled,
#        Node 20+ for npx firebase-tools (firebase-tools login, or gcloud auth application-default login),
#        uv only when SEED=1.
# Next:  bash shared/infra/deploy_live.sh          first deploy of aira-live (makes it public)
#        bash shared/infra/setup_github_deploy.sh  keyless GitHub Actions deploys on push to main
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REGION="asia-south1"
DATABASE="aira"
SECRET="aira-gemini-api-key"
SITE="aira-shop"

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
GEMINI_API_KEY="${GEMINI_API_KEY:-$(dotenv_get GEMINI_API_KEY)}"

for tool in gcloud npx; do
  command -v "$tool" >/dev/null 2>&1 || { echo "error: $tool is not installed" >&2; exit 1; }
done

RUNTIME_SA="aira-live@${GCP_PROJECT}.iam.gserviceaccount.com"

echo "==> [1/7] Enabling APIs on $GCP_PROJECT"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  firebase.googleapis.com \
  firebasehosting.googleapis.com \
  firebaserules.googleapis.com \
  generativelanguage.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project "$GCP_PROJECT"

echo "==> [2/7] Firestore database '$DATABASE' ($REGION)"
if gcloud firestore databases describe --database="$DATABASE" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  echo "    exists"
else
  gcloud firestore databases create --database="$DATABASE" --location="$REGION" \
    --type=firestore-native --project "$GCP_PROJECT"
fi

echo "==> [3/7] Runtime service account aira-live"
if gcloud iam service-accounts describe "$RUNTIME_SA" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  echo "    exists"
else
  gcloud iam service-accounts create aira-live \
    --display-name="AIRA live (Cloud Run runtime)" --project "$GCP_PROJECT"
fi
gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/datastore.user --condition=None >/dev/null

echo "==> [4/7] Secret $SECRET"
if gcloud secrets describe "$SECRET" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  echo "    exists"
else
  gcloud secrets create "$SECRET" --replication-policy=automatic --project "$GCP_PROJECT"
fi
if [ -n "$GEMINI_API_KEY" ]; then
  current="$(gcloud secrets versions access latest --secret="$SECRET" --project "$GCP_PROJECT" 2>/dev/null || true)"
  if [ "$current" = "$GEMINI_API_KEY" ]; then
    echo "    latest version already holds this key"
  else
    printf '%s' "$GEMINI_API_KEY" |
      gcloud secrets versions add "$SECRET" --data-file=- --project "$GCP_PROJECT" >/dev/null
    echo "    added a new version"
  fi
else
  echo "    GEMINI_API_KEY not set: no version added. Cloud Run needs one before the first deploy:"
  echo "    printf '%s' \"\$KEY\" | gcloud secrets versions add $SECRET --data-file=- --project \"\$GCP_PROJECT\""
fi
gcloud secrets add-iam-policy-binding "$SECRET" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor \
  --project "$GCP_PROJECT" >/dev/null

echo "==> [5/7] Cloud Build service account for source deploys"
PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')"
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
if gcloud iam service-accounts describe "$BUILD_SA" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
    --member="serviceAccount:$BUILD_SA" --role=roles/run.builder --condition=None >/dev/null
  echo "    $BUILD_SA has roles/run.builder"
else
  echo "    no compute default service account: Cloud Build uses its legacy service account, nothing to grant"
fi

echo "==> [6/7] Firebase project, Hosting site '$SITE', Firestore rules + indexes"
npx --yes firebase-tools projects:addfirebase "$GCP_PROJECT" >/dev/null 2>&1 ||
  echo "    already a Firebase project"
if npx --yes firebase-tools hosting:sites:get "$SITE" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  echo "    site $SITE exists"
else
  npx --yes firebase-tools hosting:sites:create "$SITE" --project "$GCP_PROJECT"
fi
if [ -f "$ROOT/shared/infra/firestore.rules" ] && [ -f "$ROOT/shared/infra/firestore.indexes.json" ]; then
  (cd "$ROOT" && npx --yes firebase-tools deploy --only firestore --project "$GCP_PROJECT" --non-interactive)
else
  echo "    shared/infra/firestore.rules or shared/infra/firestore.indexes.json missing: skipped the rules deploy"
fi

echo "==> [7/7] Demo data"
if [ "${SEED:-0}" = "1" ]; then
  export GCP_PROJECT
  FIRESTORE_DATABASE="$DATABASE" \
    uv run --project "$ROOT/backend" python "$ROOT/shared/scripts/seed_firestore.py" --project "$GCP_PROJECT"
else
  echo "    skipped (SEED=1 seeds sunrise_dairy; it resets the demo stock)"
fi

echo
echo "Bootstrap done for $GCP_PROJECT."
echo "Next: bash shared/infra/deploy_live.sh, then bash shared/infra/setup_github_deploy.sh"
