import { describe, it, expect } from "vitest";
import { DEMO_SHOP_ID, SHOP_NAME, resolveAccess } from "../src/lib/access";
import { shopName } from "../src/lib/shopName";

// No Firebase Auth in this build: one owner, one shop. Every visitor reads sunrise_dairy as its owner.
const DEMO = { mode: "demo", shopId: "sunrise_dairy", role: "owner" };

describe("resolveAccess", () => {
  it("the one shop is sunrise_dairy, shown as Sunrise Dairy everywhere", () => {
    expect(DEMO_SHOP_ID).toBe("sunrise_dairy");
    expect(SHOP_NAME).toBe("Sunrise Dairy");
    expect(shopName()).toBe("Sunrise Dairy");
    expect(shopName("someone_else")).toBe("Sunrise Dairy"); // a legacy id argument changes nothing
    expect(shopName(null)).toBe("Sunrise Dairy");
  });
  it("every visitor gets the one shop as its owner, with or without an argument", () => {
    expect(resolveAccess()).toEqual(DEMO);
    expect(resolveAccess({ uid: null, userDoc: null, wantDemo: false })).toEqual(DEMO);
    expect(resolveAccess({ uid: "u1", userDoc: { shopId: "s1", role: "family" }, wantDemo: true })).toEqual(DEMO);
  });
  it("nobody can be pointed at another shop or denied", () => {
    expect(resolveAccess({ uid: null, userDoc: { shopId: "someone_else", role: "owner" }, wantDemo: false }))
      .toEqual(DEMO);
    expect(resolveAccess({ uid: "u4", userDoc: null, wantDemo: false })).toEqual(DEMO);
    expect(resolveAccess({ uid: "u7", userDoc: { shopId: "s1", role: "stranger" }, wantDemo: false })).toEqual(DEMO);
  });
  it("returns a fresh object each time", () => {
    const a = resolveAccess();
    const b = resolveAccess();
    expect(a).toEqual(b);
    expect(a).not.toBe(b);
  });
});
