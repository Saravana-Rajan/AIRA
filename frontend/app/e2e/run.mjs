#!/usr/bin/env node
// Runs the phone app's end-to-end browser test (Playwright + axe) against the mock aira-live.
//
//   node e2e/run.mjs [extra playwright args]      (from frontend/app; `npm run e2e`)
//
// 1. Starts the mock backend: backend in AIRA_MOCK=1 (no Gemini key, no Firestore, nothing sent),
//    with the extra /e2e routes of aira_live.devtools.e2e_server, on a free port.
// 2. Playwright starts the Vite dev server with VITE_AIRA_LIVE_WS pointing at it (playwright.config.mjs).
// 3. The spec (app.spec.mjs) drives the app in a real browser and checks it with axe.
//
// Skips (exit 0, with the reason) when it cannot run here: no browser (neither Playwright's Chromium nor
// Google Chrome), no uv, or the e2e dev dependencies are not installed. AIRA_E2E_REQUIRED=1 or --require
// turns a skip into a failure (CI).
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { findBrowser, planRun } from "./prereqs.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const appDir = resolve(here, "..");
const liveDir = resolve(appDir, "..", "..", "backend");
const configPath = join(here, "playwright.config.mjs");

const args = process.argv.slice(2);
const required = args.includes("--require") || process.env.AIRA_E2E_REQUIRED === "1";
const playwrightArgs = args.filter((a) => a !== "--require");

const requireFromApp = createRequire(join(appDir, "package.json"));
const resolveDep = (name) => {
  try {
    return requireFromApp.resolve(name);
  } catch {
    return null;
  }
};
const playwrightCli = resolveDep("@playwright/test/cli");
const hasDeps = Boolean(playwrightCli && resolveDep("@axe-core/playwright"));

/** True when `cmd args` runs and exits 0 (output hidden). */
function runs(cmd, cmdArgs, timeout = 30_000) {
  return spawnSync(cmd, cmdArgs, { stdio: "ignore", timeout }).status === 0;
}

function chromiumPath() {
  if (!hasDeps) return null;
  try {
    return requireFromApp("@playwright/test").chromium.executablePath();
  } catch {
    return null;
  }
}

/** A TCP port nothing listens on right now. */
function freePort() {
  return new Promise((res, rej) => {
    const srv = createServer();
    srv.unref();
    srv.on("error", rej);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => res(port));
    });
  });
}

async function waitFor(url, child, timeoutMs) {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    if (child.exitCode !== null) return false;
    try {
      const r = await fetch(url);
      if (r.ok) return true;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  return false;
}

function finish(code) {
  process.exit(code ?? 1);
}

function skip(reason) {
  const msg = `app e2e: SKIPPED: ${reason}`;
  if (required) {
    console.error(`${msg} (AIRA_E2E_REQUIRED is set, so this fails)`);
    finish(1);
  }
  console.log(msg);
  finish(0);
}

const browser = hasDeps
  ? findBrowser({ env: process.env, chromiumPath: chromiumPath(), exists: existsSync, platform: process.platform })
  : null;
const uv = hasDeps && browser ? (process.env.UV_BIN || (runs("uv", ["--version"]) ? "uv" : null)) : null;
const plan = planRun({ hasDeps, browser, uv });
if (plan.action === "skip") skip(plan.reason);

const [livePort, vitePort] = await Promise.all([freePort(), freePort()]);
const liveHttp = `http://127.0.0.1:${livePort}`;
const origin = `http://127.0.0.1:${vitePort}`;

console.log(`app e2e: starting the mock aira-live on ${liveHttp} (browser: ${browser.name})`);
const backend = spawn(
  uv,
  ["run", "--frozen", "uvicorn", "aira_live.devtools.e2e_server:app", "--host", "127.0.0.1", "--port", String(livePort),
   "--log-level", "warning"],
  {
    cwd: liveDir,
    stdio: ["ignore", "inherit", "inherit"],
    env: {
      ...process.env,
      AIRA_MOCK: "1",
      GEMINI_API_KEY: "", // mock even when a .env on this machine has a key
      AIRA_ALLOWED_ORIGINS: origin, // the Vite dev server is the only page allowed on /ws
    },
  },
);
const stopBackend = () => {
  if (backend.exitCode === null) backend.kill("SIGTERM");
};
process.on("exit", stopBackend);
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => { stopBackend(); finish(130); });

if (!(await waitFor(`${liveHttp}/health`, backend, 90_000))) {
  stopBackend();
  console.error("app e2e: the mock backend did not come up (uv run uvicorn ... failed; see above)");
  finish(1);
}

const child = spawn(process.execPath, [playwrightCli, "test", "-c", configPath, ...playwrightArgs], {
  cwd: appDir,
  stdio: "inherit",
  env: {
    ...process.env,
    AIRA_E2E_HTTP: liveHttp,
    AIRA_E2E_WS: `ws://127.0.0.1:${livePort}/ws`,
    AIRA_E2E_PORT: String(vitePort),
    ...(browser.channel ? { AIRA_E2E_CHANNEL: browser.channel } : {}),
  },
});
child.on("exit", (code) => {
  stopBackend();
  finish(code);
});
