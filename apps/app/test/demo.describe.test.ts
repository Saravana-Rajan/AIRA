import { describe as d, it, expect } from "vitest";
import { describe } from "../src/demo/describe";

d("judge panel lines", () => {
  it("formats AIRA and user transcripts (final only)", () => {
    expect(describe({ t: "transcript", role: "aira", text: "28 counted. 2 short?", final: true })).toBe("AIRA: 28 counted. 2 short?");
    expect(describe({ t: "transcript", role: "user", text: "count", final: false })).toBeNull();
    expect(describe({ t: "transcript", role: "user", text: "Count the curd", final: true })).toBe("You: Count the curd");
  });

  it("formats live counts and business states", () => {
    expect(describe({ t: "count", sku: "sakthi-curd-1l", counted: 7, expected: 10, final: false, confidence: 0.9 }))
      .toBe("Counting sakthi-curd-1l: 7 / 10");
    expect(describe({ t: "state", entity: "sale", id: "s1", state: "SALE_COMPLETED", summary: "2 L curd ₹120" }))
      .toBe("sale s1 → SALE_COMPLETED: 2 L curd ₹120");
  });

  it("flags mismatch cues and errors", () => {
    expect(describe({ t: "cue", cue: "mismatch" })).toBe("⚠ Mismatch");
    expect(describe({ t: "error", code: "camera", say: "Too dark" })).toBe("Error (camera): Too dark");
    expect(describe({ t: "audio", pcm24: "AAAA" })).toBeNull();
  });

  it("marks final counts, open-ended counts, confirms and guidance targets", () => {
    expect(describe({ t: "count", sku: "sakthi_curd_1l", counted: 10, expected: null, final: true, confidence: 0.95 }))
      .toBe("Counting sakthi_curd_1l: 10 (final)");
    expect(describe({ t: "confirm", id: "c1", kind: "order", prompt: "Send 10 L to Sakthi?" })).toBe("Confirm? Send 10 L to Sakthi?");
    expect(describe({ t: "target", frameId: "f1", label: "Sakthi curd 1 L", point: [500, 500], confidence: 0.8 }))
      .toBe("Guiding to Sakthi curd 1 L");
    expect(describe({ t: "cue", cue: "match" })).toBe("✓ Match");
    expect(describe({ t: "cue", cue: "ack" })).toBeNull();
    expect(describe({ t: "ready", sessionId: "s", resume: "r", mock: false })).toBeNull();
    expect(describe({ t: "request_frame", purpose: "count" })).toBeNull();
  });
});
