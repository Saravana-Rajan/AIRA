// frontend/app/test/ui.boxes.test.tsx — the phone's "Show boxes" overlay: the drawing (colours, strokes, label
// tags), which boxes are drawn when, the setting's switch and the overlay's place in the shell.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { BOX_COLOURS, drawBoxes, tagText, toPixels, THICK_STROKE, THIN_STROKE, FOUND_STROKE, type Ctx2D } from "../src/ui/boxDraw";
import { BoxOverlay, boxesToDraw, PREVIEW_H, PREVIEW_W } from "../src/ui/BoxOverlay";
import { createBoxStore } from "../src/walk/boxes";
import { Onboarding } from "../src/ui/Onboarding";
import { DEFAULT_SETTINGS } from "../src/core/settings";

function recorder() {
  const calls: { op: string; args: unknown[]; stroke: unknown; fill: unknown; width: number }[] = [];
  const ctx: Ctx2D = {
    strokeStyle: "", fillStyle: "", lineWidth: 1, font: "", textBaseline: "alphabetic",
    clearRect: (...args) => calls.push({ op: "clearRect", args, stroke: ctx.strokeStyle, fill: ctx.fillStyle, width: ctx.lineWidth }),
    strokeRect: (...args) => calls.push({ op: "strokeRect", args, stroke: ctx.strokeStyle, fill: ctx.fillStyle, width: ctx.lineWidth }),
    fillRect: (...args) => calls.push({ op: "fillRect", args, stroke: ctx.strokeStyle, fill: ctx.fillStyle, width: ctx.lineWidth }),
    fillText: (...args) => calls.push({ op: "fillText", args, stroke: ctx.strokeStyle, fill: ctx.fillStyle, width: ctx.lineWidth }),
    measureText: (text) => ({ width: text.length * 6 }),
  };
  return { ctx, calls };
}

describe("drawBoxes", () => {
  it("maps [ymin, xmin, ymax, xmax] 0-1000 onto the canvas (y first)", () => {
    expect(toPixels([250, 100, 750, 600], 200, 100)).toEqual({ x: 20, y: 25, w: 100, h: 50 });
  });

  it("thin blue MediaPipe boxes, red when danger, thick labelled ER 2 boxes, green with a check when found", () => {
    const { ctx, calls } = recorder();
    drawBoxes(ctx, 200, 100, [
      { label: "Aavin milk", box: [500, 500, 1000, 1000], kind: "found", source: "er2", score: 0.92 },
      { label: "chair", box: [0, 0, 500, 500], kind: "object", source: "mediapipe", score: 0.8 },
      { label: "crate", box: [200, 300, 900, 700], kind: "danger", source: "mediapipe" },
    ]);
    expect(calls[0]).toMatchObject({ op: "clearRect", args: [0, 0, 200, 100] });
    const strokes = calls.filter((c) => c.op === "strokeRect");
    // MediaPipe first (thin), ER 2 last (on top)
    expect(strokes.map((s) => [s.stroke, s.width])).toEqual([
      [BOX_COLOURS.object, THIN_STROKE], [BOX_COLOURS.danger, THIN_STROKE], [BOX_COLOURS.found, FOUND_STROKE],
    ]);
    expect(strokes[0].args).toEqual([0, 0, 100, 50]);
    expect(strokes[2].args).toEqual([100, 50, 100, 50]);
    const texts = calls.filter((c) => c.op === "fillText");
    expect(texts.map((t) => t.args[0])).toEqual(["crate", "✓ Aavin milk 92%"]);   // no tag on a quiet thin box
    expect(texts.every((t) => t.fill === "#fff")).toBe(true);
    const tags = calls.filter((c) => c.op === "fillRect");
    expect(tags.map((t) => t.fill)).toEqual([BOX_COLOURS.danger, BOX_COLOURS.found]);
    expect(tags[0].args[1]).toBe(20 - 16);     // tag sits above the box...
    expect(tags[1].args[1]).toBe(50 - 16);
    const { ctx: c2, calls: k2 } = recorder();
    drawBoxes(c2, 200, 100, [{ label: "door", box: [0, 0, 300, 300], kind: "object", source: "er2" }]);
    expect(k2.filter((c) => c.op === "strokeRect")[0].width).toBe(THICK_STROKE);
    expect(k2.filter((c) => c.op === "fillRect")[0].args[1]).toBe(0);   // ...or inside its top-left corner at the edge
  });

  it("tags show the confidence when known and a check once found", () => {
    expect(tagText({ label: "Aavin milk", kind: "object", score: 0.916 })).toBe("Aavin milk 92%");
    expect(tagText({ label: "Aavin milk", kind: "found" })).toBe("✓ Aavin milk");
    expect(tagText({ label: "x", kind: "danger", score: Number.NaN })).toBe("x");
  });

  it("colours follow the spec: danger red, object blue, found green, hand orange", () => {
    expect(BOX_COLOURS).toEqual({ danger: "#e53935", object: "#1e88e5", found: "#43a047", hand: "#fb8c00" });
  });
});

describe("boxesToDraw", () => {
  it("draws fresh MediaPipe boxes (red when danger) under ER 2 boxes, the found target green, and drops stale ones", () => {
    const snap = {
      mediapipe: { reports: [{ label: "chair", box: [1, 2, 3, 4] as const, danger: true, score: 0.5 }], at: 1000 },
      er2: { objects: [{ label: "Aavin milk", box: [5, 6, 7, 8] as const, score: 0.9 }, { label: "door", box: [1, 1, 2, 2] as const }],
             target: "aavin milk", found: true, at: 1000 },
    };
    const out = boxesToDraw(snap as never, 1500);
    expect(out).toEqual([
      { label: "chair", box: [1, 2, 3, 4], score: 0.5, source: "mediapipe", kind: "danger" },
      { label: "Aavin milk", box: [5, 6, 7, 8], score: 0.9, source: "er2", kind: "found" },
      { label: "door", box: [1, 1, 2, 2], score: undefined, source: "er2", kind: "object" },
    ]);
    expect(boxesToDraw(snap as never, 2500).map((b) => b.source)).toEqual(["er2", "er2"]);   // MediaPipe older than 1 s
    expect(boxesToDraw(snap as never, 8000)).toEqual([]);                                  // ER 2 older than 6 s
    const notFound = { ...snap, er2: { ...snap.er2, found: false } };
    expect(boxesToDraw(notFound as never, 1500)[1].kind).toBe("object");
  });
});

describe("<BoxOverlay>", () => {
  let root: HTMLElement;
  beforeEach(() => { root = document.createElement("div"); document.body.appendChild(root); vi.useFakeTimers(); });
  afterEach(() => { act(() => render(null, root)); root.remove(); vi.useRealTimers(); });

  it("renders a hidden preview with a canvas and redraws when the store changes", () => {
    const store = createBoxStore(() => 1000);
    const { ctx, calls } = recorder();
    act(() => render(<BoxOverlay store={store} video={() => null} ctxOf={() => ctx} now={() => 1000} />, root));
    const el = root.querySelector('[data-testid="box-overlay"]')!;
    expect(el.getAttribute("aria-hidden")).toBe("true");
    const canvas = root.querySelector("canvas")!;
    expect(canvas.width).toBe(PREVIEW_W);
    expect(canvas.height).toBe(PREVIEW_H);
    expect(root.querySelector("video")?.hasAttribute("muted") || (root.querySelector("video") as HTMLVideoElement).muted).toBe(true);
    expect(calls.filter((c) => c.op === "strokeRect")).toEqual([]);      // nothing to draw yet
    act(() => { store.setMediapipe([{ label: "chair", box: [0, 0, 500, 500], score: 0.8, danger: true }]); });
    const strokes = calls.filter((c) => c.op === "strokeRect");
    expect(strokes.length).toBe(1);
    expect(strokes[0]).toMatchObject({ args: [0, 0, PREVIEW_W / 2, PREVIEW_H / 2], stroke: BOX_COLOURS.danger });
    expect(calls.filter((c) => c.op === "fillText").map((c) => c.args[0])).toEqual(["chair 80%"]);
    act(() => { store.onServerMsg({ t: "boxes", source: "er2", objects: [{ label: "milk", box: [0, 0, 1000, 1000] }], target: "milk", found: true }); });
    const last = calls.filter((c) => c.op === "strokeRect").pop()!;
    expect(last).toMatchObject({ stroke: BOX_COLOURS.found, width: FOUND_STROKE, args: [0, 0, PREVIEW_W, PREVIEW_H] });
  });

  it("stops listening when unmounted", () => {
    const store = createBoxStore(() => 0);
    const { ctx, calls } = recorder();
    act(() => render(<BoxOverlay store={store} video={() => null} ctxOf={() => ctx} now={() => 0} />, root));
    act(() => render(null, root));
    const before = calls.length;
    act(() => { store.setMediapipe([{ label: "chair", box: [0, 0, 500, 500] }]); });
    expect(calls.length).toBe(before);
  });
});

describe("the 'Show boxes' switch", () => {
  let root: HTMLElement;
  beforeEach(() => { root = document.createElement("div"); document.body.appendChild(root); });
  afterEach(() => { act(() => render(null, root)); root.remove(); });

  it("is off by default and is saved with the settings when switched on", () => {
    const onDone = vi.fn();
    act(() => render(<Onboarding onDone={onDone} languages={["en-IN"]} />, root));
    const switches = Array.from(root.querySelectorAll<HTMLInputElement>('input[role="switch"]'));
    expect(switches.length).toBe(2);
    const boxes = root.querySelector<HTMLInputElement>(".tb-switch--boxes input")!;
    expect(boxes.checked).toBe(false);
    expect(root.querySelector(".tb-switch--boxes")?.textContent).toMatch(/show boxes/i);
    act(() => root.querySelector<HTMLButtonElement>("button.onboard__start")!.click());
    expect(onDone.mock.calls[0][0]).toMatchObject({ showBoxes: false, onboarded: true });
    act(() => render(null, root));
    const onDone2 = vi.fn();
    act(() => render(<Onboarding onDone={onDone2} languages={["en-IN"]} initial={{ ...DEFAULT_SETTINGS }} />, root));
    act(() => { root.querySelector<HTMLInputElement>(".tb-switch--boxes input")!.click(); });
    act(() => root.querySelector<HTMLButtonElement>("button.onboard__start")!.click());
    expect(onDone2.mock.calls[0][0]).toMatchObject({ showBoxes: true, screenReaderMode: false });
  });
});
