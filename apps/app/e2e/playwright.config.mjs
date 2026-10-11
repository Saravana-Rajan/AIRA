// Playwright config for the phone app's browser test. Run it through `node e2e/run.mjs`, which starts the
// mock backend and sets AIRA_E2E_HTTP / AIRA_E2E_WS / AIRA_E2E_PORT; without them the spec skips itself.
// Plain ESM (.mjs) so the app's `tsc --noEmit` never needs Playwright's types.
import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appDir = resolve(here, "..");
const PORT = Number(process.env.AIRA_E2E_PORT || 5174);
const BASE_URL = `http://127.0.0.1:${PORT}/`;
const ws = process.env.AIRA_E2E_WS;

export default defineConfig({
  testDir: here,
  testMatch: "*.spec.mjs",
  // Results and traces stay out of the repo.
  outputDir: join(tmpdir(), "aira-app-e2e"),
  reporter: [["list"]],
  workers: 1,
  fullyParallel: false,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: BASE_URL,
    channel: process.env.AIRA_E2E_CHANNEL || undefined,
    trace: "retain-on-failure",
    // The Start tap asks for the mic and the camera: a fake device answers, so nothing prompts.
    permissions: ["microphone", "camera"],
    launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] },
  },
  // No backend: the spec skips itself, so there is no app to start.
  webServer: ws
    ? {
        command: `npx vite --port ${PORT} --strictPort --host 127.0.0.1`,
        cwd: appDir,
        url: BASE_URL,
        reuseExistingServer: false,
        timeout: 120_000,
        // Process env wins over any .env file, so the app only ever talks to the mock backend here.
        env: { VITE_AIRA_LIVE_WS: ws },
      }
    : undefined,
});
