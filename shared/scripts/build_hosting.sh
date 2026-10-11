#!/usr/bin/env bash
# Build the single Firebase Hosting bundle for the site aira-shop:
#   frontend/app        -> dist/hosting/            served at /
#   frontend/dashboard  -> dist/hosting/dashboard/  served at /dashboard/ (Vite base /dashboard/)
# firebase.json serves dist/hosting with rewrites /dashboard/** -> /dashboard/index.html
# and ** -> /index.html.
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
cd "$ROOT"

if [ ! -d node_modules ]; then
  npm ci
fi
if [ -z "${VITE_AIRA_LIVE_WS:-}" ]; then
  echo "warning: VITE_AIRA_LIVE_WS is not set; the app will use ws://localhost:8080/ws" >&2
fi

rm -rf "$OUT"
npm run build -w frontend/app -- --outDir "$OUT" --emptyOutDir
npm run build -w frontend/dashboard -- --base /dashboard/ --outDir "$OUT/dashboard" --emptyOutDir

for page in "$OUT/index.html" "$OUT/dashboard/index.html"; do
  if [ ! -f "$page" ]; then
    echo "error: $page was not built" >&2
    exit 1
  fi
done
echo "Hosting bundle ready: $OUT"
