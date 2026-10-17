#!/usr/bin/env node
// Lighthouse accessibility gate for every Shop Dashboard page (build overrides §6: score >= 0.95).
//
//   node test/lighthouse/run.mjs [--url <dashboard url>] [--pages stock,today,...] [--min 0.95]
//                              [--out <report dir>] [--require]
//
// - No --url: builds the dashboard against the Firestore emulator (database "aira", project demo-aira),
//   seeds the Sunrise Dairy demo shop (test/e2e/demoShop.mjs), serves it with `vite preview` at
//   /dashboard/ and audits each page. It attaches to FIRESTORE_EMULATOR_HOST when one answers there,
//   otherwise starts one with `firebase emulators:exec` (needs Java).
// - --url: audits that URL (for example https://aira-shop.web.app/dashboard/) as it is. Read-only.
// - Skips (exit 0, with the reason) when Chrome, npx, Java or firebase-tools is missing.
//   --require or AIRA_LIGHTHOUSE_REQUIRED=1 makes a skip fail.
// Each report is gated by a11y-score.mjs. Reports and the build go to a temp dir, never the repo; the
// temp dir is removed on PASS and kept on FAIL. --out <dir> keeps the reports there instead.
// Nothing here deploys or touches a real Firebase project: demo-* projects are emulator-only.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_MIN, checkScore, failedAudits } from "./a11y-score.mjs";
import { buildDemoShop } from "../e2e/demoShop.mjs";
import { E2E_DATABASE, E2E_PROJECT, clearDatabase, commitDocs, emulatorReachable, parseEmulatorHost } from "../e2e/firestoreRest.mjs";
import { findFirebase, findJava, javaEnv, FIREBASE_TOOLS } from "../e2e/prereqs.mjs";

/** Lighthouse major fetched through npx. */
export const LIGHTHOUSE = "lighthouse@12";

/** Every dashboard section, by its hash route (src/components/Layout.tsx ROUTES). */
export const PAGES = ["#/", "#/today", "#/stock", "#/prices", "#/orders", "#/audit", "#/forecast"];

const PAGE_NAMES = { overview: "#/", today: "#/today", stock: "#/stock", orders: "#/orders", audit: "#/audit", forecast: "#/forecast" };

/** Firestore keeps a listen channel open, so the network never goes quiet: cap the wait for load. */
const MAX_WAIT_FOR_LOAD_MS = 20_000;

const USAGE =
  "Usage: node test/lighthouse/run.mjs [--url <dashboard url>] [--pages stock,today,...] [--min 0.95] [--out <report dir>] [--require]";

/** Command-line options. Throws on an unknown flag, an unknown page or a bad minimum. */
export function parseArgs(argv, env) {
  const opts = { url: null, pages: PAGES, min: DEFAULT_MIN, require: env.AIRA_LIGHTHOUSE_REQUIRED === "1", out: null };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const value = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${arg} needs a value`);
      return v;
    };
    if (arg === "--require") opts.require = true;
    else if (arg === "--url") opts.url = value();
    else if (arg === "--out") opts.out = value();
    else if (arg === "--min") {
      const raw = value();
      const min = Number(raw);
      if (!Number.isFinite(min) || min <= 0) throw new Error(`--min must be a number such as 0.95 (got "${raw}")`);
      opts.min = min;
    } else if (arg === "--pages") {
      opts.pages = value()
        .split(",")
        .map((p) => p.trim())
        .filter(Boolean)
        .map((p) => {
          const hash = PAGES.includes(p) ? p : PAGE_NAMES[p];
          if (!hash) throw new Error(`unknown page "${p}" (use ${Object.keys(PAGE_NAMES).join(", ")})`);
          return hash;
        });
    } else throw new Error(`unknown option ${arg}`);
  }
  return opts;
}

/** The options again as arguments, for the run inside `emulators:exec` (its cwd differs, so --out is absolute). */
export function forwardArgs(opts, cwd) {
  const args = ["--pages", opts.pages.join(","), "--min", String(opts.min)];
  if (opts.out) args.push("--out", resolve(cwd, opts.out));
  if (opts.require) args.push("--require");
  return args;
}

/** The page's URL: the /dashboard/ base (with the trailing slash Vite's base needs) plus its hash route. */
export function pageUrl(base, hash) {
  const u = new URL(base);
  if (!u.pathname.endsWith("/")) u.pathname += "/";
  u.hash = hash;
  return u.toString();
}

/** Lighthouse CLI arguments: accessibility only, headless Chrome, a JSON report at outputPath. */
export function lighthouseArgs({ url, outputPath }) {
  return [
    url,
    "--only-categories=accessibility",
    "--output=json",
    `--output-path=${outputPath}`,
    "--chrome-flags=--headless=new",
    `--max-wait-for-load=${MAX_WAIT_FOR_LOAD_MS}`,
    "--quiet",
  ];
}

const CHROME_CANDIDATES = {
  darwin: [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
  ],
  linux: ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"],
};

/** The Chrome Lighthouse drives: CHROME_PATH, then the usual install paths. Null if none exists. */
export function findChrome({ env, platform, exists }) {
  const tried = [env.CHROME_PATH, ...(CHROME_CANDIDATES[platform] ?? [])].filter(Boolean);
  return tried.find((p) => exists(p)) ?? null;
}

/**
 * remote: audit the given URL. attach: use the emulator at FIRESTORE_EMULATOR_HOST.
 * emulator: start one with `firebase emulators:exec`. skip: say why (exit 0 unless --require).
 */
export function planLighthouse({ url, emulatorHost, emulatorUp, java, firebase, chrome, npx }) {
  if (!chrome) return { action: "skip", reason: "Chrome is not installed (set CHROME_PATH)" };
  if (!npx) return { action: "skip", reason: `npx is not available to fetch ${LIGHTHOUSE}` };
  if (url) return { action: "remote", url, chrome };
  if (emulatorHost) {
    return emulatorUp
      ? { action: "attach", emulatorHost, chrome }
      : { action: "skip", reason: `FIRESTORE_EMULATOR_HOST=${emulatorHost} is set, but no emulator answers there` };
  }
  if (!java) return { action: "skip", reason: "Java is not available; the Firestore emulator needs a JDK (set JAVA_HOME)" };
  if (!firebase) return { action: "skip", reason: "firebase-tools is not available (install it, or allow npx to fetch it)" };
  return { action: "emulator", java, firebase, chrome };
}

/** Build-time env for the audited preview: the emulator only, and an unreachable forecast URL. */
export function previewEnv(emulatorPort) {
  return {
    VITE_USE_EMULATOR: "1",
    VITE_FIRESTORE_EMULATOR_PORT: String(emulatorPort),
    VITE_FIRESTORE_DATABASE: E2E_DATABASE,
    VITE_FIREBASE_PROJECT_ID: E2E_PROJECT,
    VITE_FIREBASE_API_KEY: "demo-key",
    VITE_FIREBASE_AUTH_DOMAIN: `${E2E_PROJECT}.firebaseapp.com`,
    VITE_FIREBASE_STORAGE_BUCKET: `${E2E_PROJECT}.appspot.com`,
    VITE_FIREBASE_MESSAGING_SENDER_ID: "0",
    VITE_FIREBASE_APP_ID: "1:0:web:demo",
    // Unreachable on purpose: the Forecast page is audited in its "Forecast unavailable" state.
    VITE_AIRA_LIVE_HTTP: "http://127.0.0.1:9",
  };
}

/** One line per page, and ok only when every page has a report at or above min. */
export function summarize(results, min) {
  const lines = results.map(({ page, report, error }) => {
    if (!report) return `${page}: no report (${error ?? "unknown error"}) FAIL`;
    const { ok, score } = checkScore(report, min);
    if (score === null) return `${page}: no score (${report.runtimeError?.code ?? "no accessibility score in the report"}) FAIL`;
    return `${page}: ${score} ${ok ? "PASS" : "FAIL"}`;
  });
  const ok = results.length > 0 && results.every(({ report }) => report && checkScore(report, min).ok);
  return { ok, lines };
}

// ---------------------------------------------------------------------------------------------------

const here = dirname(fileURLToPath(import.meta.url));
const dashboardDir = resolve(here, "..", "..");
const repoRoot = resolve(dashboardDir, "..", "..");

/** True when `cmd args` runs and exits 0 (output hidden). */
function runs(cmd, args, timeout = 30_000) {
  return spawnSync(cmd, args, { stdio: "ignore", timeout }).status === 0;
}

/** Quotes one argument for the POSIX shell that emulators:exec runs its script in. */
const q = (s) => `"${String(s).replace(/(["\\$`])/g, "\\$1")}"`;

/** Runs Lighthouse on each page and reads its report. */
function auditPages(urls, chrome, outDir) {
  return urls.map(({ page, url }) => {
    const outputPath = join(outDir, `lh-${page.replace(/[^a-z]/g, "") || "overview"}.json`);
    console.log(`dashboard lighthouse: auditing ${url}`);
    const r = spawnSync("npx", ["-y", LIGHTHOUSE, ...lighthouseArgs({ url, outputPath })], {
      stdio: "inherit",
      env: { ...process.env, CHROME_PATH: chrome },
      timeout: 180_000,
    });
    if (!existsSync(outputPath)) return { page, report: null, error: `Lighthouse exited ${r.status ?? r.signal}` };
    try {
      return { page, report: JSON.parse(readFileSync(outputPath, "utf8")), outputPath };
    } catch (err) {
      return { page, report: null, error: `unreadable report: ${err.message}` };
    }
  });
}

function report(results, min) {
  const { ok, lines } = summarize(results, min);
  console.log(`\nLighthouse accessibility (min ${min}):`);
  for (const line of lines) console.log(`  ${line}`);
  for (const r of results) {
    if (!r.report || checkScore(r.report, min).ok) continue;
    for (const a of failedAudits(r.report)) console.log(`  ${r.page} - ${a.id} (weight ${a.weight}): ${a.title}`);
    if (r.outputPath) console.log(`  ${r.page} report: ${r.outputPath}`);
  }
  console.log(ok ? "dashboard lighthouse: PASS" : "dashboard lighthouse: FAIL");
  return ok;
}

/** Audits the pages and gates them. The temp dir goes on PASS (or when --out holds the reports). */
function gate(urls, chrome, opts, work) {
  const reportDir = opts.out ?? work;
  mkdirSync(reportDir, { recursive: true });
  const ok = report(auditPages(urls, chrome, reportDir), opts.min);
  if (ok || opts.out) rmSync(work, { recursive: true, force: true });
  if (opts.out) console.log(`dashboard lighthouse: reports in ${reportDir}`);
  return ok ? 0 : 1;
}

async function waitForHttp(url, timeoutMs = 30_000) {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    try {
      const res = await fetch(url);
      if (res.ok) return true;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 300));
  }
  return false;
}

/** Build against the emulator, seed the demo shop, serve with `vite preview`, audit every page. */
async function auditLocal(emulatorHost, chrome, opts) {
  const emulator = parseEmulatorHost(emulatorHost);
  const vite = join(dirname(createRequire(join(dashboardDir, "package.json")).resolve("vite/package.json")), "bin", "vite.js");
  const work = mkdtempSync(join(tmpdir(), "aira-lighthouse-"));
  const dist = join(work, "dist");
  const port = Number(process.env.AIRA_LIGHTHOUSE_PORT || 4173);

  console.log("dashboard lighthouse: building the dashboard against the Firestore emulator");
  const build = spawnSync(process.execPath, [vite, "build", "--outDir", dist, "--emptyOutDir", "--logLevel", "warn"], {
    cwd: dashboardDir,
    stdio: "inherit",
    // Process env wins over any .env file, so the build only ever talks to the emulator.
    env: { ...process.env, ...previewEnv(emulator.port) },
  });
  if (build.status !== 0) {
    console.error("dashboard lighthouse: the build failed");
    return 1;
  }

  const target = { emulatorHost, projectId: E2E_PROJECT, databaseId: E2E_DATABASE };
  await clearDatabase(target);
  await commitDocs({ ...target, docs: buildDemoShop(Date.now()).docs });

  const preview = spawn(process.execPath, [vite, "preview", "--outDir", dist, "--port", String(port), "--strictPort", "--host", "127.0.0.1"], {
    cwd: dashboardDir,
    stdio: "ignore",
  });
  try {
    const base = `http://127.0.0.1:${port}/dashboard/`;
    if (!(await waitForHttp(base))) {
      console.error(`dashboard lighthouse: vite preview did not answer at ${base}`);
      return 1;
    }
    return gate(opts.pages.map((page) => ({ page, url: pageUrl(base, page) })), chrome, opts, work);
  } finally {
    preview.kill();
  }
}

async function main(argv) {
  let opts;
  try {
    opts = parseArgs(argv, process.env);
  } catch (err) {
    console.error(`dashboard lighthouse: ${err.message}\n${USAGE}`);
    return 1;
  }
  if (opts.out) opts.out = resolve(opts.out);
  const skip = (reason) => {
    const msg = `dashboard lighthouse: SKIPPED: ${reason}`;
    if (opts.require) {
      console.error(`${msg} (--require / AIRA_LIGHTHOUSE_REQUIRED is set, so this fails)`);
      return 1;
    }
    console.log(msg);
    return 0;
  };

  const chrome = findChrome({ env: process.env, platform: process.platform, exists: existsSync });
  const npx = runs("npx", ["--version"]);
  const emulatorHost = opts.url ? null : process.env.FIRESTORE_EMULATOR_HOST?.trim() || null;
  const emulatorUp = emulatorHost ? await emulatorReachable(emulatorHost) : false;
  const needEmulator = Boolean(chrome && npx && !opts.url && !emulatorHost);
  const java = needEmulator ? findJava({ env: process.env, platform: process.platform, works: (bin) => runs(bin, ["-version"]) }) : null;
  const firebase =
    needEmulator && java
      ? findFirebase({
          env: process.env,
          works: (cmd) => (cmd === "npx" ? runs("npx", ["-y", FIREBASE_TOOLS, "--version"], 300_000) : runs(cmd, ["--version"])),
        })
      : null;

  const plan = planLighthouse({ url: opts.url, emulatorHost, emulatorUp, java, firebase, chrome, npx });
  if (plan.action === "skip") return skip(plan.reason);

  if (plan.action === "remote") {
    const work = mkdtempSync(join(tmpdir(), "aira-lighthouse-"));
    return gate(opts.pages.map((page) => ({ page, url: pageUrl(plan.url, page) })), plan.chrome, opts, work);
  }

  if (plan.action === "attach") {
    console.log(`dashboard lighthouse: using the Firestore emulator at ${plan.emulatorHost}`);
    return auditLocal(plan.emulatorHost, plan.chrome, opts);
  }

  // Start the emulator and run this script again inside it (FIRESTORE_EMULATOR_HOST is then set).
  // firebase runs from a temp dir so the emulator's debug log stays out of the repo.
  const workDir = mkdtempSync(join(tmpdir(), "aira-lighthouse-emu-"));
  const [bin, ...pre] = plan.firebase;
  const inner = [process.execPath, fileURLToPath(import.meta.url), ...forwardArgs(opts, process.cwd())].map(q).join(" ");
  const fbArgs = [...pre, "emulators:exec", "--only", "firestore", "--project", E2E_PROJECT, "--config", join(repoRoot, "shared", "infra", "firebase.json"), inner];
  console.log(`dashboard lighthouse: starting the Firestore emulator (java: ${plan.java})`);
  return new Promise((done) => {
    const child = spawn(bin, fbArgs, { cwd: workDir, stdio: "inherit", env: javaEnv(plan.java, process.env) });
    child.on("exit", (code) => {
      if (code === 0) rmSync(workDir, { recursive: true, force: true });
      else console.error(`dashboard lighthouse: emulator log kept in ${workDir}`);
      done(code ?? 1);
    });
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (err) => {
      console.error(`dashboard lighthouse: ${err?.stack ?? err}`);
      process.exit(1);
    },
  );
}
