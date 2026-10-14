// frontend/app/test/ui.liveScreen.test.tsx — the full-screen camera layout: the live picture fills the phone
// while the camera runs, the last exchange floats over it as bubbles, the controls stay (as a round button
// row) with aria-labels, "Exit full screen" goes back to the card layout and is remembered, and the detector
// boxes are mapped through the object-fit: cover crop.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { coverFit, drawBoxes, type Ctx2D } from "../src/ui/boxDraw";
import { CameraPreview, FULL_KEY, PREVIEW_KEY, PREVIEW_TEXT, loadFullScreen, saveFullScreen } from "../src/ui/CameraPreview";
import { LIVE_HTML_CLASS, LIVE_TEXT } from "../src/ui/LiveScreen";
import { AppShell } from "../src/ui/AppShell";
import { SURFACE_LABEL } from "../src/ui/Screen";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import { initialScreen } from "../src/ui/screenState";
import { createBoxStore } from "../src/walk/boxes";
import type { AppView, WiredApp } from "../src/core/wire";

let root: HTMLElement;
beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
  try { localStorage.clear(); } catch { /* ignore */ }
});
afterEach(() => {
  act(() => render(null, root));
  root.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function recorder() {
  const calls: { op: string; args: unknown[] }[] = [];
  const ctx: Ctx2D = {
    strokeStyle: "", fillStyle: "", lineWidth: 1, font: "", textBaseline: "alphabetic",
    clearRect: (...args) => calls.push({ op: "clearRect", args }),
    strokeRect: (...args) => calls.push({ op: "strokeRect", args }),
    fillRect: (...args) => calls.push({ op: "fillRect", args }),
    fillText: (...args) => calls.push({ op: "fillText", args }),
    measureText: (t) => ({ width: t.length * 6 }),
  };
  return { ctx, calls };
}

/** A shared camera <video> as capture.ts would hold it, with a fake stream. */
function sharedVideo(w = 720, h = 1280) {
  const stream = { id: "shared", active: true, getTracks: () => [] } as unknown as MediaStream;
  const v = document.createElement("video");
  Object.defineProperty(v, "srcObject", { value: stream, writable: true });
  Object.defineProperty(v, "videoWidth", { value: w });
  Object.defineProperty(v, "videoHeight", { value: h });
  return { v, stream };
}

describe("coverFit (object-fit: cover crop)", () => {
  it("a portrait video in a landscape box: scaled to the width, overflowing top and bottom, centred", () => {
    const r = coverFit({ w: 720, h: 1280 }, { w: 800, h: 400 });
    expect(r.w).toBeCloseTo(800);
    expect(r.h).toBeCloseTo(1422.22, 1);
    expect(r.x).toBeCloseTo(0);
    expect(r.y).toBeCloseTo((400 - 1422.22) / 2, 1);
  });

  it("a landscape video in a portrait box (a phone): scaled to the height, overflowing left and right", () => {
    const r = coverFit({ w: 1280, h: 720 }, { w: 390, h: 844 });
    expect(r.h).toBeCloseTo(844);
    expect(r.w).toBeCloseTo(1500.44, 1);
    expect(r.y).toBeCloseTo(0);
    expect(r.x).toBeCloseTo((390 - 1500.44) / 2, 1);
  });

  it("the same aspect fills the box exactly; a degenerate size falls back to the box", () => {
    expect(coverFit({ w: 300, h: 400 }, { w: 600, h: 800 })).toEqual({ x: 0, y: 0, w: 600, h: 800 });
    expect(coverFit({ w: 0, h: 0 }, { w: 390, h: 844 })).toEqual({ x: 0, y: 0, w: 390, h: 844 });
  });

  it("drawBoxes maps a box through the fit: the video's centre lands on the screen's centre", () => {
    const { ctx, calls } = recorder();
    const fit = coverFit({ w: 1280, h: 720 }, { w: 390, h: 844 });
    // the middle fifth of the video, both ways
    drawBoxes(ctx, 390, 844, [{ label: "crate", box: [400, 400, 600, 600], kind: "object", source: "mediapipe" }], { fit });
    const [x, y, w, h] = calls.find((c) => c.op === "strokeRect")!.args as number[];
    expect(x + w / 2).toBeCloseTo(195, 3);
    expect(y + h / 2).toBeCloseTo(422, 3);
    expect(w).toBeCloseTo(fit.w / 5, 3);
    expect(h).toBeCloseTo(fit.h / 5, 3);
    expect(calls[0]).toMatchObject({ op: "clearRect", args: [0, 0, 390, 844] });
  });
});

describe("<CameraPreview fill>", () => {
  it("fills its box: no aspect ratio of its own, the canvas takes the screen's size, boxes go through the crop", () => {
    const store = createBoxStore(() => 1000);
    const { ctx, calls } = recorder();
    const { v, stream } = sharedVideo(1280, 720);
    act(() => render(
      <CameraPreview fill store={store} video={() => v} denied={() => false} ctxOf={() => ctx} now={() => 1000} sizeOf={() => ({ w: 390, h: 844 })} />,
      root,
    ));
    const card = root.querySelector<HTMLElement>("[data-testid=camera-preview]")!;
    expect(card.classList.contains("cam-card--fill")).toBe(true);
    expect(card.getAttribute("aria-hidden")).toBe("true");
    expect(card.style.aspectRatio).toBe("");
    expect((card.querySelector("video") as HTMLVideoElement & { srcObject: unknown }).srcObject).toBe(stream);
    const canvas = root.querySelector<HTMLCanvasElement>("[data-testid=camera-boxes]")!;
    expect([canvas.width, canvas.height]).toEqual([390, 844]);
    act(() => store.setMediapipe([{ label: "bottle", box: [400, 400, 600, 600], score: 0.8 }]));
    const [x, y, w, h] = calls.filter((c) => c.op === "strokeRect").at(-1)!.args as number[];
    const fit = coverFit({ w: 1280, h: 720 }, { w: 390, h: 844 });
    expect(x + w / 2).toBeCloseTo(195, 3);
    expect(y + h / 2).toBeCloseTo(422, 3);
    expect(w).toBeCloseTo(fit.w / 5, 3);
  });
});

describe("full-screen choice", () => {
  it("defaults to full screen and survives missing or throwing storage", () => {
    expect(loadFullScreen(() => null)).toBe(true);
    expect(loadFullScreen(() => { throw new Error("blocked"); })).toBe(true);
    expect(() => saveFullScreen(false, () => { throw new Error("blocked"); })).not.toThrow();
    saveFullScreen(false);
    expect(localStorage.getItem(FULL_KEY)).toBe("card");
    expect(loadFullScreen()).toBe(false);
  });
});

describe("AppShell: the full-screen camera layout", () => {
  function fakeWired() {
    let v: AppView = { screen: { ...initialScreen, status: { kind: "ready", label: "Ready" } }, level: 0, progress: null, confirmPending: false, online: true, lang: "en" };
    const subs = new Set<(v: AppView) => void>();
    const w = {
      onTalk: vi.fn(), onStop: vi.fn(), dispose: vi.fn(), startListening: vi.fn(async () => true),
      view: { get: () => v, subscribe: (fn: (v: AppView) => void) => { subs.add(fn); return () => { subs.delete(fn); }; } },
    };
    const push = (patch: Partial<AppView>) => { v = { ...v, ...patch }; subs.forEach((fn) => fn(v)); };
    return { w: w as unknown as WiredApp, raw: w, push };
  }
  const mount = (w: WiredApp, video: () => HTMLVideoElement | null) => {
    act(() => { render(<AppShell deviceCheck={null} wire={() => w} languages={["en"]} initial={{ ...DEFAULT_SETTINGS }} cameraVideo={video} cameraPollMs={50} />, root); });
    act(() => { root.querySelector<HTMLButtonElement>("button.onboard__start")!.click(); });
  };
  const live = () => root.querySelector<HTMLElement>("[data-testid=live-screen]");
  const byLabel = (label: string) => root.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);

  it("fills the screen with the camera once it runs; the column layout stays while it is off", () => {
    vi.useFakeTimers();
    const f = fakeWired();
    let src: HTMLVideoElement | null = null;
    mount(f.w, () => src);
    expect(live()).toBeNull();
    expect(root.querySelector(".screen")).not.toBeNull();
    src = sharedVideo().v;
    act(() => { vi.advanceTimersByTime(80); });
    expect(live()).not.toBeNull();
    expect(root.querySelector(".screen")).toBeNull();
    expect(document.documentElement.classList.contains(LIVE_HTML_CLASS)).toBe(true);
    // the live picture is the full-screen card, inside the one gesture surface
    const card = root.querySelector("[data-testid=camera-preview]")!;
    expect(card.getAttribute("data-fill")).toBe("true");
    expect(card.closest("button.surface")!.getAttribute("aria-label")).toBe(SURFACE_LABEL);
    // the aria-live region is still there, outside the surface
    const region = root.querySelector('[aria-live="polite"]')!;
    expect(region).not.toBeNull();
    expect(card.closest("button.surface")!.contains(region)).toBe(false);
    act(() => render(null, root));
    expect(document.documentElement.classList.contains(LIVE_HTML_CLASS)).toBe(false);
  });

  it("shows only the latest 'You' line (right) and the latest AIRA line (left, labelled), updating live", () => {
    const f = fakeWired();
    mount(f.w, () => sharedVideo().v);
    const captions = root.querySelector("[data-testid=live-captions]")!;
    expect(captions.getAttribute("aria-hidden")).toBe("true");
    expect(captions.textContent).toContain("Double-tap anywhere to talk.");
    act(() => { f.push({ screen: { ...initialScreen, status: { kind: "listening", label: "Listening" }, userText: "What neigh" } }); });
    expect(root.querySelector("[data-testid=live-user]")!.textContent).toBe("What neigh");
    expect(root.querySelector("[data-testid=live-aira]")).toBeNull();
    act(() => { f.push({ screen: { ...initialScreen, userText: "What neighborhood?", airaText: "This appears to be the King's" } }); });
    expect(root.querySelector("[data-testid=live-user]")!.textContent).toBe("What neighborhood?");
    const aira = root.querySelector("[data-testid=live-aira]")!;
    expect(aira.querySelector(".live__who")!.textContent).toBe("AIRA");
    expect(aira.querySelector(".live__text")!.textContent).toBe("This appears to be the King's");
    expect(root.querySelector("[data-testid=live-user]")!.classList.contains("live__bubble--user")).toBe(true);
    expect(aira.classList.contains("live__bubble--aira")).toBe(true);
    expect(root.querySelector(".live__bubble--hint")).toBeNull();
    expect(root.querySelectorAll(".live__bubble")).toHaveLength(2);
    // the status chip floats at the top and follows the view
    act(() => { f.push({ screen: { ...initialScreen, status: { kind: "thinking", label: "Thinking" }, userText: "x" } }); });
    expect(root.querySelector(".live__top .status-chip")!.textContent).toContain("Thinking");
    expect(live()!.getAttribute("data-status")).toBe("thinking");
  });

  it("keeps every control, as labelled buttons outside the surface, routed to the wired handlers", () => {
    const f = fakeWired();
    mount(f.w, () => sharedVideo().v);
    const surface = root.querySelector("button.surface")!;
    const talk = byLabel(LIVE_TEXT.talk)!;
    const stop = byLabel(LIVE_TEXT.stop)!;
    const exit = byLabel(LIVE_TEXT.exit)!;
    const hide = byLabel(PREVIEW_TEXT.hide)!;
    for (const b of [talk, stop, exit, hide]) {
      expect(b).not.toBeNull();
      expect(b.getAttribute("type")).toBe("button");
      expect(surface.contains(b)).toBe(false);
    }
    expect(root.querySelectorAll("button")).toHaveLength(5); // the surface + four controls, nothing else
    act(() => talk.click());
    expect(f.raw.onTalk).toHaveBeenCalledTimes(1);
    act(() => stop.click());
    expect(f.raw.onStop).toHaveBeenCalledTimes(1);
    // the gesture surface still talks and stops as before
    act(() => { surface.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 0 })); });
    expect(f.raw.onTalk).toHaveBeenCalledTimes(2);
    act(() => { surface.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Escape" })); });
    expect(f.raw.onStop).toHaveBeenCalledTimes(2);
    // "Hide camera" leaves the live layout for the column, with the card hidden, and persists as before
    act(() => hide.click());
    expect(live()).toBeNull();
    expect(root.querySelector("[data-testid=camera-preview]")).toBeNull();
    expect(localStorage.getItem(PREVIEW_KEY)).toBe("hidden");
    expect(byLabel(LIVE_TEXT.enter)).toBeNull(); // nothing to fill the screen with while the card is hidden
  });

  it("Exit full screen goes back to the column layout with the camera card, remembers it, and can come back", () => {
    const f = fakeWired();
    const { v } = sharedVideo();
    mount(f.w, () => v);
    act(() => byLabel(LIVE_TEXT.exit)!.click());
    expect(live()).toBeNull();
    expect(root.querySelector(".screen")).not.toBeNull();
    expect(root.querySelector("[data-testid=camera-preview]")!.getAttribute("data-fill")).toBeNull();
    expect(localStorage.getItem(FULL_KEY)).toBe("card");
    expect(f.raw.onTalk).not.toHaveBeenCalled(); // the exit button never talks
    // the way back, next to the camera toggle, outside the surface
    const enter = byLabel(LIVE_TEXT.enter)!;
    expect(enter).not.toBeNull();
    expect(enter.closest("button.surface")).toBeNull();
    act(() => enter.click());
    expect(live()).not.toBeNull();
    expect(localStorage.getItem(FULL_KEY)).toBe("full");
  });

  it("starts in the column layout when the helper left full screen last time", () => {
    localStorage.setItem(FULL_KEY, "card");
    const f = fakeWired();
    mount(f.w, () => sharedVideo().v);
    expect(live()).toBeNull();
    expect(root.querySelector("[data-testid=camera-preview]")).not.toBeNull();
    expect(byLabel(LIVE_TEXT.enter)).not.toBeNull();
  });
});
