import { describe, it, expect } from "vitest";
import {
  formatINR, formatQty, IST_OFFSET_MS, startOfDayIST, daysUntil, formatTimeIST, stateLabel, expiryLabel,
} from "../src/lib/format";

describe("formatINR", () => {
  it("uses Indian digit grouping and no decimals", () => {
    expect(formatINR(2860)).toBe("₹2,860");
    expect(formatINR(100000)).toBe("₹1,00,000");
    expect(formatINR(0)).toBe("₹0");
  });
  it("rounds to whole rupees", () => {
    expect(formatINR(59.6)).toBe("₹60");
  });
});

describe("formatQty", () => {
  it("formats litres, packs and boxes with plurals", () => {
    expect(formatQty(28, "L")).toBe("28 L");
    expect(formatQty(1.5, "L")).toBe("1.5 L");
    expect(formatQty(1, "box")).toBe("1 box");
    expect(formatQty(3, "box")).toBe("3 boxes");
    expect(formatQty(6, "pack")).toBe("6 packs");
    expect(formatQty(1, "pack")).toBe("1 pack");
  });
  it("formats ghee jars with plurals", () => {
    expect(formatQty(1, "jar")).toBe("1 jar");
    expect(formatQty(4, "jar")).toBe("4 jars");
  });
});

describe("IST day maths", () => {
  it("IST is UTC+05:30", () => {
    expect(IST_OFFSET_MS).toBe(330 * 60 * 1000);
  });
  it("00:10 IST belongs to the new IST day", () => {
    // 2026-10-10T18:40Z == 2026-10-11 00:10 IST
    expect(startOfDayIST(Date.UTC(2026, 9, 10, 18, 40))).toBe(Date.UTC(2026, 9, 10, 18, 30));
  });
  it("23:50 IST still belongs to the previous IST day", () => {
    // 2026-10-10T18:20Z == 2026-10-10 23:50 IST
    expect(startOfDayIST(Date.UTC(2026, 9, 10, 18, 20))).toBe(Date.UTC(2026, 9, 9, 18, 30));
  });
  it("daysUntil counts IST calendar days", () => {
    const noonIst = Date.UTC(2026, 9, 10, 6, 30); // 2026-10-10 12:00 IST
    expect(daysUntil("2026-10-10", noonIst)).toBe(0);
    expect(daysUntil("2026-10-11", noonIst)).toBe(1);
    expect(daysUntil("2026-10-09", noonIst)).toBe(-1);
  });
  it("daysUntil uses the IST date just after midnight IST", () => {
    const justAfterMidnightIst = Date.UTC(2026, 9, 10, 18, 40); // 2026-10-11 00:10 IST
    expect(daysUntil("2026-10-11", justAfterMidnightIst)).toBe(0);
  });
  it("formats time in IST regardless of host timezone", () => {
    // 12:35Z == 18:05 IST
    expect(formatTimeIST(Date.UTC(2026, 9, 10, 12, 35))).toMatch(/6:05\s*pm/i);
  });
  it("uses a plain space before am/pm, not a no-break space", () => {
    expect(formatTimeIST(Date.UTC(2026, 9, 10, 12, 35))).toBe("6:05 pm");
    expect(formatTimeIST(Date.UTC(2026, 9, 10, 3, 30))).toBe("9:00 am");
  });
});

describe("labels", () => {
  it("maps order states to plain words", () => {
    expect(stateLabel("DISCREPANCY")).toBe("Discrepancy");
    expect(stateLabel("PAYMENT_CONFIRMED")).toBe("Paid");
    expect(stateLabel("SOMETHING_NEW")).toBe("SOMETHING_NEW");
  });
  it("describes expiry in words", () => {
    expect(expiryLabel(null)).toBeNull();
    expect(expiryLabel(-1)).toBe("Expired");
    expect(expiryLabel(0)).toBe("Expires today");
    expect(expiryLabel(1)).toBe("Expires tomorrow");
    expect(expiryLabel(4)).toBe("Expires in 4 days");
  });
});
