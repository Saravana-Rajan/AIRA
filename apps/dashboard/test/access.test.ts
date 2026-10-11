import { describe, it, expect } from "vitest";
import { DEMO_SHOP_ID, resolveAccess } from "../src/lib/access";

// No Firebase Auth in this build: one owner, one shop. Visitors read sunrise_dairy as its owner.
const DEMO = { mode: "demo", shopId: "sunrise_dairy", role: "owner" };
const DENIED = { mode: "denied", reason: "No shop is linked to this account." };

describe("resolveAccess", () => {
  it("the demo shop is sunrise_dairy", () => {
    expect(DEMO_SHOP_ID).toBe("sunrise_dairy");
  });
  it("signed-out visitors always get the demo shop as its single owner", () => {
    expect(resolveAccess({ uid: null, userDoc: null, wantDemo: false })).toEqual(DEMO);
  });
  it("signed-out visitors cannot be pointed at another shop", () => {
    expect(resolveAccess({ uid: null, userDoc: { shopId: "someone_else", role: "owner" }, wantDemo: false }))
      .toEqual(DEMO);
  });
  it("?demo=1 forces demo even when signed in", () => {
    expect(resolveAccess({ uid: "u1", userDoc: { shopId: "s1", role: "owner" }, wantDemo: true }))
      .toEqual(DEMO);
  });
  it("owner or family with a shop get family mode for THEIR shop only, keeping their role", () => {
    expect(resolveAccess({ uid: "u1", userDoc: { shopId: "s1", role: "family" }, wantDemo: false }))
      .toEqual({ mode: "family", shopId: "s1", uid: "u1", role: "family" });
    expect(resolveAccess({ uid: "u2", userDoc: { shopId: "s9", role: "owner" }, wantDemo: false }))
      .toEqual({ mode: "family", shopId: "s9", uid: "u2", role: "owner" });
  });
  it("role demo maps to the demo shop", () => {
    expect(resolveAccess({ uid: "u3", userDoc: { shopId: "x", role: "demo" }, wantDemo: false }))
      .toEqual(DEMO);
  });
  it("signed-in users without a linked shop are denied, not shown another shop", () => {
    expect(resolveAccess({ uid: "u4", userDoc: null, wantDemo: false })).toEqual(DENIED);
    expect(resolveAccess({ uid: "u5", userDoc: { role: "owner" }, wantDemo: false })).toEqual(DENIED);
    expect(resolveAccess({ uid: "u6", userDoc: { shopId: "", role: "owner" }, wantDemo: false })).toEqual(DENIED);
  });
  it("an unknown role is denied", () => {
    expect(resolveAccess({ uid: "u7", userDoc: { shopId: "s1", role: "stranger" }, wantDemo: false }))
      .toEqual(DENIED);
  });
  it("returns a fresh object each time, so callers cannot mutate a shared result", () => {
    const a = resolveAccess({ uid: null, userDoc: null, wantDemo: false });
    const b = resolveAccess({ uid: null, userDoc: null, wantDemo: false });
    expect(a).not.toBe(b);
  });
});
