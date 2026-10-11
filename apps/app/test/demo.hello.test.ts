import { describe, it, expect, vi } from "vitest";
import type { ClientMsg } from "@aira/contracts";
import { demoHello, demoUid, DEMO_SHOP_ID } from "../src/demo/hello";
import { connectDemo, demoWireOpts } from "../src/demo/connectDemo";

describe("demo hello (overrides §1: no Firebase Auth, shop sunrise_dairy)", () => {
  it("joins the seeded demo shop without an ID token", () => {
    const h = demoHello("0.1.0", "judge-abc");
    expect(h).toEqual({ t: "hello", uid: "judge-abc", shopId: "sunrise_dairy", lang: "en", verbosity: "short", appVersion: "0.1.0" });
    expect("idToken" in h).toBe(false);
  });

  it("the hello language is only a hint for the first line: the judge's browser locale, English otherwise", () => {
    expect(demoHello("0.1.0", "judge-abc", ["ta-IN"]).lang).toBe("ta");
    expect(demoHello("0.1.0", "judge-abc", ["ja-JP"]).lang).toBe("en");
  });

  it("carries the build-time app key as idToken when VITE_AIRA_APP_KEY is set", () => {
    vi.stubEnv("VITE_AIRA_APP_KEY", "k-demo-key");
    try {
      expect(demoHello("0.1.0", "judge-abc").idToken).toBe("k-demo-key");
    } finally {
      vi.unstubAllEnvs();
    }
    expect(DEMO_SHOP_ID).toBe("sunrise_dairy");
  });

  it("keeps one judge uid per tab and survives blocked storage", () => {
    const mem = new Map<string, string>();
    const store = { getItem: (k: string) => mem.get(k) ?? null, setItem: (k: string, v: string) => void mem.set(k, v) };
    const a = demoUid(store);
    expect(a).toMatch(/^judge-[a-z0-9]+$/);
    expect(demoUid(store)).toBe(a);
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(demoUid(broken)).toMatch(/^judge-[a-z0-9]+$/);
    expect(demoUid(null)).toMatch(/^judge-[a-z0-9]+$/);
  });
});

describe("connectDemo", () => {
  it("restarts the socket with the judge hello", async () => {
    const calls: string[] = [];
    let hello: Extract<ClientMsg, { t: "hello" }> | null = null;
    const socket = {
      close: vi.fn(() => { calls.push("close"); }),
      connect: vi.fn((h: Extract<ClientMsg, { t: "hello" }>) => { calls.push("connect"); hello = h; }),
    };
    await connectDemo("0.1.0", socket);
    expect(calls).toEqual(["close", "connect"]);
    expect(hello).toMatchObject({ t: "hello", shopId: "sunrise_dairy", lang: "en", appVersion: "0.1.0" });
    expect(hello!.uid).toMatch(/^judge-/);
  });

  it("rejects clearly when there is no socket yet", async () => {
    await expect(connectDemo("0.1.0", undefined)).rejects.toThrow(/not connected/i);
  });
});

describe("demoWireOpts", () => {
  it("wires the real app without the owner's hello, prompts or saved data", async () => {
    const o = demoWireOpts("ws://test/ws");
    expect(o.connect).toBe(false);
    expect(o.wsUrl).toBe("ws://test/ws");
    expect(o.settings).toMatchObject({ shopId: "sunrise_dairy", lang: "auto", verbosity: "short", onboarded: true });
    // The owner's saved settings and offline ops on this browser are never touched.
    expect(o.env?.storage).toBeNull();
    expect(await o.env!.queueStore!().all()).toEqual([]);
    // No camera prompt up front: demo media replaces the camera; the mic is asked for on Talk.
    expect(() => o.env!.requestPermissions!()).not.toThrow();
    expect(() => o.env!.requestWakeLock!()).not.toThrow();
  });
});
