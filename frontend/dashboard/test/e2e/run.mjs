#!/usr/bin/env node
// Runs the dashboard's end-to-end smoke test (Playwright + axe) against the Firestore emulator.
//
//   node test/e2e/run.mjs [extra playwright args]      (from frontend/dashboard)
//
// - FIRESTORE_EMULATOR_HOST set and answering: runs the tests against that emulator.
// - Otherwise starts one with `firebase emulators:exec --only firestore --project demo-aira`.
// - Skips (exit 0, with the reason) when the emulator cannot run here: no Java, no firebase-tools,
//   or the e2e dev dependencies are not installed. AIRA_E2E_REQUIRED=1 or --require makes a skip fail.
// Nothing here deploys or touches a real Firebase project: demo-* projects are emulator-only.
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { E2E_PROJECT, emulatorReachable } from "./firestoreRest.mjs";
import { FIREBASE_TOOLS, findFirebase, findJava, javaEnv, planRun } from "./prereqs.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const dashboardDir = resolve(here, "..", "..");
const repoRoot = resolve(dashboardDir, "..", "..");
const configPath = join(here, "playwright.config.mjs");

const args = process.argv.slice(2);
const required = args.includes("--require") || process.env.AIRA_E2E_REQUIRED === "1";
const playwrightArgs = args.filter((a) => a !== "--require");

const requireFromApp = createRequire(join(dashboardDir, "package.json"));
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
  const r = spawnSync(cmd, cmdArgs, { stdio: "ignore", timeout });
  return r.status === 0;
}

function finish(code) {
  process.exit(code ?? 1);
}

function skip(reason) {
  const msg = `dashboard e2e: SKIPPED: ${reason}`;
  if (required) {
    console.error(`${msg} (AIRA_E2E_REQUIRED is set, so this fails)`);
    finish(1);
  }
  console.log(msg);
  finish(0);
}

/** Quotes one argument for the POSIX shell that emulators:exec runs its script in. */
const q = (s) => `"${String(s).replace(/(["\\$`])/g, "\\$1")}"`;

const emulatorHost = process.env.FIRESTORE_EMULATOR_HOST?.trim() || null;
const emulatorUp = emulatorHost ? await emulatorReachable(emulatorHost) : false;
const needEmulator = hasDeps && !emulatorHost;
const java = needEmulator ? findJava({ env: process.env, platform: process.platform, works: (bin) => runs(bin, ["-version"]) }) : null;
const firebase =
  needEmulator && java
    ? findFirebase({
        env: process.env,
        works: (cmd) => (cmd === "npx" ? runs("npx", ["-y", FIREBASE_TOOLS, "--version"], 300_000) : runs(cmd, ["--version"])),
      })
    : null;

const plan = planRun({ hasDeps, emulatorHost, emulatorUp, java, firebase });
if (plan.action === "skip") skip(plan.reason);

const playwrightCmd = [process.execPath, playwrightCli, "test", "-c", configPath, ...playwrightArgs];

if (plan.action === "attach") {
  console.log(`dashboard e2e: using the Firestore emulator at ${plan.emulatorHost}`);
  const child = spawn(playwrightCmd[0], playwrightCmd.slice(1), { cwd: dashboardDir, stdio: "inherit" });
  child.on("exit", (code) => finish(code));
} else {
  // Run firebase from a temp dir so the emulator's debug log stays out of the repo.
  const workDir = mkdtempSync(join(tmpdir(), "aira-e2e-"));
  const [bin, ...pre] = plan.firebase;
  const fbArgs = [
    ...pre, "emulators:exec", "--only", "firestore", "--project", E2E_PROJECT,
    "--config", join(repoRoot, "shared", "infra", "firebase.json"),
    playwrightCmd.map(q).join(" "),
  ];
  console.log(`dashboard e2e: starting the Firestore emulator (java: ${plan.java})`);
  const child = spawn(bin, fbArgs, { cwd: workDir, stdio: "inherit", env: javaEnv(plan.java, process.env) });
  child.on("exit", (code) => {
    // Keep the emulator log only when something failed.
    if (code === 0) rmSync(workDir, { recursive: true, force: true });
    else console.error(`dashboard e2e: emulator log kept in ${workDir}`);
    finish(code);
  });
}
