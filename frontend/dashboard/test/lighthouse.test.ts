// @vitest-environment node
// `node test/lighthouse/run.mjs` runs Lighthouse (accessibility only) on every dashboard page and gates
// each report with a11y-score.mjs. These tests cover its decisions; nothing here starts Chrome.
import { describe, expect, it } from "vitest";
import {
  LIGHTHOUSE,
  PAGES,
  findChrome,
  forwardArgs,
  lighthouseArgs,
  pageUrl,
  parseArgs,
  planLighthouse,
  previewEnv,
  summarize,
} from "./lighthouse/run.mjs";

const report = (score: number) => ({ categories: { accessibility: { score, auditRefs: [] } }, audits: {} });

describe("PAGES", () => {
  it("covers every dashboard section by its hash route", () => {
    expect(PAGES).toEqual(["#/", "#/today", "#/stock", "#/orders", "#/audit", "#/forecast"]);
  });
});

describe("parseArgs", () => {
  it("defaults to every page on a local emulator-backed preview with min 0.95", () => {
    expect(parseArgs([], {})).toEqual({ url: null, pages: PAGES, min: 0.95, require: false, out: null });
  });

  it("reads --url, --pages, --min and --require", () => {
    const a = parseArgs(["--url", "https://aira-shop.web.app/dashboard/", "--pages", "stock,today", "--min", "0.97", "--require"], {});
    expect(a).toEqual({ url: "https://aira-shop.web.app/dashboard/", pages: ["#/stock", "#/today"], min: 0.97, require: true, out: null });
  });

  it("reads --out, the folder that keeps the reports", () => {
    expect(parseArgs(["--out", "/tmp/aira-lh"], {}).out).toBe("/tmp/aira-lh");
  });

  it("names the overview page 'overview' and accepts hashes as given", () => {
    expect(parseArgs(["--pages", "overview,#/audit"], {}).pages).toEqual(["#/", "#/audit"]);
  });

  it("treats AIRA_LIGHTHOUSE_REQUIRED=1 like --require", () => {
    expect(parseArgs([], { AIRA_LIGHTHOUSE_REQUIRED: "1" }).require).toBe(true);
  });

  it("rejects an unknown page, a bad minimum or an unknown flag", () => {
    expect(() => parseArgs(["--pages", "stock,profits"], {})).toThrow(/profits/);
    expect(() => parseArgs(["--min", "lots"], {})).toThrow(/min/);
    expect(() => parseArgs(["--deploy"], {})).toThrow(/--deploy/);
  });
});

describe("forwardArgs", () => {
  it("passes the options to the run inside the emulator, with --out made absolute", () => {
    const opts = { url: null, pages: ["#/stock", "#/"], min: 0.97, require: true, out: "reports" };
    const args = forwardArgs(opts, "/work/frontend/dashboard");
    expect(args).toEqual(["--pages", "#/stock,#/", "--min", "0.97", "--out", "/work/frontend/dashboard/reports", "--require"]);
    expect(parseArgs(args, {})).toEqual({ ...opts, out: "/work/frontend/dashboard/reports" });
  });

  it("leaves out --out and --require when they are not set", () => {
    expect(forwardArgs({ url: null, pages: PAGES, min: 0.95, require: false, out: null }, "/x")).toEqual([
      "--pages",
      PAGES.join(","),
      "--min",
      "0.95",
    ]);
  });
});

describe("pageUrl", () => {
  it("adds the hash route to the /dashboard/ base", () => {
    expect(pageUrl("http://127.0.0.1:4173/dashboard/", "#/stock")).toBe("http://127.0.0.1:4173/dashboard/#/stock");
  });

  it("adds the trailing slash the Vite base needs and replaces any hash", () => {
    expect(pageUrl("https://aira-shop.web.app/dashboard", "#/")).toBe("https://aira-shop.web.app/dashboard/#/");
    expect(pageUrl("https://aira-shop.web.app/dashboard/#/today", "#/audit")).toBe("https://aira-shop.web.app/dashboard/#/audit");
  });

  it("keeps a query such as ?demo=1", () => {
    expect(pageUrl("https://aira-shop.web.app/dashboard/?demo=1", "#/orders")).toBe("https://aira-shop.web.app/dashboard/?demo=1#/orders");
  });
});

describe("lighthouseArgs", () => {
  it("audits accessibility only, headless, into a JSON report", () => {
    const args = lighthouseArgs({ url: "http://127.0.0.1:4173/dashboard/#/stock", outputPath: "/tmp/lh-stock.json" });
    expect(args[0]).toBe("http://127.0.0.1:4173/dashboard/#/stock");
    expect(args).toContain("--only-categories=accessibility");
    expect(args).toContain("--output=json");
    expect(args).toContain("--output-path=/tmp/lh-stock.json");
    expect(args.find((a) => a.startsWith("--chrome-flags="))).toContain("--headless=new");
    expect(args).toContain("--quiet");
  });

  it("bounds the wait for load, because Firestore keeps a listen channel open", () => {
    const wait = lighthouseArgs({ url: "u", outputPath: "o" }).find((a) => a.startsWith("--max-wait-for-load="));
    expect(Number(wait?.split("=")[1])).toBeGreaterThan(0);
  });

  it("pins a Lighthouse major for npx", () => {
    expect(LIGHTHOUSE).toMatch(/^lighthouse@\d+$/);
  });
});

describe("findChrome", () => {
  const exists = (ok: string[]) => (p: string) => ok.includes(p);

  it("prefers CHROME_PATH", () => {
    expect(findChrome({ env: { CHROME_PATH: "/x/chrome" }, platform: "linux", exists: exists(["/x/chrome"]) })).toBe("/x/chrome");
  });

  it("finds Google Chrome on macOS", () => {
    const mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
    expect(findChrome({ env: {}, platform: "darwin", exists: exists([mac]) })).toBe(mac);
  });

  it("finds Chrome or Chromium on Linux", () => {
    expect(findChrome({ env: {}, platform: "linux", exists: exists(["/usr/bin/chromium"]) })).toBe("/usr/bin/chromium");
  });

  it("returns null when there is no browser", () => {
    expect(findChrome({ env: { CHROME_PATH: "/missing" }, platform: "darwin", exists: exists([]) })).toBeNull();
  });
});

describe("planLighthouse", () => {
  const base = { url: null, emulatorHost: null, emulatorUp: false, java: null, firebase: null, chrome: "/chrome", npx: true };

  it("skips without Chrome, whatever else is there", () => {
    expect(planLighthouse({ ...base, chrome: null, java: "java", firebase: ["firebase"] })).toEqual({
      action: "skip",
      reason: expect.stringMatching(/Chrome/),
    });
  });

  it("skips without npx, which fetches Lighthouse", () => {
    expect(planLighthouse({ ...base, npx: false }).action).toBe("skip");
  });

  it("audits a given URL directly, with no emulator and no build", () => {
    expect(planLighthouse({ ...base, url: "https://aira-shop.web.app/dashboard/" })).toEqual({
      action: "remote",
      url: "https://aira-shop.web.app/dashboard/",
      chrome: "/chrome",
    });
  });

  it("attaches to a running emulator", () => {
    expect(planLighthouse({ ...base, emulatorHost: "127.0.0.1:8085", emulatorUp: true })).toEqual({
      action: "attach",
      emulatorHost: "127.0.0.1:8085",
      chrome: "/chrome",
    });
  });

  it("skips when FIRESTORE_EMULATOR_HOST is set but nothing answers", () => {
    const plan = planLighthouse({ ...base, emulatorHost: "127.0.0.1:8085", emulatorUp: false });
    expect(plan).toEqual({ action: "skip", reason: expect.stringContaining("127.0.0.1:8085") });
  });

  it("starts the emulator when Java and firebase-tools are available", () => {
    expect(planLighthouse({ ...base, java: "java", firebase: ["firebase"] })).toEqual({
      action: "emulator",
      java: "java",
      firebase: ["firebase"],
      chrome: "/chrome",
    });
  });

  it("skips without Java or firebase-tools", () => {
    expect(planLighthouse({ ...base, firebase: ["firebase"] })).toEqual({ action: "skip", reason: expect.stringMatching(/Java/) });
    expect(planLighthouse({ ...base, java: "java" })).toEqual({ action: "skip", reason: expect.stringMatching(/firebase-tools/) });
  });
});

describe("previewEnv", () => {
  it("builds the dashboard against the emulator only, with an unreachable forecast URL", () => {
    const env = previewEnv(8085);
    expect(env.VITE_USE_EMULATOR).toBe("1");
    expect(env.VITE_FIRESTORE_EMULATOR_PORT).toBe("8085");
    expect(env.VITE_FIRESTORE_DATABASE).toBe("aira");
    expect(env.VITE_FIREBASE_PROJECT_ID).toMatch(/^demo-/);
    expect(env.VITE_AIRA_LIVE_HTTP).toBe("http://127.0.0.1:9");
  });
});

describe("summarize", () => {
  it("passes only when every page meets the minimum", () => {
    const s = summarize(
      [
        { page: "#/stock", report: report(0.98) },
        { page: "#/today", report: report(0.95) },
      ],
      0.95,
    );
    expect(s.ok).toBe(true);
    expect(s.lines).toEqual(["#/stock: 0.98 PASS", "#/today: 0.95 PASS"]);
  });

  it("fails when any page is below the minimum, has no report, or Lighthouse failed", () => {
    const s = summarize(
      [
        { page: "#/stock", report: report(0.98) },
        { page: "#/orders", report: report(0.9) },
        { page: "#/audit", report: null, error: "Lighthouse exited 1" },
      ],
      0.95,
    );
    expect(s.ok).toBe(false);
    expect(s.lines).toEqual(["#/stock: 0.98 PASS", "#/orders: 0.9 FAIL", "#/audit: no report (Lighthouse exited 1) FAIL"]);
  });

  it("says why a page has no score when Lighthouse could not load it", () => {
    const broken = { runtimeError: { code: "CHROME_INTERSTITIAL_ERROR", message: "Chrome prevented page load" }, categories: { accessibility: { score: null } } };
    const s = summarize([{ page: "#/stock", report: broken }], 0.95);
    expect(s.ok).toBe(false);
    expect(s.lines).toEqual(["#/stock: no score (CHROME_INTERSTITIAL_ERROR) FAIL"]);
  });

  it("fails an empty run", () => {
    expect(summarize([], 0.95).ok).toBe(false);
  });
});
