// Lighthouse accessibility gate for the AIRA Shop Dashboard (build overrides §6: score >= 0.95).
//
//   node test/lighthouse/a11y-score.mjs <lighthouse-report.json> [min=0.95]
//
// Exits 0 when the report's accessibility score is at or above `min`, and 1 when it is lower or the
// report is missing or broken. `min` may be 0-1 (the Lighthouse JSON scale) or 0-100 (the UI scale).
// On FAIL it lists the audits that did not pass, worst first. Plain ESM: no build step, no deps.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const DEFAULT_MIN = 0.95;

/** { ok, score }: ok only for a numeric score at or above min. score is null for a broken report. */
export function checkScore(report, min) {
  const score = report?.categories?.accessibility?.score;
  if (typeof score !== "number" || Number.isNaN(score)) return { ok: false, score: null };
  const floor = min > 1 ? min / 100 : min;
  return { ok: Number.isFinite(floor) && score >= floor, score };
}

/** The accessibility audits that scored below 1 (not-applicable ones have score null), heaviest first. */
export function failedAudits(report) {
  const refs = report?.categories?.accessibility?.auditRefs;
  const audits = report?.audits;
  if (!Array.isArray(refs) || !audits || typeof audits !== "object") return [];
  const failed = [];
  for (const ref of refs) {
    const audit = audits[ref?.id];
    if (typeof audit?.score !== "number" || audit.score >= 1) continue;
    failed.push({ id: ref.id, title: String(audit.title ?? ref.id), weight: Number(ref.weight) || 0 });
  }
  return failed.sort((a, b) => b.weight - a.weight);
}

function main(argv) {
  const [file, minArg = String(DEFAULT_MIN)] = argv;
  if (!file) {
    console.error("Usage: node test/lighthouse/a11y-score.mjs <lighthouse-report.json> [min=0.95]");
    return 1;
  }
  let report;
  try {
    report = JSON.parse(readFileSync(file, "utf8"));
  } catch (err) {
    console.error(`Lighthouse accessibility score: unreadable report ${file} (${err.message}) → FAIL`);
    return 1;
  }
  const result = checkScore(report, Number(minArg));
  console.log(`Lighthouse accessibility score: ${result.score} (min ${minArg}) → ${result.ok ? "PASS" : "FAIL"}`);
  if (!result.ok) {
    for (const a of failedAudits(report)) console.log(`  - ${a.id} (weight ${a.weight}): ${a.title}`);
  }
  return result.ok ? 0 : 1;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  process.exit(main(process.argv.slice(2)));
}
