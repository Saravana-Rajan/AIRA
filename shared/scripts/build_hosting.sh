#!/usr/bin/env bash
# Build the single Firebase Hosting bundle for the site aira-shop:
#   frontend/app        -> dist/hosting/            served at /
#   frontend/dashboard  -> dist/hosting/dashboard/  served at /dashboard/ (Vite base /dashboard/)
# shared/infra/firebase.json serves ../../dist/hosting (relative to the config file) with rewrites
# /dashboard/** -> /dashboard/index.html and ** -> /index.html.
#
# npm runs inside frontend/, the workspace root for app and dashboard (frontend/package.json).
#
# Build-time env read by Vite:
#   VITE_AIRA_LIVE_WS   wss://<cloud-run-host>/ws   (the app falls back to ws://localhost:8080/ws)
#   VITE_AIRA_APP_KEY   the shared demo key sent as hello.idToken (= AIRA_APP_KEY on aira-live; security.md)
#   VITE_FIREBASE_*     Firebase web-app config used by the dashboard
#
# Usage: bash shared/scripts/build_hosting.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$ROOT/dist/hosting"
cd "$ROOT/frontend"

if [ ! -d node_modules ]; then
  npm ci
fi
if [ -z "${VITE_AIRA_LIVE_WS:-}" ]; then
  echo "warning: VITE_AIRA_LIVE_WS is not set; the app will use ws://localhost:8080/ws" >&2
fi

rm -rf "$OUT"
npm run build -w app -- --outDir "$OUT" --emptyOutDir
npm run build -w dashboard -- --base /dashboard/ --outDir "$OUT/dashboard" --emptyOutDir

for page in "$OUT/index.html" "$OUT/dashboard/index.html"; do
  if [ ! -f "$page" ]; then
    echo "error: $page was not built" >&2
    exit 1
  fi
done
# version.json: what the downloads page shows as the latest update (the APK loads this live site).
BUILD_SHA="${GITHUB_SHA:-$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo local)}"
printf '{"build":"%s","updated":"%s"}\n' "${BUILD_SHA:0:7}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUT/version.json"

# firebase deploy --config shared/infra/firebase.json only publishes folders inside shared/infra,
# so mirror the bundle to shared/infra/public (git-ignored).
DEPLOY_DIR="$ROOT/shared/infra/public"
rm -rf "$DEPLOY_DIR"
cp -R "$OUT" "$DEPLOY_DIR"
echo "Hosting bundle ready: $OUT (copied to $DEPLOY_DIR for firebase deploy)"
