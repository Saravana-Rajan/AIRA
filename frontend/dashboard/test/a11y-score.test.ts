// @vitest-environment node
// The Lighthouse accessibility gate: the dashboard ships only with an accessibility score of 0.95 or more.
import { describe, it, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_MIN, checkScore, failedAudits } from "./lighthouse/a11y-score.mjs";

const SCRIPT = fileURLToPath(new URL("./lighthouse/a11y-score.mjs", import.meta.url));

function runGate(args: string[]) {
  const r = spawnSync(process.execPath, [SCRIPT, ...args], { encoding: "utf8" });
  return { code: r.status, out: `${r.stdout}${r.stderr}` };
}

function writeReport(report: unknown): string {
  const dir = mkdtempSync(join(tmpdir(), "aira-lh-"));
  const file = join(dir, "lh.json");
  writeFileSync(file, typeof report === "string" ? report : JSON.stringify(report));
  return file;
}

const report = (score: unknown) => ({ categories: { accessibility: { score } } });

describe("Lighthouse accessibility gate", () => {
  it("defaults the minimum to 0.95", () => {
    expect(DEFAULT_MIN).toBe(0.95);
  });

  it("passes at or above the minimum", () => {
    expect(checkScore(report(0.97), 0.95)).toEqual({ ok: true, score: 0.97 });
    expect(checkScore(report(0.95), 0.95)).toEqual({ ok: true, score: 0.95 });
  });

  it("fails below the minimum or on a broken report", () => {
    expect(checkScore(report(0.9), 0.95)).toEqual({ ok: false, score: 0.9 });
    expect(checkScore({}, 0.95)).toEqual({ ok: false, score: null });
    expect(checkScore(null, 0.95)).toEqual({ ok: false, score: null });
    expect(checkScore(report(null), 0.95)).toEqual({ ok: false, score: null });
    expect(checkScore(report("0.99"), 0.95)).toEqual({ ok: false, score: null });
  });

  it("accepts the minimum on the 0-100 scale the Lighthouse UI shows", () => {
    expect(checkScore(report(0.96), 95)).toEqual({ ok: true, score: 0.96 });
    expect(checkScore(report(0.94), 95)).toEqual({ ok: false, score: 0.94 });
  });

  it("never passes with an invalid minimum", () => {
    expect(checkScore(report(1), Number.NaN)).toEqual({ ok: false, score: 1 });
  });
});

describe("failedAudits", () => {
  it("lists the accessibility audits that scored below 1, worst first, so a FAIL says what to fix", () => {
    const lh = {
      categories: {
        accessibility: {
          score: 0.88,
          auditRefs: [
            { id: "color-contrast", weight: 7 },
            { id: "label", weight: 7 },
            { id: "th-has-data-cells", weight: 0 },
            { id: "image-alt", weight: 10 },
          ],
        },
      },
      audits: {
        "color-contrast": { id: "color-contrast", title: "Background and foreground colors do not have a sufficient contrast ratio.", score: 0 },
        label: { id: "label", title: "Form elements have associated labels", score: 1 },
        "th-has-data-cells": { id: "th-has-data-cells", title: "Not applicable", score: null },
        "image-alt": { id: "image-alt", title: "Image elements do not have [alt] attributes", score: 0 },
      },
    };
    expect(failedAudits(lh)).toEqual([
      { id: "image-alt", title: "Image elements do not have [alt] attributes", weight: 10 },
      { id: "color-contrast", title: "Background and foreground colors do not have a sufficient contrast ratio.", weight: 7 },
    ]);
  });

  it("is empty for a broken report", () => {
    expect(failedAudits({})).toEqual([]);
    expect(failedAudits(null)).toEqual([]);
  });
});

describe("a11y-score CLI", () => {
  it("exits 0 and prints PASS when the score meets the minimum", () => {
    const r = runGate([writeReport(report(0.98)), "0.95"]);
    expect(r.code).toBe(0);
    expect(r.out).toContain("Lighthouse accessibility score: 0.98 (min 0.95)");
    expect(r.out).toContain("PASS");
  });

  it("defaults the minimum to 0.95 and names the failing audits", () => {
    const lh = {
      categories: { accessibility: { score: 0.94, auditRefs: [{ id: "color-contrast", weight: 7 }] } },
      audits: { "color-contrast": { id: "color-contrast", title: "Low contrast", score: 0 } },
    };
    const r = runGate([writeReport(lh)]);
    expect(r.code).toBe(1);
    expect(r.out).toContain("min 0.95");
    expect(r.out).toContain("FAIL");
    expect(r.out).toContain("color-contrast");
  });

  it("exits 1 with a clear message on a missing or malformed report", () => {
    const missing = runGate([]);
    expect(missing.code).toBe(1);
    expect(missing.out).toMatch(/usage/i);

    const broken = runGate([writeReport("{not json")]);
    expect(broken.code).toBe(1);
    expect(broken.out).toContain("FAIL");
  });
});
