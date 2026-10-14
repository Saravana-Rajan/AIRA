import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "preact/test-utils";
import type { MonitorMsg } from "@aira/contracts";
import { cleanup, findAllByText, mount, text } from "./helpers/render";
import { LivePage, type LiveAuth } from "../src/pages/LivePage";
import type { DrawDeps, Drawable } from "../src/lib/frameDraw";
import { initialState, reduce, type Frame, type LiveState } from "../src/lib/monitor";
import type { VideoState } from "../src/lib/rtcViewer";

afterEach(cleanup);
// jsdom has no media playback: the WebRTC <video>'s play() would only log "not implemented".
HTMLMediaElement.prototype.play = vi.fn(async () => {});
HTMLMediaElement.prototype.pause = vi.fn();

type MonInput = MonitorMsg extends infer M ? (M extends MonitorMsg ? Omit<M, "t" | "session" | "ts"> & { ts?: number; session?: string } : never) : never;
const T0 = 1760000000000;
// The page's clock is T0 + 84.5 s; messages default to just before it, so ER 2 and MediaPipe boxes are fresh.
const mon = (m: MonInput): MonitorMsg =>
  ({ t: "mon", session: "s1", ts: T0 + 84_000, ...m }) as MonitorMsg;
const stateOf = (msgs: MonitorMsg[]): LiveState => msgs.reduce(reduce, initialState());
type Conn = "waiting" | "connecting" | "live" | "offline" | "rejected" | "unauthenticated" | "unauthorized";
const SAM = { uid: "u-sam", email: "sam@example.com", name: "Sam" };
const auth = (status: LiveAuth["status"], extra: Partial<LiveAuth> = {}): LiveAuth =>
  ({ status, user: status === "signed_in" ? SAM : null, error: null, onSignIn: vi.fn(), onSignOut: vi.fn(), ...extra });

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

const page = (state: LiveState, conn: Conn = "live", extra: { record?: boolean; onRecord?: (on: boolean) => void; drawDeps?: DrawDeps; video?: VideoState; auth?: LiveAuth } = {}) =>
  mount(<LivePage state={state} conn={conn} now={() => T0 + 84_500} {...extra} />);

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
    const el = mount(<LivePage state={stateOf([started])} conn="live" shopName="Sunrise Dairy East" now={() => T0 + 84_500} />);
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
    const one = mount(<LivePage state={stateOf([started])} conn="live" onFollow={onFollow} now={() => T0 + 84_500} />);
    expect(one.querySelector("select.lv-session")).toBeNull();
    const two = mount(<LivePage state={stateOf([started, mon({ kind: "session", state: "start", lang: "en", uid: "u2", session: "s2", ts: T0 + 1 })])} conn="live" onFollow={onFollow} now={() => T0 + 84_500} />);
    const select = two.querySelector<HTMLSelectElement>("select.lv-session")!;
    expect(select.value).toBe("s1");
    expect([...select.options].map((o) => o.value)).toEqual(["s1", "s2"]);
    act(() => {
      select.value = "s2";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(onFollow).toHaveBeenCalledWith("s2");
  });
  it("explains a refusal by the server without mentioning any key", () => {
    const el = page(initialState(), "rejected");
    expect(text(el)).toContain("AIRA Live refused this page");
    expect(text(el)).not.toContain("key");
    expect(findAllByText(el, ".lv-conn__text", "Refused")).toHaveLength(1);
  });
  it("says what it is waiting for: not configured only without a live URL, else connecting or waiting for the phone", () => {
    const sub = (el: HTMLElement) => text(el.querySelector(".cam__empty-sub"));
    const unconfigured = mount(<LivePage state={initialState()} conn="waiting" configured={false} now={() => T0 + 84_500} />);
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
  });
  it("never shows a key field: there is no monitor key, in any connection or sign-in state", () => {
    const states: Array<[Conn, LiveAuth | undefined]> = [
      ...(["waiting", "connecting", "live", "offline", "rejected", "unauthenticated", "unauthorized"] as Conn[]).map((c) => [c, undefined] as [Conn, undefined]),
      ["waiting", auth("unconfigured")], ["waiting", auth("loading")], ["waiting", auth("signed_out")], ["live", auth("signed_in")],
      ["unauthorized", auth("signed_in")], ["unauthenticated", auth("signed_in")],
    ];
    for (const [conn, a] of states) {
      const el = page(initialState(), conn, a ? { auth: a } : {});
      expect(el.querySelector("input, form, #monitor-key, .keyprompt")).toBeNull();
      expect(text(el)).not.toMatch(/monitor key|key field/i);
      cleanup();
    }
  });
});

describe("LivePage: sign-in", () => {
  it("signed out: a Google sign-in button in the camera area (no key field), which calls onSignIn; no user in the header", () => {
    const a = auth("signed_out");
    const el = page(initialState(), "waiting", { auth: a });
    const btn = findAllByText(el, "button.signin__btn", "Sign in with Google to watch AIRA Live");
    expect(btn).toHaveLength(1);
    expect(btn[0].getAttribute("type")).toBe("button");
    expect(text(el.querySelector(".signin__title"))).toBe("Watch AIRA Live");
    expect(el.querySelector("input")).toBeNull();
    expect(el.querySelector(".cam__empty")).toBeNull(); // not "waiting for the phone": waiting for a sign-in
    expect(el.querySelector(".lv-conn__user, .lv-conn__signout")).toBeNull();
    expect(findAllByText(el, ".lv-conn__text", "Not connected")).toHaveLength(1);
    act(() => { btn[0].click(); });
    expect(a.onSignIn).toHaveBeenCalledTimes(1);
    expect(el.querySelector(".signin__error")).toBeNull();
  });
  it("shows why the last sign-in failed under the button", () => {
    const el = page(initialState(), "waiting", { auth: auth("signed_out", { error: "Google sign-in isn't enabled for this project yet. Ask the shop owner to enable it." }) });
    expect(text(el.querySelector(".signin__error"))).toContain("isn't enabled for this project yet");
    expect(el.querySelector(".signin__error")!.getAttribute("role")).toBe("alert");
  });
  it("signed in but not allowed: the exact message, who is signed in, and a sign-out", () => {
    const a = auth("signed_in");
    const el = page(initialState(), "unauthorized", { auth: a });
    expect(findAllByText(el, "[role=alert]", "This Google account isn't allowed to watch AIRA Live. Ask the shop owner to add it.")).toHaveLength(1);
    expect(text(el.querySelector(".signin__who"))).toBe("Signed in as sam@example.com");
    expect(el.querySelector("button.signin__btn")!.textContent).toBe("Sign out");
    expect(findAllByText(el, ".lv-conn__text", "Not allowed")).toHaveLength(1);
    act(() => { (el.querySelector("button.signin__btn") as HTMLButtonElement).click(); });
    expect(a.onSignOut).toHaveBeenCalledTimes(1);
    expect(text(el)).not.toContain("Waiting for Sam's phone");
  });
  it("sign-in expired: says so and offers to sign in again", () => {
    const a = auth("signed_in");
    const el = page(initialState(), "unauthenticated", { auth: a });
    expect(findAllByText(el, "[role=alert]", "Your sign-in expired.")).toHaveLength(1);
    const btn = findAllByText(el, "button.signin__btn", "Sign in again");
    expect(btn).toHaveLength(1);
    act(() => { btn[0].click(); });
    expect(a.onSignIn).toHaveBeenCalledTimes(1);
    expect(findAllByText(el, ".lv-conn__text", "Sign-in expired")).toHaveLength(1);
  });
  it("not configured: a clear note instead of a button; loading: a checking note", () => {
    const el = page(initialState(), "waiting", { auth: auth("unconfigured") });
    expect(findAllByText(el, "[role=status] p", "Live Monitor sign-in isn't configured for this dashboard.")).toHaveLength(1);
    expect(text(el)).toContain("VITE_FIREBASE_PROJECT_ID");
    expect(el.querySelector("button.signin__btn")).toBeNull();
    cleanup();
    const loading = page(initialState(), "waiting", { auth: auth("loading") });
    expect(findAllByText(loading, "p", "Checking sign-in…")).toHaveLength(1);
    expect(loading.querySelector("button.signin__btn")).toBeNull();
  });
  it("signed in and allowed: the feed's own waiting text, the viewer's email and a Sign out in the header", () => {
    const a = auth("signed_in");
    const el = page(initialState(), "live", { auth: a });
    expect(findAllByText(el, "p", "Waiting for Sam's phone…")).toHaveLength(1);
    expect(el.querySelector(".signin")).toBeNull();
    expect(text(el.querySelector(".lv-conn__user"))).toBe("sam@example.com");
    const out = el.querySelector("button.lv-conn__signout") as HTMLButtonElement;
    expect(text(out)).toBe("Sign out");
    act(() => { out.click(); });
    expect(a.onSignOut).toHaveBeenCalledTimes(1);
    cleanup();
    const withPicture = page(stateOf([frame]), "live", { auth: a });
    expect(withPicture.querySelector("canvas.camera__canvas")).not.toBeNull();
    expect(withPicture.querySelector(".signin")).toBeNull();
  });
  it("record mode shows the sign-in card in the camera slot and no header user", () => {
    const el = page(initialState(), "waiting", { auth: auth("signed_out"), record: true });
    expect(el.querySelector(".record__sam .signin__btn")).not.toBeNull();
    expect(el.querySelector(".lv-conn__signout, .lv-user")).toBeNull();
  });
  it("without an auth prop (the feed's own tests) the page behaves as signed in", () => {
    const el = page(initialState(), "connecting");
    expect(findAllByText(el, "p", "Waiting for Sam's phone…")).toHaveLength(1);
    expect(el.querySelector(".signin, .lv-user")).toBeNull();
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
    const el = page(stateOf([frame]), "live", { drawDeps: d.deps });
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

  it("plays the WebRTC video under the same overlay (mapped to the video's size) with a WebRTC badge, and no frame age", () => {
    const stream = { id: "remote" } as unknown as MediaStream;
    const d = fakeDraw();
    const el = page(stateOf([
      mon({ kind: "status", state: "guiding" }),
      mon({ kind: "vision", source: "er2", objects: [milk], target: "Aavin milk 500 ml", found: false, context: "guidance" }),
    ]), "live", { video: { mode: "webrtc", stream, session: "s1" }, drawDeps: d.deps });
    expect(el.querySelector("canvas.camera__canvas")).toBeNull();
    expect(el.querySelector("img.camera__img")).toBeNull();
    expect(d.rafs).toHaveLength(0);
    const video = el.querySelector<HTMLVideoElement>("video.camera__video")!;
    expect(video.muted).toBe(true);
    expect(video.hasAttribute("autoplay")).toBe(true);
    expect(video.hasAttribute("playsinline")).toBe(true);
    expect(el.querySelector("svg.camera__overlay")!.getAttribute("viewBox")).toBe("0 0 640 480"); // the stream's size until its metadata says otherwise
    const rect = el.querySelector("svg [data-tone=target] rect")!;
    expect([rect.getAttribute("x"), rect.getAttribute("y")]).toEqual(["320", "192"]);
    expect(text(el.querySelector("[data-testid=video-badge]"))).toBe("WebRTC");
    expect(el.querySelector(".camera__age, .cam__age")).toBeNull(); // no frame age for a live picture
    expect(el.querySelector(".cam__live")!.className).toContain("cam__live--on");
    expect(el.querySelector(".cam__empty")).toBeNull();
    expect(el.querySelector(".stage, .hud")).toBeNull();
  });

  it("shows the JPEG frame on the canvas with a Preview badge while WebRTC is not connected", () => {
    const d = fakeDraw();
    const el = page(stateOf([frame]), "live", { video: { mode: "connecting", stream: null, session: "s1" }, drawDeps: d.deps });
    expect(el.querySelector("canvas.camera__canvas")).not.toBeNull();
    expect(el.querySelector("video")).toBeNull();
    expect(d.rafs).toHaveLength(1);
    expect(text(el.querySelector("[data-testid=video-badge]"))).toBe("Preview");
    expect(text(el.querySelector(".cam__age"))).toBe("0.5s ago");
    cleanup();
    const none = page(stateOf([frame]));
    expect(none.querySelector("[data-testid=video-badge]")).toBeNull();
  });

  it("record mode plays the WebRTC video too, without a frame age", () => {
    const stream = { id: "remote" } as unknown as MediaStream;
    const el = page(stateOf([started]), "live", { record: true, video: { mode: "webrtc", stream, session: "s1" } });
    expect(el.querySelector(".record__sam video.camera__video")).not.toBeNull();
    expect(el.querySelector(".record__sam .camera__age")).toBeNull();
    expect(el.querySelector(".cam__empty")).toBeNull();
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
    const el = page(stateOf(room), "live", { drawDeps: d.deps });
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
    const el = page(state, "live", { record: true, onRecord });
    expect(el.querySelector("[data-testid=record]")).not.toBeNull();
    expect(el.querySelector(".lv-head, .cam, .chat, .tl, .steps")).toBeNull();
    expect(el.querySelectorAll(".record__cams canvas")).toHaveLength(2);
    expect(text(el.querySelector(".record__sam .camera__age"))).toBe("0.5s ago");
    expect([...el.querySelectorAll(".caption__text")].map(text)).toEqual(["two", "three", "four"]);
    expect(el.querySelector(".caption--partial")).not.toBeNull();
    act(() => { (el.querySelector(".record__exit") as HTMLButtonElement).click(); });
    expect(onRecord).toHaveBeenCalledWith(false);
    cleanup();
    const normal = page(state, "live", { onRecord });
    act(() => { findAllByText(normal, ".bar__btn", "Record mode")[0].click(); });
    expect(onRecord).toHaveBeenCalledWith(true);
  });
});
