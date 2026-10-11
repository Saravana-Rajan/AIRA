// The browser e2e runner's decisions (frontend/app/e2e/prereqs.mjs) are pure, so they are checked here
// without a browser, a Python toolchain or a network.
import { describe, expect, it } from "vitest";
import { findBrowser, planRun } from "../e2e/prereqs.mjs";

describe("findBrowser", () => {
  it("prefers Playwright's own Chromium (no channel) when it is installed", () => {
    expect(findBrowser({ env: {}, chromiumPath: "/pw/chrome", exists: (p) => p === "/pw/chrome" })).toEqual({
      channel: undefined, name: "Playwright Chromium",
    });
  });

  it("falls back to the machine's Google Chrome", () => {
    expect(findBrowser({ env: {}, chromiumPath: "/pw/chrome", exists: (p) => p.includes("Google Chrome") })).toEqual({
      channel: "chrome", name: "Google Chrome",
    });
  });

  it("AIRA_E2E_CHANNEL wins", () => {
    expect(findBrowser({ env: { AIRA_E2E_CHANNEL: "msedge" }, chromiumPath: null, exists: () => false })).toEqual({
      channel: "msedge", name: "msedge",
    });
  });

  it("is null when nothing can run", () => {
    expect(findBrowser({ env: {}, chromiumPath: "/pw/chrome", exists: () => false })).toBeNull();
    expect(findBrowser({ env: {}, chromiumPath: null, exists: () => false })).toBeNull();
  });
});

describe("planRun", () => {
  const ok = { hasDeps: true, browser: { channel: undefined, name: "Playwright Chromium" }, uv: "uv" };

  it("runs when the deps, a browser and uv are all there", () => {
    expect(planRun(ok)).toEqual({ action: "run" });
  });

  it("skips, with the reason, when something is missing", () => {
    const skip = (reason: RegExp) => ({ action: "skip", reason: expect.stringMatching(reason) });
    expect(planRun({ ...ok, hasDeps: false })).toEqual(skip(/@playwright\/test/));
    expect(planRun({ ...ok, browser: null })).toEqual(skip(/npx playwright install chromium/));
    expect(planRun({ ...ok, uv: null })).toEqual(skip(/uv/));
  });
});
