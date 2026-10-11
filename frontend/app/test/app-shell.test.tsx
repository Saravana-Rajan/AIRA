import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { AppShell, localizeStatus } from "../src/ui/AppShell";
import { SURFACE_LABEL } from "../src/ui/Screen";
import { initialScreen, type ScreenState } from "../src/ui/screenState";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import type { AppView, WiredApp, WireOpts } from "../src/core/wire";

let root: HTMLElement;
beforeEach(() => { root = document.createElement("div"); document.body.appendChild(root); });
afterEach(() => { act(() => render(null, root)); root.remove(); });

function fakeWired(lang: AppView["lang"] = "en") {
  let v: AppView = { screen: initialScreen, level: 0, progress: null, confirmPending: false, online: true, lang };
  const subs = new Set<(v: AppView) => void>();
  const w = {
    onTalk: vi.fn(),
    onStop: vi.fn(),
    dispose: vi.fn(),
    view: {
      get: () => v,
      subscribe: (fn: (v: AppView) => void) => { subs.add(fn); return () => { subs.delete(fn); }; },
    },
  };
  const push = (patch: Partial<AppView>) => { v = { ...v, ...patch }; subs.forEach((fn) => fn(v)); };
  return { w: w as unknown as WiredApp, raw: w, push };
}

const startBtn = () => root.querySelector<HTMLButtonElement>("button.onboard__start")!;

describe("localizeStatus", () => {
  const at = (kind: ScreenState["status"]["kind"], label: string): ScreenState => ({ ...initialScreen, status: { kind, label } });
  it("translates the reducer's standard chip labels and the live count", () => {
    expect(localizeStatus(at("ready", "Ready"), "ta", null).status.label).toBe("தயார்");
    expect(localizeStatus(at("warning", "Offline"), "hi", null).status.label).toBe("ऑफ़लाइन");
    expect(localizeStatus(at("counting", "Counting 12 / 30"), "en", { counted: 12, expected: 30 }).status.label).toBe("Counting 12 / 30");
    expect(localizeStatus(at("counting", "Counting 12 / 30"), "ta", { counted: 12, expected: 30 }).status.label).toBe("எண்ணுகிறேன் 12 / 30");
  });
  it("translates the warning and confirm chips, the count outcome and the task card (ta/hi/en rule)", () => {
    expect(localizeStatus(at("warning", "Mismatch"), "ta", null).status.label).toBe("பொருந்தவில்லை");
    expect(localizeStatus(at("warning", "Not sure"), "hi", null).status.label).toBe("पक्का नहीं");
    expect(localizeStatus(at("connecting", "Reconnecting"), "ta", null).status.label).toBe("மீண்டும் இணைக்கிறது");
    expect(localizeStatus(at("listening", "Yes or no?"), "ta", null).status.label).toBe("ஆமா இல்லையா?");
    expect(localizeStatus(at("warning", "3 short"), "hi", null).status.label).toBe("3 कम");
    expect(localizeStatus(at("warning", "2 extra"), "ta", null).status.label).toBe("2 கூடுதல்");
    expect(localizeStatus(at("warning", "3 short"), "en", null).status.label).toBe("3 short");
    const counting: ScreenState = { ...initialScreen, task: { title: "Delivery", detail: "12 / 30 counted", tone: "neutral" } };
    expect(localizeStatus(counting, "ta", null).task).toEqual({ title: "டெலிவரி", detail: "12 / 30 எண்ணப்பட்டது", tone: "neutral" });
    const confirm: ScreenState = { ...initialScreen, task: { title: "Confirm sale", detail: "Sell 2?", tone: "neutral" } };
    expect(localizeStatus(confirm, "hi", null).task).toEqual({ title: "बिक्री पक्का करें?", detail: "Sell 2?", tone: "neutral" });
    const state: ScreenState = { ...initialScreen, task: { title: "Payment", detail: "150", tone: "ok" } };
    expect(localizeStatus(state, "ta", null).task!.title).toBe("பணம்");
    expect(localizeStatus(state, "en", null)).toBe(state); // nothing to change: same object
  });
  it("leaves labels it has no translation for", () => {
    expect(localizeStatus(at("warning", "Something else"), "ta", null).status.label).toBe("Something else");
  });
});

describe("AppShell", () => {
  it("shows onboarding first and wires nothing before the Start tap", () => {
    const wire = vi.fn();
    act(() => { render(<AppShell wire={wire} languages={["en"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
    expect(startBtn()).not.toBeNull();
    expect(wire).not.toHaveBeenCalled();
  });

  it("the Start tap wires the app synchronously, then shows the one gesture surface", () => {
    const f = fakeWired();
    const wire = vi.fn((_o: WireOpts) => f.w);
    const onWired = vi.fn();
    act(() => { render(<AppShell wire={wire} onWired={onWired} wsUrl="ws://x/ws" languages={["ta-IN"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
    act(() => { startBtn().click(); });
    expect(wire).toHaveBeenCalledTimes(1);
    expect(wire.mock.calls[0][0]).toMatchObject({ wsUrl: "ws://x/ws", settings: { lang: "auto", shopId: "sunrise_dairy", onboarded: true } });
    expect(onWired).toHaveBeenCalledWith(f.w);
    const buttons = root.querySelectorAll("button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].getAttribute("aria-label")).toBe(SURFACE_LABEL);
    // the aria-live region sits outside the button
    const region = root.querySelector('[aria-live="polite"]')!;
    expect(region).not.toBeNull();
    expect(buttons[0].contains(region)).toBe(false);
    expect(root.textContent).toContain("Sunrise Dairy");
  });

  it("redraws from the wired view and routes gestures to the wired handlers", () => {
    const f = fakeWired("ta");
    act(() => { render(<AppShell wire={() => f.w} languages={["ta"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
    act(() => { startBtn().click(); });
    act(() => { f.push({ screen: { ...initialScreen, status: { kind: "ready", label: "Ready" }, airaText: "வணக்கம்" } }); });
    expect(root.querySelector(".status-chip")!.textContent).toContain("தயார்");
    expect(root.textContent).toContain("வணக்கம்");
    act(() => { f.push({ confirmPending: true, screen: { ...initialScreen, task: { title: "Confirm sale", detail: "Sell 2?", tone: "neutral" } } }); });
    expect(root.querySelector('.task-card[data-confirm="true"]')).not.toBeNull();
    const surface = root.querySelector("button")!;
    act(() => { surface.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 })); }); // TalkBack activation
    expect(f.raw.onTalk).toHaveBeenCalledTimes(1);
    act(() => { surface.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Escape" })); });
    expect(f.raw.onStop).toHaveBeenCalledTimes(1);
  });

  it("a mismatch cue in Tamil draws the Tamil chip and task card, not English", () => {
    const f = fakeWired("ta");
    act(() => { render(<AppShell wire={() => f.w} languages={["ta"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
    act(() => { startBtn().click(); });
    act(() => {
      f.push({ screen: {
        ...initialScreen,
        status: { kind: "warning", label: "Mismatch" },
        task: { title: "Delivery", detail: "28 / 30 counted", tone: "warn" },
      } });
    });
    expect(root.querySelector(".status-chip")!.textContent).toContain("பொருந்தவில்லை");
    expect(root.querySelector(".status-chip")!.textContent).not.toContain("Mismatch");
    const card = root.querySelector(".task-card")!.textContent!;
    expect(card).toContain("டெலிவரி");
    expect(card).toContain("28 / 30 எண்ணப்பட்டது");
  });

  it("still shows the app when an extra onWired hook throws", () => {
    const f = fakeWired();
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    act(() => { render(<AppShell wire={() => f.w} onWired={() => { throw new Error("camera module failed"); }} languages={["en"]} />, root); });
    act(() => { startBtn().click(); });
    expect(root.querySelector("button")!.getAttribute("aria-label")).toBe(SURFACE_LABEL);
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it("disposes the wiring when it unmounts", () => {
    const f = fakeWired();
    act(() => { render(<AppShell wire={() => f.w} languages={["en"]} />, root); });
    act(() => { startBtn().click(); });
    act(() => { render(null, root); });
    expect(f.raw.dispose).toHaveBeenCalledTimes(1);
  });
});
