import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "preact/test-utils";
import type { MonitorMsg } from "@aira/contracts";
import { cleanup, findAllByText, mount, text } from "./helpers/render";
import { LivePage } from "../src/pages/LivePage";
import { initialState, reduce, type LiveState } from "../src/lib/monitor";

afterEach(cleanup);

type MonInput = MonitorMsg extends infer M ? (M extends MonitorMsg ? Omit<M, "t" | "session" | "ts"> & { ts?: number } : never) : never;
const mon = (m: MonInput): MonitorMsg =>
  ({ t: "mon", session: "s1", ts: 1760000000000, ...m }) as MonitorMsg;
const stateOf = (msgs: MonitorMsg[]): LiveState => msgs.reduce(reduce, initialState());
const page = (state: LiveState, conn: "waiting" | "connecting" | "live" | "offline" | "rejected" = "live", keyNeeded = false, onKey = vi.fn()) =>
  mount(<LivePage state={state} conn={conn} keyNeeded={keyNeeded} onKey={onKey} />);

const milk = { label: "Aavin milk 500 ml", box: [400, 500, 440, 560] as [number, number, number, number], point: null };
const frame = mon({ kind: "frame", jpeg_b64: "AAAA", w: 480, h: 640, purpose: "guide" });

describe("LivePage: empty state and key handling", () => {
  it("shows the waiting state with the three regions and no camera before the first frame", () => {
    const el = page(initialState(), "connecting");
    expect([...el.querySelectorAll("h2")].map(text)).toEqual(["Sam's camera", "Conversation", "Activity"]);
    expect(findAllByText(el, "p", "Waiting for Sam's phone…")).toHaveLength(1);
    expect(el.querySelector("img")).toBeNull();
    expect(findAllByText(el, "[role=status]", "Connecting…")).toHaveLength(1);
    expect(findAllByText(el, "[role=status]", "Idle")).toHaveLength(1);
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

describe("LivePage: camera overlay", () => {
  it("draws the frame with each object box in the frame's pixel space, labelled, in the object or target tone", () => {
    const el = page(stateOf([
      frame,
      mon({ kind: "status", state: "guiding" }),
      mon({ kind: "vision", source: "guidance", objects: [milk, { label: "Sakthi curd 1 L", box: null, point: [600, 200] }], target: "Aavin milk 500 ml", found: false }),
    ]));
    const img = el.querySelector<HTMLImageElement>("img.camera__img")!;
    expect(img.getAttribute("src")).toBe("data:image/jpeg;base64,AAAA");
    const svg = el.querySelector("svg.camera__overlay")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 480 640");
    const target = svg.querySelector("[data-tone=target]")!;
    expect(target.getAttribute("data-label")).toBe("Aavin milk 500 ml");
    const rect = target.querySelector("rect")!;
    expect([rect.getAttribute("x"), rect.getAttribute("y"), rect.getAttribute("width"), rect.getAttribute("height")]).toEqual(["240", "256", "28.8", "25.6"]);
    expect(text(target.querySelector("text"))).toBe("Aavin milk 500 ml");
    const other = svg.querySelector("[data-tone=object]")!;
    expect(other.querySelector("circle")!.getAttribute("cx")).toBe("96");
    expect(other.querySelector("circle")!.getAttribute("cy")).toBe("384");
    expect(findAllByText(el, "[role=status]", "Looking for Aavin milk 500 ml")).toHaveLength(1);
    expect(findAllByText(el, "[role=status]", "Guiding")).toHaveLength(1);
    expect(svg.querySelector("[data-tone=found]")).toBeNull();
  });

  it("turns the target green with a check and a Found banner when found", () => {
    const el = page(stateOf([
      frame,
      mon({ kind: "status", state: "guiding" }),
      mon({ kind: "vision", source: "guidance", objects: [milk], target: "Aavin milk 500 ml", found: true }),
    ]));
    const found = el.querySelector("svg [data-tone=found]")!;
    expect(found.getAttribute("class")).toContain("ov--found");
    expect(text(found.querySelector("text"))).toBe("✓ Aavin milk 500 ml");
    expect(findAllByText(el, "p.camera__found", "✓ Found: Aavin milk 500 ml")).toHaveLength(1);
    expect(findAllByText(el, ".status", "Found")).toHaveLength(1);
    expect(el.querySelector(".status")!.getAttribute("class")).toContain("status--found");
    expect(findAllByText(el, ".act__label", "Found")).toHaveLength(1);
  });

  it("draws the fingertip dot and a dashed line to the target while guiding", () => {
    const el = page(stateOf([
      frame,
      mon({ kind: "status", state: "guiding" }),
      mon({ kind: "vision", source: "guidance", objects: [milk], target: "Aavin milk 500 ml", found: false }),
      mon({ kind: "hand", point: [800, 100], near: false }),
    ]));
    const hand = el.querySelector("svg [data-tone=hand] circle")!;
    expect([hand.getAttribute("cx"), hand.getAttribute("cy")]).toEqual(["48", "512"]);
    const line = el.querySelector("svg line.ov--line")!;
    expect([line.getAttribute("x1"), line.getAttribute("y1"), line.getAttribute("x2"), line.getAttribute("y2")]).toEqual(["48", "512", "254.4", "268.8"]);
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
  it("says what the conversation region is for before anyone speaks", () => {
    expect(text(page(initialState()).querySelector(".talk__empty"))).toContain("Nothing said yet");
  });
});

describe("LivePage: activity", () => {
  it("shows the newest events first with a readable label, detail and time", () => {
    const el = page(stateOf([
      mon({ kind: "event", name: "guidance_start", detail: { target: "Aavin milk 500 ml" }, ts: 1760000000000 }),
      mon({ kind: "event", name: "count", detail: { sku: "sakthi_curd_1l", counted: 28, expected: 30 }, ts: 1760000060000 }),
      mon({ kind: "event", name: "order_sent", detail: { summary: "30 L Sakthi curd sent on WhatsApp" }, ts: 1760000120000 }),
      mon({ kind: "event", name: "confirm_resolved", detail: { id: "c1", answer: "yes" }, ts: 1760000180000 }),
    ]));
    const items = [...el.querySelectorAll("li.act")];
    expect(items.map((i) => text(i.querySelector(".act__label")))).toEqual(["Confirm resolved", "Order sent", "Counted", "Searching"]);
    expect(items.map((i) => text(i.querySelector(".act__detail")))).toEqual(["Answer: yes", "30 L Sakthi curd sent on WhatsApp", "Counted 28/30", "Aavin milk 500 ml"]);
    expect(items.every((i) => i.querySelector(".act__icon svg") !== null)).toBe(true);
    expect(text(items[3].querySelector(".act__time"))).toMatch(/\d{1,2}:\d{2}/);
  });
});
