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
#   VITE_AIRA_MEDIA_BUTTON / VITE_AIRA_VOLUME_TRIGGER / VITE_AIRA_WAKEWORD
#                       hands-free start flags, off unless "1" (shared/docs/ops/handsfree.md)
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

# Cache-bust the service worker per build so a new deploy actually reaches installed apps. The browser only
# re-installs the SW (and re-precaches the shell) when sw.js / sw-strategy.js change bytes; they are byte-identical
# across builds otherwise, so a deploy is silently masked. VERSION stays "aira-v1" in the SOURCE (the pwa tests
# read that); only the DEPLOYED copies carry the build hash: the cache name in sw-strategy.js, plus a build stamp
# comment in both so even a browser that does not diff imported scripts still sees sw.js change.
SW_VER="aira-${BUILD_SHA:0:7}"
# vite copies public/ (sw.js, sw-strategy.js) into $OUT, so the real build always has them; stamp only what is
# present so a stubbed/partial build does not fail here. When sw-strategy.js IS present, assert the cache name was
# actually stamped: a silent miss (the source VERSION line changed shape) would ship an unversioned SW and bring
# back the stale-deploy bug.
if [ -f "$OUT/sw-strategy.js" ]; then
  sed -i "s/\"aira-v1\"/\"$SW_VER\"/" "$OUT/sw-strategy.js"
  grep -q "VERSION = \"$SW_VER\"" "$OUT/sw-strategy.js" || { echo "error: SW VERSION not stamped (sw-strategy.js changed?)" >&2; exit 1; }
  printf '\n// build %s\n' "$BUILD_SHA" >> "$OUT/sw-strategy.js"
fi
if [ -f "$OUT/sw.js" ]; then
  printf '\n// build %s\n' "$BUILD_SHA" >> "$OUT/sw.js"
fi

# firebase deploy --config shared/infra/firebase.json only publishes folders inside shared/infra,
# so mirror the bundle to shared/infra/public (git-ignored).
DEPLOY_DIR="$ROOT/shared/infra/public"
rm -rf "$DEPLOY_DIR"
cp -R "$OUT" "$DEPLOY_DIR"
echo "Hosting bundle ready: $OUT (copied to $DEPLOY_DIR for firebase deploy)"
