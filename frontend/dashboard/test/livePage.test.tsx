import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "preact/test-utils";
import type { MonitorMsg } from "@aira/contracts";
import { cleanup, findAllByText, mount, text } from "./helpers/render";
import { LivePage } from "../src/pages/LivePage";
import { initialState, reduce, type LiveState } from "../src/lib/monitor";

afterEach(cleanup);

type MonInput = MonitorMsg extends infer M ? (M extends MonitorMsg ? Omit<M, "t" | "session" | "ts"> & { ts?: number; session?: string } : never) : never;
const T0 = 1760000000000;
// The page's clock is T0 + 84.5 s; messages default to just before it, so ER 2 and MediaPipe boxes are fresh.
const mon = (m: MonInput): MonitorMsg =>
  ({ t: "mon", session: "s1", ts: T0 + 84_000, ...m }) as MonitorMsg;
const stateOf = (msgs: MonitorMsg[]): LiveState => msgs.reduce(reduce, initialState());
type Conn = "waiting" | "connecting" | "live" | "offline" | "rejected";
const page = (state: LiveState, conn: Conn = "live", keyNeeded = false, onKey = vi.fn(), extra: { record?: boolean; onRecord?: (on: boolean) => void } = {}) =>
  mount(<LivePage state={state} conn={conn} keyNeeded={keyNeeded} onKey={onKey} now={() => T0 + 84_500} {...extra} />);

const milk = { label: "Aavin milk 500 ml", box: [400, 500, 440, 560] as [number, number, number, number], point: null, score: 0.92, distance: "arm" as const };
const frame = mon({ kind: "frame", jpeg_b64: "AAAA", w: 480, h: 640, purpose: "guide", ts: T0 + 84_000 });
const started = mon({ kind: "session", state: "start", lang: "ta", uid: "u1", ts: T0 });

describe("LivePage: mission control chrome", () => {
  it("has the header, the camera stage with HUD and legend, the four cards and the bottom bar before any frame", () => {
    const el = page(initialState(), "connecting");
    expect(text(el.querySelector(".mc__title"))).toBe("AIRA Live");
    expect(text(el.querySelector(".mc__sub"))).toBe("Live agent for a blind shopkeeper");
    expect(text(el.querySelector(".steps"))).toBe("Waiting for a task");
    expect(text(el.querySelector(".mc__phone"))).toContain("Sam's phone");
    expect(findAllByText(el, ".mc__conn", "Connecting…")).toHaveLength(1);
    expect(text(el.querySelector(".mc__demo"))).toBe("Demo");
    expect([...el.querySelectorAll("h3")].map(text)).toEqual(["Target", "Session stats", "Conversation", "Activity log"]);
    expect(findAllByText(el, "p", "Waiting for Sam's phone…")).toHaveLength(1);
    expect(el.querySelector("img")).toBeNull();
    expect(findAllByText(el, ".hud__mode", "Idle")).toHaveLength(1);
    expect(text(el.querySelector(".hud__clock"))).toBe("T+00:00");
    expect([...el.querySelectorAll(".legend__item")].map(text)).toEqual(["MediaPipe (on-device)", "Gemini Robotics-ER 2", "Obstacle", "Found", "Fingertip"]);
    expect(text(el.querySelector(".engine"))).toBe("Gemini Robotics-ER 2");
    expect([...el.querySelectorAll(".bar__btn")].map(text)).toEqual(["Record mode", "Pause camera"]);
    expect(el.querySelector(".room")).toBeNull();
  });
  it("shows the session clock, language and connection once a session is open", () => {
    const el = page(stateOf([started]));
    expect(text(el.querySelector(".hud__clock"))).toBe("T+01:24");
    expect(findAllByText(el, ".hud__item", "Tamil")).toHaveLength(1);
    expect(findAllByText(el, ".mc__conn", "Session open")).toHaveLength(1);
    expect(el.querySelector(".mc__dot")!.className).toContain("mc__dot--on");
  });
  it("offers a session picker only when the shop has more than one live phone, and hands the pick back", () => {
    const onFollow = vi.fn();
    const one = mount(<LivePage state={stateOf([started])} conn="live" keyNeeded={false} onKey={vi.fn()} onFollow={onFollow} now={() => T0 + 84_500} />);
    expect(one.querySelector("select.mc__session")).toBeNull();
    const two = mount(<LivePage state={stateOf([started, mon({ kind: "session", state: "start", lang: "en", uid: "u2", session: "s2", ts: T0 + 1 })])} conn="live" keyNeeded={false} onKey={vi.fn()} onFollow={onFollow} now={() => T0 + 84_500} />);
    const select = two.querySelector<HTMLSelectElement>("select.mc__session")!;
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
  it("shows the find steps with checks and durations, and the active step", () => {
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

describe("LivePage: camera overlay", () => {
  it("draws the frame with each ER 2 box in the frame's pixel space, with a label tag and score, in the object or target tone", () => {
    const el = page(stateOf([
      frame,
      mon({ kind: "status", state: "guiding" }),
      mon({ kind: "vision", source: "er2", objects: [milk, { label: "Sakthi curd 1 L", box: null, point: [600, 200] }], target: "Aavin milk 500 ml", found: false, context: "guidance" }),
    ]));
    const img = el.querySelector<HTMLImageElement>("img.camera__img")!;
    expect(img.getAttribute("src")).toBe("data:image/jpeg;base64,AAAA");
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
    expect(findAllByText(el, ".hud__mode", "Guiding")).toHaveLength(1);
    expect(svg.querySelector("[data-tone=found]")).toBeNull();
    expect(text(el.querySelector(".camera__age"))).toBe("0.5s ago");
  });

  it("draws MediaPipe boxes thin and grey, and an obstacle red with the stage flagged", () => {
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
    expect(findAllByText(el, ".hud__mode", "Found")).toHaveLength(1);
    expect(findAllByText(el, ".act__label", "Found")).toHaveLength(1);
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

  it("pauses the camera on the last frame and says so in the HUD", () => {
    const el = page(stateOf([frame]));
    const btn = findAllByText(el, ".bar__btn", "Pause camera")[0];
    act(() => { btn.click(); });
    expect(findAllByText(el, ".bar__btn", "Resume camera")).toHaveLength(1);
    expect(findAllByText(el, ".hud__paused", "Paused")).toHaveLength(1);
    expect(el.querySelector("img.camera__img")).not.toBeNull();
  });
});

describe("LivePage: target and session stats cards", () => {
  it("shows the focus object, visibility, confidence bar, distance and direction", () => {
    const el = page(stateOf([
      mon({ kind: "vision", source: "er2", objects: [milk], target: "Aavin milk 500 ml", found: false, context: "product_find" }),
    ]));
    const card = el.querySelector(".card--target")!;
    expect(text(card.querySelector(".target__label"))).toBe("Aavin milk 500 ml in view");
    expect(card.querySelector(".target__seen")!.className).toContain("target__seen--on");
    expect(card.querySelector("[role=meter]")!.getAttribute("aria-valuenow")).toBe("92");
    expect((card.querySelector(".meter__fill") as HTMLElement).style.width).toBe("92%");
    expect(text(card.querySelector(".target__num"))).toBe("92%");
    expect(text(card)).toContain("Arm's length");
    expect(text(card)).toContain("Centre, middle shelf");
  });
  it("says what the target card is for when nothing is being looked for", () => {
    expect(text(page(initialState()).querySelector(".card--target .card__empty"))).toContain("Nothing being looked for");
  });
  it("shows the server's counters and an Executing pill while AIRA works", () => {
    const el = page(stateOf([
      started,
      mon({ kind: "stats", turns: 4, er2Calls: 3, flashCalls: 1, visionMs: 1240, fps: 2.5, ts: T0 + 8000 }),
      mon({ kind: "status", state: "thinking" }),
    ]));
    const card = el.querySelector(".card--stats")!;
    const rows = [...card.querySelectorAll(".stats > div")].map((d) => [text(d.querySelector("dt")), text(d.querySelector("dd"))]);
    expect(rows).toEqual([["Session time", "01:24"], ["Live API turns", "4"], ["ER 2 calls", "3"], ["Flash calls", "1"], ["Vision latency", "1.2s"], ["Frames/s", "2.5"]]);
    expect(findAllByText(card, ".pill", "Executing")).toHaveLength(1);
    const idle = page(stateOf([started]));
    expect(findAllByText(idle, ".pill", "Idle")).toHaveLength(1);
    expect(text(idle.querySelector(".card--stats .stats"))).toContain("—");
  });
});

describe("LivePage: conversation", () => {
  it("lists lines in order with speaker chips and language tags; partials are marked and then replaced", () => {
    const s1 = stateOf([
      mon({ kind: "transcript", role: "owner", text: "பால் எங்கே?", lang: "ta", final: true }),
      mon({ kind: "transcript", role: "customer", text: "ஒரு பால்", lang: "ta", final: true }),
      mon({ kind: "transcript", role: "aira", text: "Fridge two,", lang: "en", final: false }),
    ]);
    const el = page(s1);
    const lines = [...el.querySelectorAll("li.line")];
    expect(lines.map((l) => text(l.querySelector(".chip")))).toEqual(["Sam", "Customer", "AIRA"]);
    expect(lines.map((l) => text(l.querySelector(".lang")))).toEqual(["ta", "ta", "en"]);
    expect(lines.map((l) => text(l.querySelector(".line__text")))).toEqual(["பால் எங்கே?", "ஒரு பால்", "Fridge two,"]);
    expect(lines.map((l) => l.classList.contains("line--partial"))).toEqual([false, false, true]);
    expect(lines[0].classList.contains("line--owner")).toBe(true);
    expect(lines[1].classList.contains("line--customer")).toBe(true);
    expect(lines[2].classList.contains("line--aira")).toBe(true);
    cleanup();
    const s2 = reduce(s1, mon({ kind: "transcript", role: "aira", text: "Fridge two, middle shelf.", lang: "en", final: true }));
    const el2 = page(s2);
    const lines2 = [...el2.querySelectorAll("li.line")];
    expect(lines2).toHaveLength(3);
    expect(text(lines2[2].querySelector(".line__text"))).toBe("Fridge two, middle shelf.");
    expect(lines2[2].classList.contains("line--partial")).toBe(false);
  });
  it("says what the conversation card is for before anyone speaks", () => {
    expect(text(page(initialState()).querySelector(".talk__empty"))).toContain("Nothing said yet");
  });
});

describe("LivePage: activity log", () => {
  it("shows events oldest first with a tag, a readable label, detail and time", () => {
    const el = page(stateOf([
      mon({ kind: "event", name: "guidance_start", detail: { target: "Aavin milk 500 ml" }, ts: T0 }),
      mon({ kind: "event", name: "count", detail: { sku: "sakthi_curd_1l", counted: 28, expected: 30 }, ts: T0 + 60_000 }),
      mon({ kind: "event", name: "order_sent", detail: { summary: "30 L Sakthi curd sent on WhatsApp" }, ts: T0 + 120_000 }),
      mon({ kind: "event", name: "confirm_resolved", detail: { id: "c1", answer: "yes" }, ts: T0 + 180_000 }),
      mon({ kind: "event", name: "client_event", detail: { name: "network", data: { online: false } }, ts: T0 + 190_000 }),
    ]));
    const items = [...el.querySelectorAll("li.act")];
    expect(items.map((i) => text(i.querySelector(".act__label")))).toEqual(["Searching", "Counted", "Order sent", "Answered", "Phone event"]);
    expect(items.map((i) => text(i.querySelector(".act__tag")))).toEqual(["Vision", "Vision", "Order", "AIRA", "Sam"]);
    expect(items.map((i) => text(i.querySelector(".act__detail")))).toEqual(["Aavin milk 500 ml", "Counted 28/30", "30 L Sakthi curd sent on WhatsApp", "Answer: yes", "network"]);
    expect(text(items[0].querySelector(".act__time"))).toMatch(/\d{1,2}:\d{2}/);
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
    const el = page(stateOf(room));
    expect(text(el.querySelector(".roomcam__title"))).toBe("Room camera: front");
    expect(el.querySelector(".roomcam img")!.getAttribute("src")).toBe("data:image/jpeg;base64,ROOM");
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
    expect(el.querySelector(".mc")).toBeNull();
    expect(el.querySelector(".card")).toBeNull();
    expect(el.querySelectorAll(".record__cams img")).toHaveLength(2);
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
