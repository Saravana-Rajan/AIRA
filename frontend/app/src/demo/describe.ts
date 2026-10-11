import type { CueName, ServerMsg } from "@aira/contracts";

const CUE_LINES: Partial<Record<CueName, string>> = {
  mismatch: "⚠ Mismatch",
  match: "✓ Match",
  unsure: "? Not sure",
  warn: "⚠ Warning",
};

/** One human-readable line per server message for the judge panel; null = don't show. */
export function describe(m: ServerMsg): string | null {
  switch (m.t) {
    case "transcript":
      if (!m.final) return null;
      return `${m.role === "aira" ? "AIRA" : "You"}: ${m.text}`;
    case "count":
      return `Counting ${m.sku}: ${m.counted}${m.expected === null ? "" : ` / ${m.expected}`}${m.final ? " (final)" : ""}`;
    case "state":
      return `${m.entity} ${m.id} → ${m.state}: ${m.summary}`;
    case "confirm":
      return `Confirm? ${m.prompt}`;
    case "target":
      return `Guiding to ${m.label}`;
    case "cue":
      return CUE_LINES[m.cue] ?? null;
    case "error":
      return `Error (${m.code}): ${m.say}`;
    default:
      return null;
  }
}
