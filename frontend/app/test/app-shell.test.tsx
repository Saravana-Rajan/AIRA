import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { AppShell, localizeStatus } from "../src/ui/AppShell";
import { SURFACE_LABEL } from "../src/ui/Screen";
import { initialScreen, type ScreenState } from "../src/ui/screenState";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import type { AppView, WiredApp, WireOpts } from "../src/core/wire";
import type { DeviceCheckProps } from "../src/check/DeviceCheck";

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
    startListening: vi.fn(async () => true),
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
    act(() => { render(<AppShell deviceCheck={null} wire={wire} languages={["en"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
    expect(startBtn()).not.toBeNull();
    expect(wire).not.toHaveBeenCalled();
  });

  it("the Start tap wires the app synchronously, then shows the one gesture surface", () => {
    const f = fakeWired();
    const wire = vi.fn((_o: WireOpts) => f.w);
    const onWired = vi.fn();
    act(() => { render(<AppShell deviceCheck={null} wire={wire} onWired={onWired} wsUrl="ws://x/ws" languages={["ta-IN"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
    act(() => { startBtn().click(); });
    expect(wire).toHaveBeenCalledTimes(1);
    expect(wire.mock.calls[0][0]).toMatchObject({ wsUrl: "ws://x/ws", settings: { lang: "auto", onboarded: true } });
    expect(onWired).toHaveBeenCalledWith(f.w);
    // ONE gesture surface; the only other button is the helper's "Hide camera" toggle, outside it
    const buttons = root.querySelectorAll("button");
    expect(buttons).toHaveLength(2);
    expect(buttons[0].getAttribute("aria-label")).toBe(SURFACE_LABEL);
    expect(buttons[1].classList.contains("cam-toggle")).toBe(true);
    expect(buttons[0].contains(buttons[1])).toBe(false);
    // the aria-live region sits outside the button
    const region = root.querySelector('[aria-live="polite"]')!;
    expect(region).not.toBeNull();
    expect(buttons[0].contains(region)).toBe(false);
    expect(root.textContent).toContain("Sunrise Dairy");
  });

  it("redraws from the wired view and routes gestures to the wired handlers", () => {
    const f = fakeWired("ta");
    act(() => { render(<AppShell deviceCheck={null} wire={() => f.w} languages={["ta"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
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
    act(() => { render(<AppShell deviceCheck={null} wire={() => f.w} languages={["ta"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
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
    act(() => { render(<AppShell deviceCheck={null} wire={() => f.w} onWired={() => { throw new Error("camera module failed"); }} languages={["en"]} />, root); });
    act(() => { startBtn().click(); });
    expect(root.querySelector("button")!.getAttribute("aria-label")).toBe(SURFACE_LABEL);
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it("disposes the wiring when it unmounts", () => {
    const f = fakeWired();
    act(() => { render(<AppShell deviceCheck={null} wire={() => f.w} languages={["en"]} />, root); });
    act(() => { startBtn().click(); });
    act(() => { render(null, root); });
    expect(f.raw.dispose).toHaveBeenCalledTimes(1);
  });
});

describe("AppShell: hands-free start", () => {
  const ONBOARDED = { ...DEFAULT_SETTINGS, onboarded: true };
  const settle = async () => { for (let i = 0; i < 10; i++) await act(async () => { await Promise.resolve(); }); };

  it("an onboarded owner opening the app skips Start: wires, listens and shows the surface at once", async () => {
    const f = fakeWired("ta");
    f.raw.startListening = vi.fn(async () => true);
    const wire = vi.fn((_o: WireOpts) => f.w);
    const onWired = vi.fn();
    act(() => { render(<AppShell deviceCheck={null} wire={wire} onWired={onWired} autoStart languages={["ta-IN"]} initial={ONBOARDED} />, root); });
    await settle();
    expect(wire).toHaveBeenCalledTimes(1);
    expect(wire.mock.calls[0][0].settings).toMatchObject({ onboarded: true });
    expect(onWired).toHaveBeenCalledWith(f.w);
    expect(f.raw.startListening).toHaveBeenCalledTimes(1);
    expect(root.querySelector("button.onboard__start")).toBeNull();
    expect(root.querySelector("button")!.getAttribute("aria-label")).toBe(SURFACE_LABEL);
    expect(f.raw.dispose).not.toHaveBeenCalled();
  });

  it("falls back to the Start screen, saying 'Tap anywhere to start', when the mic auto-start is blocked", async () => {
    const f = fakeWired();
    f.raw.startListening = vi.fn(async () => false); // getUserMedia rejected: no user gesture yet
    const second = fakeWired();
    second.raw.startListening = vi.fn(async () => true);
    const wire = vi.fn().mockReturnValueOnce(f.w).mockReturnValueOnce(second.w);
    act(() => { render(<AppShell deviceCheck={null} wire={wire} autoStart languages={["en"]} initial={ONBOARDED} />, root); });
    await settle();
    expect(f.raw.dispose).toHaveBeenCalledTimes(1); // the gesture-less wiring is thrown away
    expect(startBtn()).not.toBeNull();
    const note = root.querySelector('[role="status"].autostart-note')!;
    expect(note.textContent).toBe("Tap anywhere to start.");
    // "anywhere": a tap outside the Start button starts too
    act(() => { root.querySelector<HTMLElement>(".autostart-fallback")!.click(); });
    expect(wire).toHaveBeenCalledTimes(2);
    expect(root.querySelector("button")!.getAttribute("aria-label")).toBe(SURFACE_LABEL);
    // The real tap goes through the normal Start path, which now also opens the mic (it used to wait for a
    // double-tap, so on iPhone, where every start is a tap, AIRA never heard the owner).
    expect(second.raw.startListening).toHaveBeenCalledTimes(1);
  });

  it("a Start tap opens the mic right away (iPhone has no hands-free start: every start is this tap)", async () => {
    const f = fakeWired();
    const wire = vi.fn((_o: WireOpts) => f.w);
    act(() => { render(<AppShell deviceCheck={null} wire={wire} languages={["en"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
    act(() => { startBtn().click(); });
    expect(wire).toHaveBeenCalledTimes(1);
    expect(f.raw.startListening).toHaveBeenCalledTimes(1); // called inside the tap, not on a later double-tap
    await settle();
    expect(f.raw.dispose).not.toHaveBeenCalled();
  });

  it("if the mic refuses after a Start tap, the app stays wired (a double-tap can still open it)", async () => {
    const f = fakeWired();
    f.raw.startListening = vi.fn(async () => false);
    const wire = vi.fn((_o: WireOpts) => f.w);
    act(() => { render(<AppShell deviceCheck={null} wire={wire} languages={["en"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
    act(() => { startBtn().click(); });
    await settle();
    expect(f.raw.dispose).not.toHaveBeenCalled();
    expect(root.querySelector("button")!.getAttribute("aria-label")).toBe(SURFACE_LABEL);
  });

  it("the Start tap itself still works on the fallback screen, once", async () => {
    const f = fakeWired();
    f.raw.startListening = vi.fn(async () => false);
    const second = fakeWired();
    const wire = vi.fn().mockReturnValueOnce(f.w).mockReturnValue(second.w);
    act(() => { render(<AppShell deviceCheck={null} wire={wire} autoStart languages={["en"]} initial={ONBOARDED} />, root); });
    await settle();
    act(() => { startBtn().click(); });
    expect(wire).toHaveBeenCalledTimes(2);
  });

  it("the first launch ever still shows Start and wires nothing, even from the ?start=1 shortcut", async () => {
    const wire = vi.fn();
    const firstLaunch = { ...DEFAULT_SETTINGS, onboarded: false };
    act(() => { render(<AppShell deviceCheck={null} wire={wire} autoStart languages={["en"]} initial={firstLaunch} />, root); });
    await settle();
    expect(startBtn()).not.toBeNull();
    expect(wire).not.toHaveBeenCalled();
    expect(root.querySelector(".autostart-note")).toBeNull();
  });

  it("without the prop, a jsdom tab (not installed, no ?start=1) shows Start even when onboarded", async () => {
    const wire = vi.fn();
    act(() => { render(<AppShell deviceCheck={null} wire={wire} languages={["en"]} initial={ONBOARDED} />, root); });
    await settle();
    expect(startBtn()).not.toBeNull();
    expect(wire).not.toHaveBeenCalled();
  });
});

describe("AppShell: the device check comes before the conversation", () => {
  const ONBOARDED = { ...DEFAULT_SETTINGS, onboarded: true };
  const settle = async () => { for (let i = 0; i < 10; i++) await act(async () => { await Promise.resolve(); }); };

  function fakeCheck() {
    const seen: { props: DeviceCheckProps | null; renders: number } = { props: null, renders: 0 };
    const C = (p: DeviceCheckProps) => { seen.props = p; seen.renders++; return <div class="fake-check" />; };
    return { C, seen };
  }

  it("the Start tap shows the check and wires nothing until the check passes", () => {
    const f = fakeWired();
    const wire = vi.fn((_o: WireOpts) => f.w);
    const check = fakeCheck();
    act(() => { render(<AppShell deviceCheck={check.C} wire={wire} languages={["ta-IN"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
    act(() => { startBtn().click(); });
    expect(root.querySelector(".fake-check")).not.toBeNull();
    expect(wire).not.toHaveBeenCalled();
    expect(check.seen.props).toMatchObject({ lang: "ta", autoContinue: false });
    act(() => { check.seen.props!.onPass(); });
    expect(wire).toHaveBeenCalledTimes(1);
    expect(f.raw.startListening).toHaveBeenCalledTimes(1); // "Tap anywhere to start talking": the mic opens now
    expect(root.querySelector(".fake-check")).toBeNull();
    expect(root.querySelector("button")!.getAttribute("aria-label")).toBe(SURFACE_LABEL);
  });

  it("a hands-free start runs the check first, then wires and listens on its own", async () => {
    const f = fakeWired();
    const wire = vi.fn((_o: WireOpts) => f.w);
    const check = fakeCheck();
    act(() => { render(<AppShell deviceCheck={check.C} wire={wire} autoStart languages={["en"]} initial={ONBOARDED} />, root); });
    await settle();
    expect(wire).not.toHaveBeenCalled();
    expect(check.seen.props).toMatchObject({ autoContinue: true });
    act(() => { check.seen.props!.onPass(); });
    await settle();
    expect(wire).toHaveBeenCalledTimes(1);
    expect(f.raw.startListening).toHaveBeenCalledTimes(1);
  });

  it("after one pass, the fallback Start tap goes straight to the conversation", async () => {
    const first = fakeWired();
    first.raw.startListening = vi.fn(async () => false);       // the mic needs a tap after all
    const second = fakeWired();
    const wire = vi.fn().mockReturnValueOnce(first.w).mockReturnValueOnce(second.w);
    const check = fakeCheck();
    act(() => { render(<AppShell deviceCheck={check.C} wire={wire} autoStart languages={["en"]} initial={ONBOARDED} />, root); });
    await settle();
    act(() => { check.seen.props!.onPass(); });
    await settle();
    const rendersBefore = check.seen.renders;
    act(() => { startBtn().click(); });
    expect(wire).toHaveBeenCalledTimes(2);
    expect(check.seen.renders).toBe(rendersBefore);             // not tested again on this page
  });
});
