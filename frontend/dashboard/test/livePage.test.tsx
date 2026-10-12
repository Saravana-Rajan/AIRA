import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "preact/test-utils";
import type { MonitorMsg } from "@aira/contracts";
import { cleanup, findAllByText, mount, text } from "./helpers/render";
import { LivePage } from "../src/pages/LivePage";
import type { DrawDeps, Drawable } from "../src/lib/frameDraw";
import { initialState, reduce, type Frame, type LiveState } from "../src/lib/monitor";

afterEach(cleanup);

type MonInput = MonitorMsg extends infer M ? (M extends MonitorMsg ? Omit<M, "t" | "session" | "ts"> & { ts?: number; session?: string } : never) : never;
const T0 = 1760000000000;
// The page's clock is T0 + 84.5 s; messages default to just before it, so ER 2 and MediaPipe boxes are fresh.
const mon = (m: MonInput): MonitorMsg =>
  ({ t: "mon", session: "s1", ts: T0 + 84_000, ...m }) as MonitorMsg;
const stateOf = (msgs: MonitorMsg[]): LiveState => msgs.reduce(reduce, initialState());
type Conn = "waiting" | "connecting" | "live" | "offline" | "rejected";

/** A drawer that records what it was asked to decode, so the test sees which frame reached the canvas. */
function fakeDraw() {
  const rafs: Array<() => void> = [];
  const decoded: Frame[] = [];
  const deps: DrawDeps = {
    raf: (cb) => { rafs.push(cb); },
    decode: async (f) => { decoded.push(f); return { close: () => {} } as unknown as Drawable; },
  };
  return { deps, rafs, decoded, tick: () => rafs.splice(0).forEach((cb) => cb()) };
}

const page = (state: LiveState, conn: Conn = "live", keyNeeded = false, onKey = vi.fn(), extra: { record?: boolean; onRecord?: (on: boolean) => void; drawDeps?: DrawDeps } = {}) =>
  mount(<LivePage state={state} conn={conn} keyNeeded={keyNeeded} onKey={onKey} now={() => T0 + 84_500} {...extra} />);

const milk = { label: "Aavin milk 500 ml", box: [400, 500, 440, 560] as [number, number, number, number], point: null, score: 0.92, distance: "arm" as const };
const frame = mon({ kind: "frame", jpeg_b64: "AAAA", w: 480, h: 640, purpose: "preview", ts: T0 + 84_000 });
const started = mon({ kind: "session", state: "start", lang: "ta", uid: "u1", ts: T0 });

describe("LivePage: AIRA's own chrome", () => {
  it("has the header with logo, shop name, status pill and connection, the camera card, the chat, the stepper, the timeline and the bar; no Target or Session stats card", () => {
    const el = page(initialState(), "connecting");
    expect(el.querySelector("img.lv-logo")!.getAttribute("src")).toMatch(/aira-logo-192\.png$/);
    expect(text(el.querySelector(".lv-title"))).toBe("Sunrise Dairy - Live");
    expect(findAllByText(el, ".lv-pill", "Idle")).toHaveLength(1);
    expect(findAllByText(el, ".lv-conn__text", "Connecting…")).toHaveLength(1);
    expect([...el.querySelectorAll("h3")].map(text)).toEqual(["Sam's view", "Conversation"]);
    expect(text(el)).not.toContain("Target");
    expect(text(el)).not.toContain("Session stats");
    expect(text(el)).not.toContain("Nothing being looked for");
    expect(el.querySelector(".card, .card--target, .card--stats, .hud")).toBeNull();
    expect(findAllByText(el, "p", "Waiting for Sam's phone…")).toHaveLength(1);
    expect(el.querySelector("canvas")).toBeNull();
    expect(text(el.querySelector(".cam__age"))).toBe("—");
    expect(text(el.querySelector(".steps"))).toBe("Waiting for a task");
    expect(el.querySelector(".tl")!.getAttribute("aria-label")).toBe("Activity");
    expect(text(el.querySelector(".tl__empty"))).toContain("What AIRA does shows up here");
    expect(text(el.querySelector(".chat__empty"))).toContain("Nothing said yet");
    expect([...el.querySelectorAll(".legend__item")].map(text)).toEqual(["MediaPipe (on-device)", "Gemini Robotics-ER 2", "Obstacle", "Found", "Fingertip"]);
    expect([...el.querySelectorAll(".bar__btn")].map(text)).toEqual(["Record mode", "Pause camera"]);
    expect(el.querySelector(".room")).toBeNull();
  });
  it("takes the shop's name, and shows the language and connection once a session is open", () => {
    const el = mount(<LivePage state={stateOf([started])} conn="live" keyNeeded={false} onKey={vi.fn()} shopName="Sunrise Dairy East" now={() => T0 + 84_500} />);
    expect(text(el.querySelector(".lv-title"))).toBe("Sunrise Dairy East - Live");
    expect(findAllByText(el, ".lv-pill", "Listening")).toHaveLength(1);
    expect(text(el.querySelector(".lv-conn__lang"))).toBe("Tamil");
    expect(findAllByText(el, ".lv-conn__text", "Session open")).toHaveLength(1);
    expect(el.querySelector(".lv-conn__dot")!.className).toContain("lv-conn__dot--on");
  });
  it("the status pill follows AIRA's state: thinking, guiding, found", () => {
    const thinking = page(stateOf([started, mon({ kind: "status", state: "thinking" })]));
    expect(thinking.querySelector(".lv-pill")!.className).toContain("lv-pill--thinking");
    expect(thinking.querySelector(".lv-pill")!.getAttribute("aria-label")).toBe("AIRA is thinking");
    cleanup();
    const guiding = page(stateOf([started, mon({ kind: "status", state: "guiding" })]));
    expect(findAllByText(guiding, ".lv-pill", "Guiding")).toHaveLength(1);
    cleanup();
    const found = page(stateOf([started, mon({ kind: "vision", source: "er2", objects: [milk], target: "Aavin milk 500 ml", found: true, context: "guidance" })]));
    expect(findAllByText(found, ".lv-pill", "Found")).toHaveLength(1);
  });
  it("offers a session picker only when the shop has more than one live phone, and hands the pick back", () => {
    const onFollow = vi.fn();
    const one = mount(<LivePage state={stateOf([started])} conn="live" keyNeeded={false} onKey={vi.fn()} onFollow={onFollow} now={() => T0 + 84_500} />);
    expect(one.querySelector("select.lv-session")).toBeNull();
    const two = mount(<LivePage state={stateOf([started, mon({ kind: "session", state: "start", lang: "en", uid: "u2", session: "s2", ts: T0 + 1 })])} conn="live" keyNeeded={false} onKey={vi.fn()} onFollow={onFollow} now={() => T0 + 84_500} />);
    const select = two.querySelector<HTMLSelectElement>("select.lv-session")!;
    expect(select.value).toBe("s1");
    expect([...select.options].map((o) => o.value)).toEqual(["s1", "s2"]);
    act(() => {
      select.value = "s2";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(onFollow).toHaveBeenCalledWith("s2");
  });
  it("explains a refused key", () => {
    const el = page(initialState(), "rejected");
    expect(text(el)).toContain("The monitor key was refused");
  });
  it("says what it is waiting for: not configured only without a live URL, else connecting or waiting for the phone", () => {
    const sub = (el: HTMLElement) => text(el.querySelector(".cam__empty-sub"));
    const unconfigured = mount(<LivePage state={initialState()} conn="waiting" keyNeeded={false} onKey={vi.fn()} configured={false} now={() => T0 + 84_500} />);
    expect(sub(unconfigured)).toBe("AIRA Live is not configured for this dashboard.");
    cleanup();
    expect(sub(page(initialState(), "waiting"))).toBe("Connecting to AIRA Live…");
    cleanup();
    expect(sub(page(initialState(), "connecting"))).toBe("Connecting to AIRA Live…");
    cleanup();
    expect(sub(page(initialState(), "offline"))).toContain("Reconnecting");
    cleanup();
    const live = page(initialState(), "live");
    expect(sub(live)).toBe("Connected. Waiting for the phone to start a session.");
    expect(text(live)).not.toContain("not configured");
    cleanup();
    // No key yet: the key prompt, even when the live URL is missing too.
    const noKey = mount(<LivePage state={initialState()} conn="waiting" keyNeeded onKey={vi.fn()} configured={false} now={() => T0 + 84_500} />);
    expect(noKey.querySelector("input#monitor-key")).not.toBeNull();
    expect(noKey.querySelector(".cam__empty")).toBeNull();
  });
  it("asks for the key when none is known and hands it back on submit", () => {
    const onKey = vi.fn();
    const el = page(initialState(), "waiting", true, onKey);
    const input = el.querySelector<HTMLInputElement>("input#monitor-key")!;
    expect(input.type).toBe("password");
    expect(text(el.querySelector("label"))).toBe("Monitor key");
    act(() => {
      input.value = "  secret-1 ";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => {
      el.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(onKey).toHaveBeenCalledWith("secret-1");
    expect(findAllByText(el, "p", "Waiting for Sam's phone…")).toHaveLength(0);
  });
});

describe("LivePage: task stepper", () => {
  it("shows the find steps as chips with checks and durations, and the active step", () => {
    const el = page(stateOf([
      started,
      mon({ kind: "event", name: "tool_call", detail: { tool: "product_find", args: { product: "Aavin milk" } }, ts: T0 + 1000 }),
      mon({ kind: "vision", source: "er2", objects: [milk], target: "Aavin milk 500 ml", found: false, context: "product_find", ts: T0 + 2200 }),
    ]));
    const steps = [...el.querySelectorAll(".step")];
    expect(steps.map((s) => text(s.querySelector(".step__label")))).toEqual(["Ask", "Point", "Guide", "Found"]);
    expect(steps.map((s) => s.className.includes("step--done"))).toEqual([true, true, false, false]);
    expect(steps[2].className).toContain("step--active");
    expect(steps[2].getAttribute("aria-current")).toBe("step");
    expect(steps.map((s) => text(s.querySelector(".step__mark")))).toEqual(["✓", "✓", "3", "4"]);
    expect(text(steps[1].querySelector(".step__time"))).toBe("1.2s");
    expect(el.querySelector(".steps")!.getAttribute("aria-label")).toBe("Finding Aavin milk");
  });
});

describe("LivePage: camera", () => {
  it("paints the newest frame on a canvas through the drawer (one animation frame, newest frame only) and shows the frame age", () => {
    const d = fakeDraw();
    const el = page(stateOf([frame]), "live", false, vi.fn(), { drawDeps: d.deps });
    const canvas = el.querySelector<HTMLCanvasElement>("canvas.camera__canvas")!;
    expect([canvas.getAttribute("width"), canvas.getAttribute("height")]).toEqual(["480", "640"]);
    expect(canvas.getAttribute("data-frame-ts")).toBe(String(T0 + 84_000));
    expect(el.querySelector("img.camera__img")).toBeNull();
    expect(d.rafs).toHaveLength(1);
    d.tick();
    expect(d.decoded.map((f) => f.ts)).toEqual([T0 + 84_000]);
    expect(el.querySelector(".cam__live")!.className).toContain("cam__live--on");
    expect(text(el.querySelector(".cam__age"))).toBe("0.5s ago");
    expect(el.querySelector(".hud")).toBeNull();
  });

  it("draws the frame with each ER 2 box in the frame's pixel space, with a label tag and score, in the object or target tone", () => {
    const el = page(stateOf([
      frame,
      mon({ kind: "status", state: "guiding" }),
      mon({ kind: "vision", source: "er2", objects: [milk, { label: "Sakthi curd 1 L", box: null, point: [600, 200] }], target: "Aavin milk 500 ml", found: false, context: "guidance" }),
    ]));
    const svg = el.querySelector("svg.camera__overlay")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 480 640");
    const target = svg.querySelector("[data-tone=target]")!;
    expect(target.getAttribute("data-label")).toBe("Aavin milk 500 ml");
    const rect = target.querySelector("rect")!;
    expect([rect.getAttribute("x"), rect.getAttribute("y"), rect.getAttribute("width"), rect.getAttribute("height")]).toEqual(["240", "256", "28.8", "25.6"]);
    expect(target.querySelector(".ov__tag")).not.toBeNull();
    expect(text(target.querySelector("text"))).toBe("Aavin milk 500 ml 92%");
    const other = svg.querySelector("[data-tone=object]")!;
    expect(other.querySelector("circle")!.getAttribute("cx")).toBe("96");
    expect(other.querySelector("circle")!.getAttribute("cy")).toBe("384");
    expect(findAllByText(el, "[role=status]", "Looking for Aavin milk 500 ml")).toHaveLength(1);
    expect(findAllByText(el, ".lv-pill", "Guiding")).toHaveLength(1);
    expect(svg.querySelector("[data-tone=found]")).toBeNull();
  });

  it("draws MediaPipe boxes thin and grey, and an obstacle red with the camera flagged", () => {
    const el = page(stateOf([
      frame,
      mon({ kind: "vision", source: "mediapipe", objects: [{ label: "chair", box: [420, 310, 900, 690], point: null, score: 0.82, severity: "danger" }, { label: "bottle", box: [100, 50, 300, 120], point: null, score: 0.61 }], severity: "danger", ts: T0 + 84_400 }),
    ]));
    const svg = el.querySelector("svg.camera__overlay")!;
    expect(svg.querySelector("[data-tone=fast]")!.getAttribute("data-label")).toBe("bottle");
    expect(svg.querySelector("[data-tone=fast] .ov__tag")).toBeNull();
    const danger = svg.querySelector("[data-tone=danger]")!;
    expect(danger.getAttribute("data-label")).toBe("chair");
    expect(text(danger.querySelector("text"))).toBe("chair 82%");
    expect(el.querySelector(".camera")!.className).toContain("camera--danger");
  });

  it("turns the target green with a check and a Found banner when found", () => {
    const el = page(stateOf([
      frame,
      mon({ kind: "status", state: "guiding" }),
      mon({ kind: "vision", source: "er2", objects: [milk], target: "Aavin milk 500 ml", found: true, context: "guidance" }),
    ]));
    const found = el.querySelector("svg [data-tone=found]")!;
    expect(found.getAttribute("class")).toContain("ov--found");
    expect(text(found.querySelector("text"))).toBe("✓ Aavin milk 500 ml 92%");
    expect(findAllByText(el, "p.camera__found", "✓ Found: Aavin milk 500 ml")).toHaveLength(1);
    expect(findAllByText(el, ".lv-pill", "Found")).toHaveLength(1);
    expect(findAllByText(el, ".tl__label", "Found")).toHaveLength(1);
  });

  it("draws the fingertip dot and a dashed line to the target while guiding", () => {
    const el = page(stateOf([
      frame,
      mon({ kind: "status", state: "guiding" }),
      mon({ kind: "vision", source: "er2", objects: [milk], target: "Aavin milk 500 ml", found: false, context: "guidance" }),
      mon({ kind: "hand", point: [800, 100], near: false }),
    ]));
    const hand = el.querySelector("svg [data-tone=hand] circle")!;
    expect([hand.getAttribute("cx"), hand.getAttribute("cy")]).toEqual(["48", "512"]);
    const line = el.querySelector("svg line.ov--line")!;
    expect([line.getAttribute("x1"), line.getAttribute("y1"), line.getAttribute("x2"), line.getAttribute("y2")]).toEqual(["48", "512", "254.4", "268.8"]);
  });

  it("pauses the camera on the last frame and says so in the camera card", () => {
    const el = page(stateOf([frame]));
    const btn = findAllByText(el, ".bar__btn", "Pause camera")[0];
    act(() => { btn.click(); });
    expect(findAllByText(el, ".bar__btn", "Resume camera")).toHaveLength(1);
    expect(findAllByText(el, ".cam__paused", "Paused")).toHaveLength(1);
    expect(el.querySelector("canvas.camera__canvas")).not.toBeNull();
  });
});

describe("LivePage: conversation as chat bubbles", () => {
  it("puts Sam on the right, AIRA on the left with an avatar, the customer on the right in purple, with language chips; partials are italic and then replaced", () => {
    const s1 = stateOf([
      mon({ kind: "transcript", role: "owner", text: "பால் எங்கே?", lang: "ta", final: true }),
      mon({ kind: "transcript", role: "customer", text: "ஒரு பால்", lang: "ta", final: true }),
      mon({ kind: "transcript", role: "aira", text: "Fridge two,", lang: "en", final: false }),
    ]);
    const el = page(s1);
    const msgs = [...el.querySelectorAll("li.msg")];
    expect(msgs.map((l) => text(l.querySelector(".msg__who")))).toEqual(["Sam", "Customer", "AIRA"]);
    expect(msgs.map((l) => text(l.querySelector(".msg__lang")))).toEqual(["ta", "ta", "en"]);
    expect(msgs.map((l) => text(l.querySelector(".msg__text")))).toEqual(["பால் எங்கே?", "ஒரு பால்", "Fridge two,"]);
    expect(msgs.map((l) => l.classList.contains("msg--partial"))).toEqual([false, false, true]);
    expect(msgs[0].classList.contains("msg--owner")).toBe(true);
    expect(msgs[1].classList.contains("msg--customer")).toBe(true);
    expect(msgs[2].classList.contains("msg--aira")).toBe(true);
    expect(msgs.map((l) => l.querySelector(".msg__avatar") !== null)).toEqual([false, false, true]);
    expect(el.querySelector("li.line, .chip")).toBeNull();
    cleanup();
    const s2 = reduce(s1, mon({ kind: "transcript", role: "aira", text: "Fridge two, middle shelf.", lang: "en", final: true }));
    const el2 = page(s2);
    const msgs2 = [...el2.querySelectorAll("li.msg")];
    expect(msgs2).toHaveLength(3);
    expect(text(msgs2[2].querySelector(".msg__text"))).toBe("Fridge two, middle shelf.");
    expect(msgs2[2].classList.contains("msg--partial")).toBe(false);
  });
});

describe("LivePage: activity timeline", () => {
  it("shows events oldest first as icon chips with a readable label, detail, time and kind", () => {
    const el = page(stateOf([
      mon({ kind: "event", name: "guidance_start", detail: { target: "Aavin milk 500 ml" }, ts: T0 }),
      mon({ kind: "event", name: "count", detail: { sku: "sakthi_curd_1l", counted: 28, expected: 30 }, ts: T0 + 60_000 }),
      mon({ kind: "event", name: "order_sent", detail: { summary: "30 L Sakthi curd sent on WhatsApp" }, ts: T0 + 120_000 }),
      mon({ kind: "event", name: "confirm_resolved", detail: { id: "c1", answer: "yes" }, ts: T0 + 180_000 }),
      mon({ kind: "event", name: "client_event", detail: { name: "network", data: { online: false } }, ts: T0 + 190_000 }),
    ]));
    const items = [...el.querySelectorAll("li.tl__item")];
    expect(items.map((i) => text(i.querySelector(".tl__label")))).toEqual(["Searching", "Counted", "Order sent", "Answered", "Phone event"]);
    expect(items.map((i) => [...i.classList].find((c) => c.startsWith("tl__item--")))).toEqual(["tl__item--vision", "tl__item--vision", "tl__item--order", "tl__item--aira", "tl__item--sam"]);
    expect(items.map((i) => text(i.querySelector(".tl__detail")))).toEqual(["Aavin milk 500 ml", "Counted 28/30", "30 L Sakthi curd sent on WhatsApp", "Answer: yes", "network"]);
    expect(items.every((i) => i.querySelector("svg.tl__icon path")!.getAttribute("d")!.length > 10)).toBe(true);
    expect(text(items[0].querySelector(".tl__time"))).toMatch(/Vision, \d{1,2}:\d{2}/);
    expect(el.querySelector(".activity__list, .act")).toBeNull();
  });
});

describe("LivePage: room camera and record mode", () => {
  const room = [
    started,
    mon({ kind: "session", state: "start", lang: "en", uid: "camera:front", session: "cam1", ts: T0 + 500 }),
    mon({ kind: "frame", jpeg_b64: "ROOM", w: 480, h: 270, purpose: "ask", session: "cam1", ts: T0 + 84_200 }),
    frame,
  ];
  it("shows the room camera panel when a camera:<name> session exists, with a picker for several", () => {
    const d = fakeDraw();
    const el = page(stateOf(room), "live", false, vi.fn(), { drawDeps: d.deps });
    expect(text(el.querySelector(".roomcam__title"))).toBe("Room camera: front");
    expect(el.querySelector(".roomcam canvas")!.getAttribute("width")).toBe("480");
    d.tick();
    expect(d.decoded.map((f) => f.src)).toContain("data:image/jpeg;base64,ROOM");
    expect(el.querySelector(".room__pick")).toBeNull();
    cleanup();
    const two = page(stateOf([...room, mon({ kind: "session", state: "start", lang: "en", uid: "camera:fridge", session: "cam2", ts: T0 + 600 })]));
    const btns = [...two.querySelectorAll(".room__btn")];
    expect(btns.map(text)).toEqual(["front", "fridge"]);
    act(() => { (btns[1] as HTMLButtonElement).click(); });
    expect(text(two.querySelector(".roomcam__title"))).toBe("Room camera: fridge");
    expect(text(two.querySelector(".roomcam__empty"))).toBe("Connected, no picture yet.");
  });
  it("record mode is a clean 16:9 view: cameras and the last three caption lines, no cards, no header", () => {
    const onRecord = vi.fn();
    const state = stateOf([
      ...room,
      mon({ kind: "transcript", role: "owner", text: "one", lang: "en", final: true }),
      mon({ kind: "transcript", role: "aira", text: "two", lang: "en", final: true }),
      mon({ kind: "transcript", role: "customer", text: "three", lang: "ta", final: true }),
      mon({ kind: "transcript", role: "aira", text: "four", lang: "en", final: false }),
    ]);
    const el = page(state, "live", false, vi.fn(), { record: true, onRecord });
    expect(el.querySelector("[data-testid=record]")).not.toBeNull();
    expect(el.querySelector(".lv-head, .cam, .chat, .tl, .steps")).toBeNull();
    expect(el.querySelectorAll(".record__cams canvas")).toHaveLength(2);
    expect(text(el.querySelector(".record__sam .camera__age"))).toBe("0.5s ago");
    expect([...el.querySelectorAll(".caption__text")].map(text)).toEqual(["two", "three", "four"]);
    expect(el.querySelector(".caption--partial")).not.toBeNull();
    act(() => { (el.querySelector(".record__exit") as HTMLButtonElement).click(); });
    expect(onRecord).toHaveBeenCalledWith(false);
    cleanup();
    const normal = page(state, "live", false, vi.fn(), { onRecord });
    act(() => { findAllByText(normal, ".bar__btn", "Record mode")[0].click(); });
    expect(onRecord).toHaveBeenCalledWith(true);
  });
});
