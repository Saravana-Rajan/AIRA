import { describe, it, expect } from "vitest";
import { SCENARIOS, DEMO_MEDIA_FILES, DAY_PARTS } from "../src/demo/scenarios";

describe("demo scenarios", () => {
  it("cover the whole Sunrise Dairy loop, in order", () => {
    expect(SCENARIOS.map((s) => s.id)).toEqual([
      "order", "delivery-one", "delivery-crate", "invoice-ok", "invoice-wrong",
      "pay-vendor", "sale-guide", "sale-cash", "sale-upi", "summary",
    ]);
  });

  it("list exactly the media files that must be recorded", () => {
    expect(DEMO_MEDIA_FILES).toEqual([
      "/demo/delivery_one_by_one.mp4", "/demo/crate_sakthi_curd.jpg", "/demo/invoice_ok.jpg",
      "/demo/invoice_wrong_qty.jpg", "/demo/cash_vendor_500x2.jpg", "/demo/sale_guidance.mp4",
      "/demo/cash_customer_100.jpg", "/demo/soundbox_upi_60.wav",
    ]);
  });

  it("every card has a spoken-style prompt", () => {
    for (const s of SCENARIOS) expect(s.prompt.length).toBeGreaterThan(8);
  });

  it("every card has a unique id, a title and a blurb", () => {
    expect(new Set(SCENARIOS.map((s) => s.id)).size).toBe(SCENARIOS.length);
    for (const s of SCENARIOS) {
      expect(s.title.length).toBeGreaterThan(3);
      expect(s.blurb.length).toBeGreaterThan(10);
    }
  });

  it("groups the day into parts that use every scenario once, in order", () => {
    expect(DAY_PARTS.map((p) => p.id)).toEqual(["stock", "counter", "close"]);
    expect(DAY_PARTS.flatMap((p) => p.scenarioIds)).toEqual(SCENARIOS.map((s) => s.id));
    for (const p of DAY_PARTS) expect(p.title.length).toBeGreaterThan(3);
  });

  it("only uses cash or UPI for payments", () => {
    const text = SCENARIOS.map((s) => `${s.title} ${s.blurb} ${s.prompt}`).join(" ").toLowerCase();
    expect(text).not.toMatch(/credit|card payment|udhaar/);
  });
});
