import { describe, it, expect } from "vitest";
import { normalize, toMs } from "../src/lib/normalize";

const ts = (ms: number) => ({ toMillis: () => ms });

describe("normalize", () => {
  it("adds id and converts Timestamps (top-level and nested) to millis", () => {
    const out: any = normalize("o1", {
      state: "ORDER_PLACED",
      updatedAt: ts(1000),
      items: [{ sku: "a", at: ts(5) }],
      invoice: { readAt: ts(7), total: 10 },
    });
    expect(out).toEqual({
      id: "o1",
      state: "ORDER_PLACED",
      updatedAt: 1000,
      items: [{ sku: "a", at: 5 }],
      invoice: { readAt: 7, total: 10 },
    });
  });

  it("converts serialised {seconds, nanoseconds} timestamps and keeps date strings as they are", () => {
    const out: any = normalize("i1", {
      updatedAt: { seconds: 2, nanoseconds: 500_000_000 },
      batches: [{ batchId: "b1", qty: 3, expiryDate: "2026-10-14" }],
      note: null,
    });
    expect(out).toEqual({
      id: "i1",
      updatedAt: 2500,
      batches: [{ batchId: "b1", qty: 3, expiryDate: "2026-10-14" }],
      note: null,
    });
  });
});

describe("toMs", () => {
  it("reads millis, ISO strings and Timestamp-like values; anything else is null", () => {
    expect(toMs(1234)).toBe(1234);
    expect(toMs("2026-10-10T06:30:00Z")).toBe(Date.UTC(2026, 9, 10, 6, 30));
    expect(toMs(ts(42))).toBe(42);
    expect(toMs({ seconds: 1, nanoseconds: 0 })).toBe(1000);
    expect(toMs(null)).toBeNull();
    expect(toMs(undefined)).toBeNull();
    expect(toMs("not a date")).toBeNull();
    expect(toMs(Number.NaN)).toBeNull();
  });
});
