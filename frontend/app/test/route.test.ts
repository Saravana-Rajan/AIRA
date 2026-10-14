// Earphones routing (voice/route): the OS sends AIRA's voice to a paired headset; this only notices the
// headset coming and going so core/wire can reopen the mic and warn the owner once when it drops.
import { describe, it, expect, vi } from "vitest";
import { hasHeadset, watchHeadset, type DeviceSource } from "../src/voice/route";

const dev = (label: string, kind: MediaDeviceKind = "audioinput", deviceId = "d"): MediaDeviceInfo =>
  ({ deviceId, label, kind, groupId: "g", toJSON() { return {}; } }) as MediaDeviceInfo;

describe("hasHeadset", () => {
  it("recognises Bluetooth, wired and branded earphones on either side (input or output)", () => {
    expect(hasHeadset([dev("Bluetooth headset")])).toBe(true);
    expect(hasHeadset([dev("boAt Rockerz 255 (Bluetooth)")])).toBe(true);
    expect(hasHeadset([dev("AirPods Pro", "audiooutput")])).toBe(true);
    expect(hasHeadset([dev("Galaxy Buds2 Pro", "audiooutput")])).toBe(true);
    expect(hasHeadset([dev("Wired headset")])).toBe(true);
    expect(hasHeadset([dev("Headphones", "audiooutput")])).toBe(true);
    expect(hasHeadset([dev("Hands-Free AG Audio")])).toBe(true);
    expect(hasHeadset([dev("Speakerphone"), dev("Headset earpiece")])).toBe(true);
  });
  it("is false for the phone alone, for empty labels (no permission yet) and for an empty list", () => {
    expect(hasHeadset([dev("Speakerphone"), dev("Phone microphone"), dev("Built-in speaker", "audiooutput")])).toBe(false);
    expect(hasHeadset([dev("iPhone Microphone")])).toBe(false);
    expect(hasHeadset([dev(""), dev("", "audiooutput")])).toBe(false);
    expect(hasHeadset([])).toBe(false);
  });
  it("ignores video inputs, whatever they are called", () => {
    expect(hasHeadset([dev("Bluetooth camera headset", "videoinput")])).toBe(false);
  });
});

/* ---------- watchHeadset with a fake navigator.mediaDevices ---------- */

const PHONE = [dev("Speakerphone"), dev("Built-in speaker", "audiooutput")];
const WITH_BT = [...PHONE, dev("Bluetooth headset"), dev("Bluetooth headset", "audiooutput")];

function fakeDevices(initial: MediaDeviceInfo[]) {
  let list = initial;
  const listeners = new Set<() => void>();
  const md = {
    enumerateDevices: vi.fn(async () => list),
    addEventListener: vi.fn((_t: "devicechange", fn: () => void) => { listeners.add(fn); }),
    removeEventListener: vi.fn((_t: "devicechange", fn: () => void) => { listeners.delete(fn); }),
  };
  const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
  return {
    md: md as unknown as DeviceSource & typeof md,
    listeners,
    set: (l: MediaDeviceInfo[]) => { list = l; },
    change: async () => { listeners.forEach((fn) => fn()); await flush(); },
    flush,
  };
}

describe("watchHeadset", () => {
  it("seeds from the first enumeration and reports nothing until the answer changes", async () => {
    const f = fakeDevices(WITH_BT);
    const seen: boolean[] = [];
    watchHeadset(f.md, (p) => seen.push(p));
    await f.flush();
    expect(f.md.enumerateDevices).toHaveBeenCalledTimes(1);
    expect(seen).toEqual([]);
    await f.change(); // a devicechange with the same list: still a headset
    await f.change();
    expect(seen).toEqual([]);
  });

  it("true→false reports once; true→false→true→false reports three transitions", async () => {
    const f = fakeDevices(WITH_BT);
    const seen: boolean[] = [];
    watchHeadset(f.md, (p) => seen.push(p));
    await f.flush();
    f.set(PHONE);
    await f.change();
    expect(seen).toEqual([false]);
    await f.change(); // still gone: nothing new
    expect(seen).toEqual([false]);
    f.set(WITH_BT);
    await f.change();
    f.set(PHONE);
    await f.change();
    expect(seen).toEqual([false, true, false]);
  });

  it("dispose removes the listener and drops a result still in flight", async () => {
    const f = fakeDevices(WITH_BT);
    const seen: boolean[] = [];
    const dispose = watchHeadset(f.md, (p) => seen.push(p));
    await f.flush();
    expect(f.listeners.size).toBe(1);
    f.set(PHONE);
    f.listeners.forEach((fn) => fn()); // enumeration starts...
    dispose(); // ...and the watcher is gone before it lands
    await f.flush();
    expect(seen).toEqual([]);
    expect(f.md.removeEventListener).toHaveBeenCalledWith("devicechange", expect.any(Function));
    expect(f.listeners.size).toBe(0);
    dispose(); // idempotent
  });

  it("keeps the last answer when an enumeration fails", async () => {
    const f = fakeDevices(WITH_BT);
    const seen: boolean[] = [];
    watchHeadset(f.md, (p) => seen.push(p));
    await f.flush();
    f.md.enumerateDevices.mockRejectedValueOnce(new Error("blocked"));
    await f.change();
    expect(seen).toEqual([]);
    f.set(PHONE);
    await f.change();
    expect(seen).toEqual([false]);
  });

  it("is a no-op without mediaDevices or without devicechange listeners", async () => {
    expect(() => watchHeadset(null, () => {})()).not.toThrow();
    expect(() => watchHeadset(undefined, () => {})()).not.toThrow();
    const seen: boolean[] = [];
    const only = { enumerateDevices: vi.fn(async () => WITH_BT) };
    const dispose = watchHeadset(only, (p) => seen.push(p));
    for (let i = 0; i < 10; i++) await Promise.resolve();
    expect(only.enumerateDevices).toHaveBeenCalledTimes(1);
    expect(() => dispose()).not.toThrow();
    expect(seen).toEqual([]);
  });
});
