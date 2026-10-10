#!/usr/bin/env bash
# Keyless GitHub Actions deploys (Workload Identity Federation). Idempotent: safe to re-run.
#
# Creates or updates:
#   - workload identity pool  aira-github-pool  (global)
#   - OIDC provider           aira-github       GitHub's token issuer; only the repository
#                             Saravana-Rajan/AIRA on refs/heads/main may use it
#   - Artifact Registry repo  aira              (docker, asia-south1) for the image built in the runner
#   - service account         aira-deployer     with the minimum roles for .github/workflows/deploy.yml:
#       roles/run.developer                    on the aira-live service only (service-level IAM)
#       roles/artifactregistry.writer          on the Artifact Registry repo aira only: docker push
#       roles/iam.serviceAccountUser           on aira-live only: deploy revisions that run as aira-live
#       roles/firebasehosting.admin            firebase deploy --only hosting:aira-shop
#       roles/serviceusage.serviceUsageConsumer  API calls billed to the project
#   - roles/iam.workloadIdentityUser on aira-deployer for the GitHub repository principal set.
# No JSON key is ever created.
#
# Usage: GCP_PROJECT=<project-id> bash infra/setup_github_deploy.sh   (GCP_PROJECT may come from .env)
#        GITHUB_REPO=<owner>/<repo> overrides the repository (default Saravana-Rajan/AIRA).
# Needs: gcloud logged in as a project owner, infra/bootstrap.sh already run (the aira-live service
#        account exists) and the first deploy done by infra/deploy_live.sh (the aira-live service exists).
# Then:  set the printed values as GitHub repository SECRETS (Settings > Secrets and variables >
#        Actions > Secrets), plus the VITE_FIREBASE_* web-app config values. The repo is public:
#        never use repository variables for these.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${GITHUB_REPO:-Saravana-Rajan/AIRA}"
POOL="aira-github-pool"
PROVIDER="aira-github"
DEPLOYER="aira-deployer"
ISSUER="https://token.actions.githubusercontent.com"
REGION="asia-south1"
AR_REPO="aira"
MAPPING="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref"
CONDITION="assertion.repository=='${REPO}' && assertion.ref=='refs/heads/main'"

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

DEPLOYER_SA="${DEPLOYER}@${GCP_PROJECT}.iam.gserviceaccount.com"
RUNTIME_SA="aira-live@${GCP_PROJECT}.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$RUNTIME_SA" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  echo "error: service account $RUNTIME_SA not found; run infra/bootstrap.sh first" >&2
  exit 1
fi

echo "==> Enabling the IAM / STS / Artifact Registry APIs"
gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudresourcemanager.googleapis.com \
  artifactregistry.googleapis.com \
  --project "$GCP_PROJECT"

echo "==> Artifact Registry repo $AR_REPO ($REGION)"
if gcloud artifacts repositories describe "$AR_REPO" --location="$REGION" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  echo "    exists"
else
  gcloud artifacts repositories create "$AR_REPO" --repository-format=docker --location="$REGION" \
    --description="AIRA images (aira-live)" --project "$GCP_PROJECT"
fi

PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')"

echo "==> Workload identity pool $POOL"
if gcloud iam workload-identity-pools describe "$POOL" --location=global --project "$GCP_PROJECT" >/dev/null 2>&1; then
  echo "    exists"
else
  gcloud iam workload-identity-pools create "$POOL" --location=global \
    --display-name="AIRA GitHub Actions" --project "$GCP_PROJECT"
fi

echo "==> OIDC provider $PROVIDER (repo $REPO, refs/heads/main)"
if gcloud iam workload-identity-pools providers describe "$PROVIDER" --workload-identity-pool="$POOL" \
  --location=global --project "$GCP_PROJECT" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" \
    --workload-identity-pool="$POOL" --location=global \
    --issuer-uri="$ISSUER" \
    --attribute-mapping="$MAPPING" \
    --attribute-condition="$CONDITION" \
    --project "$GCP_PROJECT"
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
    --workload-identity-pool="$POOL" --location=global \
    --display-name="AIRA GitHub" \
    --issuer-uri="$ISSUER" \
    --attribute-mapping="$MAPPING" \
    --attribute-condition="$CONDITION" \
    --project "$GCP_PROJECT"
fi

echo "==> Deployer service account $DEPLOYER"
if gcloud iam service-accounts describe "$DEPLOYER_SA" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  echo "    exists"
else
  gcloud iam service-accounts create "$DEPLOYER" \
    --display-name="AIRA GitHub deployer" --project "$GCP_PROJECT"
fi

for role in \
  roles/firebasehosting.admin \
  roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
    --member="serviceAccount:$DEPLOYER_SA" --role="$role" --condition=None >/dev/null
  echo "    $role"
done

# run.developer on the aira-live service itself (its own IAM policy), never project-wide.
# A project-level IAM condition on resource.name does not work here: the Cloud Run v1 API
# names the service namespaces/<project>/services/aira-live, so the condition never matches.
if gcloud run services describe aira-live --region "$REGION" --project "$GCP_PROJECT" >/dev/null 2>&1; then
  gcloud run services add-iam-policy-binding aira-live --region "$REGION" \
    --member="serviceAccount:$DEPLOYER_SA" --role=roles/run.developer \
    --project "$GCP_PROJECT" >/dev/null
  echo "    roles/run.developer on the aira-live service"
else
  echo "    aira-live does not exist yet: run infra/deploy_live.sh, then re-run this script" >&2
fi

gcloud artifacts repositories add-iam-policy-binding "$AR_REPO" --location="$REGION" \
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/artifactregistry.writer \
  --project "$GCP_PROJECT" >/dev/null
echo "    roles/artifactregistry.writer on $AR_REPO"

gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:$DEPLOYER_SA" --role=roles/iam.serviceAccountUser \
  --project "$GCP_PROJECT" >/dev/null
echo "    roles/iam.serviceAccountUser on $RUNTIME_SA"

PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" \
  --member="$PRINCIPAL" --role=roles/iam.workloadIdentityUser \
  --project "$GCP_PROJECT" >/dev/null
echo "    roles/iam.workloadIdentityUser for $REPO"

WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"

cat <<EOF

Set these GitHub repository SECRETS (not variables; the repo is public) on $REPO:
  GCP_PROJECT=$GCP_PROJECT
  GCP_WIF_PROVIDER=$WIF_PROVIDER
  GCP_DEPLOY_SA=$DEPLOYER_SA
plus the Firebase web-app config: VITE_FIREBASE_API_KEY, VITE_FIREBASE_AUTH_DOMAIN,
VITE_FIREBASE_PROJECT_ID, VITE_FIREBASE_STORAGE_BUCKET, VITE_FIREBASE_MESSAGING_SENDER_ID,
VITE_FIREBASE_APP_ID.

With the GitHub CLI:
  gh secret set GCP_PROJECT --repo $REPO --body "$GCP_PROJECT"
  gh secret set GCP_WIF_PROVIDER --repo $REPO --body "$WIF_PROVIDER"
  gh secret set GCP_DEPLOY_SA --repo $REPO --body "$DEPLOYER_SA"
EOF
