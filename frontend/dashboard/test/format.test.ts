import { describe, it, expect } from "vitest";
import {
  formatINR, formatPrice, formatCount, formatQty, formatLineQty, IST_OFFSET_MS, startOfDayIST, daysUntil, formatTimeIST, stateLabel, expiryLabel,
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

describe("formatPrice", () => {
  it("is formatINR for whole rupees and shows paise only when there are some", () => {
    expect(formatPrice(1100)).toBe("₹1,100");
    expect(formatPrice(110000)).toBe("₹1,10,000");
    expect(formatPrice(24.5)).toBe("₹24.50");
    expect(formatPrice(25.25)).toBe("₹25.25");
  });
});

describe("formatCount", () => {
  it("prints whole counts as they are and anything else to one decimal", () => {
    expect(formatCount(30)).toBe("30");
    expect(formatCount(2.333333)).toBe("2.3");
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

describe("formatLineQty", () => {
  // The backend stores order lines in the product's selling unit: litres for milk and curd, else packs/jars/boxes.
  it("adds the pack count to litres of a pouch product when the litres are whole packs", () => {
    expect(formatLineQty(20, "L", 0.5)).toBe("20 L (40 packs)");
    expect(formatLineQty(0.5, "L", 0.5)).toBe("0.5 L (1 pack)");
    expect(formatLineQty(0.6, "L", 0.2)).toBe("0.6 L (3 packs)"); // 0.6 / 0.2 is 2.9999… in floating point
  });
  it("shows litres alone for a 1 L pack, when the litres are not whole packs, or with no pack size", () => {
    expect(formatLineQty(30, "L", 1)).toBe("30 L");
    expect(formatLineQty(3, "L", 2)).toBe("3 L");
    expect(formatLineQty(12, "L")).toBe("12 L");
  });
  it("shows packs, jars and boxes as they are, whatever their pack size", () => {
    expect(formatLineQty(25, "pack")).toBe("25 packs");
    expect(formatLineQty(10, "pack", 0.2)).toBe("10 packs");
    expect(formatLineQty(3, "jar", 0.2)).toBe("3 jars");
    expect(formatLineQty(1, "box")).toBe("1 box");
  });
  it("shows a bare number when the unit is unknown", () => {
    expect(formatLineQty(12, undefined)).toBe("12");
    expect(formatLineQty(4, "crate")).toBe("4");
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
