import { describe, it, expect, vi } from "vitest";
import { normalizeAngle, readAngle, startOrientation } from "../src/camera/orientation";

/** A fake window with a settable screen.orientation and the two change events we listen to. */
function fakeWin(angle: number) {
  const bags: Record<string, Record<string, Array<() => void>>> = { so: {}, win: {} };
  const add = (bag: Record<string, Array<() => void>>) => (t: string, fn: () => void) => { (bag[t] ??= []).push(fn); };
  const rm = (bag: Record<string, Array<() => void>>) => (t: string, fn: () => void) => { bag[t] = (bag[t] ?? []).filter((f) => f !== fn); };
  const orientation = { angle, addEventListener: add(bags.so), removeEventListener: rm(bags.so) };
  const win = { screen: { orientation }, addEventListener: add(bags.win), removeEventListener: rm(bags.win) };
  const fire = (bag: Record<string, Array<() => void>>, t: string) => (bag[t] ?? []).slice().forEach((f) => f());
  return {
    win,
    set: (a: number) => { orientation.angle = a; },
    fireScreen: () => fire(bags.so, "change"),
    fireWindow: () => fire(bags.win, "orientationchange"),
    screenListeners: () => (bags.so.change ?? []).length,
    windowListeners: () => (bags.win.orientationchange ?? []).length,
  };
}

describe("camera/orientation", () => {
  it("normalizes any degrees to 0/90/180/270", () => {
    expect(normalizeAngle(0)).toBe(0);
    expect(normalizeAngle(90)).toBe(90);
    expect(normalizeAngle(-90)).toBe(270);
    expect(normalizeAngle(271)).toBe(270);
    expect(normalizeAngle(360)).toBe(0);
    expect(normalizeAngle(Number.NaN)).toBe(0);
  });

  it("reads screen.orientation.angle first, then window.orientation, then matchMedia, else 0", () => {
    expect(readAngle({ screen: { orientation: { angle: 90 } } })).toBe(90);
    expect(readAngle({ orientation: -90 })).toBe(270);
    expect(readAngle({ matchMedia: () => ({ matches: true }) })).toBe(90);
    expect(readAngle({ matchMedia: () => ({ matches: false }) })).toBe(0);
    expect(readAngle({})).toBe(0);
  });

  it("does not seed the default portrait, sends on change, never the same twice, and stop() unbinds", () => {
    const f = fakeWin(0);
    const send = vi.fn();
    const stop = startOrientation({ send, win: f.win });
    expect(send).toHaveBeenCalledTimes(0); // portrait (0) is the dashboard's default: no redundant seed
    f.fireScreen(); // still 0: nothing
    expect(send).toHaveBeenCalledTimes(0);
    f.set(90);
    f.fireScreen(); // changed to landscape: one send
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenLastCalledWith({ t: "orientation", angle: 90 });
    f.set(0);
    f.fireScreen(); // back to portrait is a real change from 90: sent explicitly
    expect(send).toHaveBeenCalledTimes(2);
    expect(send).toHaveBeenLastCalledWith({ t: "orientation", angle: 0 });
    expect(f.screenListeners()).toBe(1);
    stop();
    expect(f.screenListeners()).toBe(0);
    expect(f.windowListeners()).toBe(0);
  });

  it("seeds a phone that starts already rotated (landscape) at once", () => {
    const f = fakeWin(90);
    const send = vi.fn();
    startOrientation({ send, win: f.win });
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenLastCalledWith({ t: "orientation", angle: 90 });
  });
});
