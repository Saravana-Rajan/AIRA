// frontend/app/test/ui.cameraPreview.test.tsx — the live camera card on the phone screen: it shows the SAME
// stream (no second getUserMedia), draws the on-device boxes, lights "AIRA is looking" during a tool frame,
// and "Hide camera" / "Show camera" persists without touching the camera.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import type { FramePurpose } from "@aira/contracts";
import {
  CameraPreview, LOOKING_LINGER_MS, PREVIEW_KEY, PREVIEW_TEXT, loadPreviewShown, savePreviewShown,
} from "../src/ui/CameraPreview";
import { createBoxStore } from "../src/walk/boxes";
import type { Ctx2D } from "../src/ui/boxDraw";
import { AppShell } from "../src/ui/AppShell";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import { initialScreen } from "../src/ui/screenState";
import type { AppView, WiredApp } from "../src/core/wire";
import { __resetLooking, beginLooking, currentLooking, lookingKind, subscribeLooking } from "../src/camera/looking";
import { __setCaptureDeps, captureFrame, stopCamera } from "../src/camera/capture";

let root: HTMLElement;
beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
  try { localStorage.clear(); } catch { /* ignore */ }
  __resetLooking();
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
function sharedVideo(w = 640, h = 480) {
  const stream = { id: "shared", active: true, getTracks: () => [] } as unknown as MediaStream;
  const v = document.createElement("video");
  Object.defineProperty(v, "srcObject", { value: stream, writable: true });
  Object.defineProperty(v, "videoWidth", { value: w });
  Object.defineProperty(v, "videoHeight", { value: h });
  return { v, stream };
}

const card = () => root.querySelector<HTMLElement>("[data-testid=camera-preview]");
const previewVideo = () => card()?.querySelector("video") as (HTMLVideoElement & { srcObject: unknown }) | null;

describe("<CameraPreview>", () => {
  it("attaches the existing shared stream, never calls getUserMedia, and is hidden from screen readers", () => {
    const gum = vi.fn();
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: gum }, configurable: true });
    const { v, stream } = sharedVideo(640, 480);
    act(() => render(<CameraPreview video={() => v} denied={() => false} ctxOf={() => recorder().ctx} />, root));
    const pv = previewVideo()!;
    expect(pv.srcObject).toBe(stream);
    expect(pv.muted).toBe(true);
    expect(pv.autoplay).toBe(true);
    expect(pv.hasAttribute("playsinline")).toBe(true);
    expect(card()!.getAttribute("aria-hidden")).toBe("true");
    expect(card()!.style.aspectRatio.replace(/\s/g, "")).toBe("640/480");
    expect(root.querySelector("[data-testid=camera-placeholder]")).toBeNull();
    expect(gum).not.toHaveBeenCalled();
    // Frames go to AIRA continuously while the camera runs, so the card always says so (not only the badge).
    expect(root.querySelector("[data-testid=camera-shared]")!.textContent).toBe(PREVIEW_TEXT.shared);
    expect(root.querySelector("[data-testid=looking-badge]")).toBeNull();
  });

  it("shows a placeholder before the camera starts, and a permission note when it was refused", () => {
    act(() => render(<CameraPreview video={() => null} denied={() => false} ctxOf={() => null} />, root));
    expect(root.querySelector("[data-testid=camera-placeholder]")!.textContent).toBe(PREVIEW_TEXT.waiting);
    expect(root.querySelector("[data-testid=camera-shared]")).toBeNull();
    act(() => render(null, root));
    act(() => render(<CameraPreview video={() => null} denied={() => true} ctxOf={() => null} />, root));
    expect(root.querySelector("[data-testid=camera-placeholder]")!.textContent).toBe(PREVIEW_TEXT.denied);
  });

  it("picks up the stream once the camera opens later", () => {
    vi.useFakeTimers();
    let src: HTMLVideoElement | null = null;
    const { v, stream } = sharedVideo();
    act(() => render(<CameraPreview video={() => src} denied={() => false} ctxOf={() => null} pollMs={100} />, root));
    expect(previewVideo()!.srcObject ?? null).toBeNull();
    src = v;
    act(() => { vi.advanceTimersByTime(150); });
    expect(previewVideo()!.srcObject).toBe(stream);
  });

  it("draws the on-device MediaPipe boxes with their labels over the preview", () => {
    const store = createBoxStore(() => 1000);
    const { ctx, calls } = recorder();
    const { v } = sharedVideo(400, 300);
    act(() => render(<CameraPreview store={store} video={() => v} denied={() => false} ctxOf={() => ctx} now={() => 1000} />, root));
    act(() => store.setMediapipe([{ label: "bottle", box: [0, 0, 500, 500], score: 0.8 }]));
    const strokes = calls.filter((c) => c.op === "strokeRect");
    expect(strokes.length).toBeGreaterThan(0);
    const canvas = root.querySelector<HTMLCanvasElement>("[data-testid=camera-boxes]")!;
    expect(strokes.at(-1)!.args).toEqual([0, 0, canvas.width / 2, canvas.height / 2]);
    expect(canvas.height).toBe(Math.round((canvas.width * 300) / 400));   // scaled to the stream
    expect(calls.filter((c) => c.op === "fillText").map((c) => c.args[0])).toContain("bottle 80%");
  });

  it("shows 'AIRA is looking' during a tool frame (Counting… / Reading… by purpose), then clears", () => {
    vi.useFakeTimers();
    act(() => render(<CameraPreview video={() => null} denied={() => false} ctxOf={() => null} />, root));
    const badge = () => root.querySelector("[data-testid=looking-badge]");
    expect(badge()).toBeNull();
    let end = () => {};
    act(() => { end = beginLooking("count"); });
    expect(badge()!.textContent).toContain(PREVIEW_TEXT.looking);
    expect(badge()!.textContent).toContain(PREVIEW_TEXT.counting);
    act(() => { end(); });
    expect(badge()).not.toBeNull();   // lingers so a quick capture is still seen
    act(() => { vi.advanceTimersByTime(LOOKING_LINGER_MS + 10); });
    expect(badge()).toBeNull();
    act(() => { end = beginLooking("invoice"); });
    expect(badge()!.textContent).toContain(PREVIEW_TEXT.reading);
    act(() => { end(); vi.advanceTimersByTime(LOOKING_LINGER_MS + 10); });
    act(() => { end = beginLooking("ask"); });
    expect(badge()!.textContent).toBe(PREVIEW_TEXT.looking);
    act(() => { end(); });
  });
});

describe("looking state", () => {
  it("lights only for tool purposes (never live, preview, describe or guide)", () => {
    const seen: (FramePurpose | null)[] = [];
    subscribeLooking((p) => seen.push(p));
    for (const p of ["live", "preview", "describe", "guide"] as FramePurpose[]) beginLooking(p)();
    expect(seen).toEqual([]);
    const end = beginLooking("walk");
    expect(currentLooking()).toBe("walk");
    end(); end();
    expect(currentLooking()).toBeNull();
    expect(seen).toEqual(["walk", null]);
    expect(lookingKind("count")).toBe("counting");
    expect(lookingKind("cash")).toBe("reading");
    expect(lookingKind("walk")).toBe("looking");
  });

  it("captureFrame of a tool purpose reports looking while it captures (display only, same frame)", async () => {
    const seen: (FramePurpose | null)[] = [];
    subscribeLooking((p) => seen.push(p));
    __setCaptureDeps({
      source: { width: () => 64, height: () => 48, draw: () => {} },
      makeCanvas: () => ({ canvas: {} as HTMLCanvasElement, ctx: { getImageData: () => ({ data: new Uint8ClampedArray(64 * 48 * 4) }) } as unknown as CanvasRenderingContext2D }),
      encoder: async () => "abc",
    });
    try {
      const f = await captureFrame("count");
      expect(f.purpose).toBe("count");
      await captureFrame("live");
    } finally {
      stopCamera();
    }
    expect(seen).toEqual(["count", null]);
  });
});

describe("preview toggle", () => {
  it("load/save survive missing or throwing storage; default is shown", () => {
    expect(loadPreviewShown(() => null)).toBe(true);
    expect(loadPreviewShown(() => { throw new Error("blocked"); })).toBe(true);
    expect(() => savePreviewShown(false, () => { throw new Error("blocked"); })).not.toThrow();
    savePreviewShown(false);
    expect(localStorage.getItem(PREVIEW_KEY)).toBe("hidden");
    expect(loadPreviewShown()).toBe(false);
  });

  function fakeWired() {
    const v: AppView = { screen: initialScreen, level: 0, progress: null, confirmPending: false, online: true, lang: "en" };
    return {
      onTalk: vi.fn(), onStop: vi.fn(), dispose: vi.fn(), startListening: vi.fn(async () => true),
      view: { get: () => v, subscribe: () => () => {} },
    } as unknown as WiredApp;
  }
  const mount = (w: WiredApp) => {
    act(() => { render(<AppShell deviceCheck={null} wire={() => w} languages={["en"]} initial={{ ...DEFAULT_SETTINGS }} />, root); });
    act(() => { root.querySelector<HTMLButtonElement>("button.onboard__start")!.click(); });
  };
  const toggle = () => root.querySelector<HTMLButtonElement>("[data-testid=camera-toggle]")!;

  it("the main screen shows the camera card by default; the toggle hides and shows it and persists", () => {
    const w = fakeWired();
    mount(w);
    expect(card()).not.toBeNull();
    expect(card()!.closest("button.surface")).not.toBeNull();      // inside the screen column, visual only
    expect(toggle().closest("button.surface")).toBeNull();          // a real button of its own, never nested
    expect(toggle().textContent).toBe(PREVIEW_TEXT.hide);
    expect(toggle().getAttribute("type")).toBe("button");
    act(() => toggle().click());
    expect(card()).toBeNull();
    expect(toggle().textContent).toBe(PREVIEW_TEXT.show);
    expect(localStorage.getItem(PREVIEW_KEY)).toBe("hidden");
    expect(w.onTalk).not.toHaveBeenCalled();                       // the toggle never talks
    act(() => toggle().click());
    expect(card()).not.toBeNull();
    expect(localStorage.getItem(PREVIEW_KEY)).toBe("shown");
  });

  it("starts hidden when the helper hid it last time, and toggling never stops or opens the camera", async () => {
    localStorage.setItem(PREVIEW_KEY, "hidden");
    const capture = await import("../src/camera/capture");
    const stop = vi.spyOn(capture, "stopCamera");
    const start = vi.spyOn(capture, "startCamera");
    mount(fakeWired());
    expect(card()).toBeNull();
    act(() => toggle().click());
    act(() => toggle().click());
    expect(stop).not.toHaveBeenCalled();
    expect(start).not.toHaveBeenCalled();
  });
});
