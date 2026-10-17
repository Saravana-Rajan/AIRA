// Playwright config for the dashboard smoke test. Run it through `node test/e2e/run.mjs`, which starts
// the Firestore emulator (firebase emulators:exec) or skips when it cannot run on this machine.
// Plain ESM (.mjs) so the app's `tsc --noEmit` never needs Playwright's types.
import { defineConfig, chromium } from "@playwright/test";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { E2E_DATABASE, E2E_PROJECT, parseEmulatorHost } from "./firestoreRest.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const dashboardDir = resolve(here, "..", "..");
const PORT = Number(process.env.AIRA_E2E_PORT || 5175);
const BASE_URL = `http://127.0.0.1:${PORT}/dashboard/`;
const emulator = parseEmulatorHost(process.env.FIRESTORE_EMULATOR_HOST);

/** Playwright's own Chromium when installed, else the machine's Google Chrome (AIRA_E2E_CHANNEL overrides). */
function channel() {
  if (process.env.AIRA_E2E_CHANNEL) return process.env.AIRA_E2E_CHANNEL;
  try {
    if (existsSync(chromium.executablePath())) return undefined;
  } catch {
    // fall through to Chrome
  }
  return "chrome";
}

export default defineConfig({
  testDir: here,
  testMatch: "*.spec.mjs",
  // Results and traces stay out of the repo.
  outputDir: join(tmpdir(), "aira-dashboard-e2e"),
  reporter: [["list"]],
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: { baseURL: BASE_URL, channel: channel(), trace: "retain-on-failure" },
  // No emulator: the spec skips itself, so there is no app to start.
  webServer: emulator
    ? {
        command: `npx vite --port ${PORT} --strictPort --host 127.0.0.1`,
        cwd: dashboardDir,
        url: BASE_URL,
        reuseExistingServer: false,
        timeout: 120_000,
        // Process env wins over any .env file, so the app only ever talks to the emulator here.
        env: {
          VITE_USE_EMULATOR: "1",
          VITE_FIRESTORE_EMULATOR_PORT: String(emulator.port),
          VITE_FIRESTORE_DATABASE: E2E_DATABASE,
          VITE_FIREBASE_PROJECT_ID: E2E_PROJECT,
          VITE_FIREBASE_API_KEY: "demo-key",
          VITE_FIREBASE_AUTH_DOMAIN: `${E2E_PROJECT}.firebaseapp.com`,
          VITE_FIREBASE_STORAGE_BUCKET: `${E2E_PROJECT}.appspot.com`,
          VITE_FIREBASE_MESSAGING_SENDER_ID: "0",
          VITE_FIREBASE_APP_ID: "1:0:web:demo",
          // Unreachable on purpose: the Forecast page must say "Forecast unavailable", not hang.
          VITE_AIRA_LIVE_HTTP: "http://127.0.0.1:9",
          VITE_AIRA_LIVE_WS: "",
        },
      }
    : undefined,
});
