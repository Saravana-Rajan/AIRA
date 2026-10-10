# AIRA Android app (Trusted Web Activity)

The APK wraps the AIRA web app at `https://aira-shop.web.app` with Bubblewrap.

- Package id: `in.techjays.aira`. Never change it.
- Config: `twa-manifest.json` in this folder. It is the only Bubblewrap file in git.
- Fallback: `customtabs`. A WebView fallback would lose Media Session and speechSynthesis, which the voice UI needs.
- The app runs inside Chrome. Chrome asks for camera and microphone the first time. No Android permissions are added.
- Light theme only: white status bar, navigation bar and splash, with the `--border` colour `#E3E9F4` as the nav divider.
- Off on purpose: notifications, Play Billing, location delegation, extra origins.

This folder holds config only. Nothing here has been built yet.

## What you need

- Node 18 or newer.
- JDK 17. Bubblewrap can download it, and the Android SDK, on first run.
- Bubblewrap CLI: `npm i -g @bubblewrap/cli`
- An Android phone with Chrome, USB debugging on, and `adb`.
- The web app deployed to Firebase Hosting, so `https://aira-shop.web.app/manifest.webmanifest` loads.

If the Hosting URL is not `aira-shop.web.app`, change `host` and every `https://aira-shop.web.app` URL in `twa-manifest.json` first.

## 1. Make the signing key (once)

```bash
cd apps/app/twa
keytool -genkeypair -v -keystore aira.keystore -alias aira \
  -keyalg RSA -keysize 2048 -validity 10000
```

- Save both passwords in the team password manager.
- Never commit `aira.keystore`. This folder's `.gitignore` blocks it, and blocks the APK and the generated Android project.
- If the key is lost, installed copies cannot be updated. Back it up.

## 2. Build the APK

```bash
cd apps/app/twa
bubblewrap update --skipVersionUpgrade   # makes the Android project from twa-manifest.json
bubblewrap build                         # asks for the two passwords
```

- `bubblewrap build` writes `app-release-signed.apk` (to install) and `app-release-bundle.aab` (for the Play Store).
- For CI, set `BUBBLEWRAP_KEYSTORE_PASSWORD` and `BUBBLEWRAP_KEY_PASSWORD` from the secret store instead of typing them.
- If `bubblewrap update` cannot find a project, run `bubblewrap init --manifest https://aira-shop.web.app/manifest.webmanifest` once. Answer every prompt with the values in `twa-manifest.json`: package id `in.techjays.aira`, name `AIRA`, display `standalone`, orientation `portrait`, colours `#FFFFFF`, start URL `/`, key `./aira.keystore` alias `aira`, no Play Billing, no location. Then set `"fallbackType": "customtabs"` again and run `bubblewrap build`.

## 3. Asset links (removes the URL bar)

Android shows AIRA full screen only when the website says it trusts this APK.

Get the SHA-256 fingerprint of the signing key:

```bash
keytool -list -v -keystore apps/app/twa/aira.keystore -alias aira | grep "SHA256:" | awk '{print $2}'
```

Write it into `apps/app/public/.well-known/assetlinks.json`:

```json
[
  {
    "relation": ["delegate_permission/common.handle_all_urls"],
    "target": {
      "namespace": "android_app",
      "package_name": "in.techjays.aira",
      "sha256_cert_fingerprints": ["<the fingerprint printed above>"]
    }
  }
]
```

- Use the real fingerprint only. Never commit a placeholder.
- The fingerprint is public, so this file is safe to commit.
- `test/assetlinks.test.ts` checks the file once it exists.
- If the APK is uploaded to the Play Store with Play App Signing, also add Play's app-signing fingerprint (Play Console, App integrity) to the same list.

Firebase Hosting must serve the file:

- `firebase.json` has `"ignore": ["**/.*", ...]`. That pattern skips `.well-known/`. Remove `**/.*` from the list, or the file is never uploaded.
- Vite copies `public/.well-known/` into `dist/hosting/.well-known/`. The `**` → `/index.html` rewrite only applies when no file matches, so the real file wins.

Then build and deploy Hosting the normal way (`bash scripts/build_hosting.sh`, then the CI deploy). Check:

```bash
curl -s https://aira-shop.web.app/.well-known/assetlinks.json
```

The output must be the JSON above, with `in.techjays.aira`.

## 4. Install and check

```bash
adb install -r apps/app/twa/app-release-signed.apk
```

- Open AIRA from the launcher.
- Pass: it opens full screen with no URL bar.
- Fail: a URL bar shows. The fingerprint does not match the signing key, or `assetlinks.json` is not served. Repeat step 3, then force-stop AIRA and open it again.

## Updating the app

- Web changes need no new APK. The APK loads the live site.
- To change the APK itself (name, icon, colours), edit `twa-manifest.json`, raise `appVersionCode` by 1 and set a new `appVersionName`. Then run step 2 again.
