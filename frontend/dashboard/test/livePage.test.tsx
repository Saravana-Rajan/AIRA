// The Live page: the header, the people selector and the selected person's camera. Nothing else: no box
// legend or overlay, no task steps, no conversation, no timeline, no record mode, no status pills.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "preact/test-utils";
import type { MonitorMsg } from "@aira/contracts";
import { cleanup, findAllByText, mount, text } from "./helpers/render";
import { LivePage, REJECTED_TEXT, type LiveAuth } from "../src/pages/LivePage";
import type { DrawDeps, Drawable } from "../src/lib/frameDraw";
import { initialState, reduce, type Frame, type LiveState } from "../src/lib/monitor";
import type { VideoState } from "../src/lib/rtcViewer";

afterEach(cleanup);
// jsdom has no media playback: the WebRTC <video>'s play() would only log "not implemented".
HTMLMediaElement.prototype.play = vi.fn(async () => {});
HTMLMediaElement.prototype.pause = vi.fn();

type MonInput = MonitorMsg extends infer M ? (M extends MonitorMsg ? Omit<M, "t" | "session" | "ts"> & { ts?: number; session?: string } : never) : never;
const T0 = 1760000000000;
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

const page = (state: LiveState, conn: Conn = "live", extra: { drawDeps?: DrawDeps; video?: VideoState; auth?: LiveAuth; configured?: boolean; onFollow?: (id: string) => void } = {}) =>
  mount(<LivePage state={state} conn={conn} now={() => T0 + 84_500} {...extra} />);

const frame = mon({ kind: "frame", jpeg_b64: "AAAA", w: 480, h: 640, purpose: "preview", ts: T0 + 84_000 });
const started = mon({ kind: "session", state: "start", lang: "ta", uid: "u1", ts: T0 });
const second = mon({ kind: "session", state: "start", lang: "en", uid: "u2", session: "s2", ts: T0 + 1, startedAt: T0 + 1 });

describe("LivePage: chrome", () => {
  it("is the header, the people selector and the camera, in that order, and nothing else", () => {
    const el = page(initialState(), "connecting");
    expect(el.querySelector("img.lv-logo")!.getAttribute("src")).toMatch(/aira-logo-192\.png$/);
    expect(text(el.querySelector(".lv-title"))).toBe("AIRA - Live");
    expect(findAllByText(el, ".lv-conn__text", "Connecting…")).toHaveLength(1);
    expect([...el.querySelectorAll(".livepage > *")].map((n) => n.className)).toEqual(["lv-head", "people", "cam"]);
    expect([...el.querySelectorAll("h3")].map(text)).toEqual(["People using AIRA now (0)", "Camera"]);
    expect(el.querySelector(".legend, .ov, svg.camera__overlay, .steps, .step, .chat, .msg, .tl, .bar, .bar__btn, .lv-pill, .room, .roomcam, .captions, [data-testid=record], .record__exit, .camera__badge")).toBeNull();
    expect(text(el)).not.toMatch(/Waiting for a task|Record mode|Pause camera|Conversation|MediaPipe|Gemini Robotics|Obstacle|Fingertip|Idle|Listening/);
    expect(el.querySelector("canvas, video")).toBeNull();
  });
  it("takes the shop's name, and shows the language and connection once a session is open", () => {
    const el = mount(<LivePage state={stateOf([started])} conn="live" shopName="Sunrise Dairy East" now={() => T0 + 84_500} />);
    expect(text(el.querySelector(".lv-title"))).toBe("Sunrise Dairy East - Live");
    expect(text(el.querySelector(".lv-conn__lang"))).toBe("Tamil");
    expect(findAllByText(el, ".lv-conn__text", "Session open")).toHaveLength(1);
    expect(el.querySelector(".lv-conn__dot")!.className).toContain("lv-conn__dot--on");
  });
  it("never shows a key field: there is no monitor key, in any connection or sign-in state", () => {
    const states: Array<[Conn, LiveAuth | undefined]> = [
      ...(["waiting", "connecting", "live", "offline", "rejected", "unauthenticated", "unauthorized"] as Conn[]).map((c) => [c, undefined] as [Conn, undefined]),
      ["waiting", auth("unconfigured")], ["waiting", auth("loading")], ["waiting", auth("signed_out")], ["live", auth("signed_in")],
      ["unauthorized", auth("signed_in")], ["unauthenticated", auth("signed_in")], ["live", auth("off")], ["unauthorized", auth("off")],
    ];
    for (const [conn, a] of states) {
      const el = page(initialState(), conn, a ? { auth: a } : {});
      expect(el.querySelector("input, form, #monitor-key, .keyprompt")).toBeNull();
      expect(text(el)).not.toMatch(/monitor key|key field/i);
      cleanup();
    }
  });
});

describe("LivePage: people using AIRA now", () => {
  it("lists the one person using AIRA, followed (the black card), with their language, since when and AIRA's status; the camera is theirs", () => {
    const el = page(stateOf([started]), "live");
    expect(text(el.querySelector(".people__title"))).toBe("People using AIRA now (1)");
    expect(el.querySelector("select.lv-session")).toBeNull();
    const cards = [...el.querySelectorAll(".person")];
    expect(cards).toHaveLength(1);
    expect(cards[0].className).toContain("person--on");
    expect(cards[0].getAttribute("aria-pressed")).toBe("true");
    expect(cards[0].getAttribute("type")).toBe("button");
    expect(text(cards[0].querySelector(".person__label"))).toBe("u1");
    expect(text(cards[0].querySelector(".person__lang"))).toBe("Tamil");
    expect(text(cards[0].querySelector(".person__since"))).toMatch(/^since \d{1,2}:\d{2} (am|pm)$/);
    expect(text(cards[0].querySelector(".person__status"))).toBe("Idle"); // no status line from it yet
    expect(el.querySelector(".people__empty")).toBeNull();
    expect(text(el.querySelector(".cam__title"))).toBe("u1's view");
  });
  it("shows everyone newest first, hands a click on another person back as onFollow, and each card's status follows that session's status lines", () => {
    const onFollow = vi.fn();
    const state = stateOf([
      started, second,
      mon({ kind: "status", state: "guiding", ts: T0 + 2 }),
      mon({ kind: "status", state: "thinking", session: "s2", ts: T0 + 3 }),
    ]);
    const el = page(state, "live", { onFollow });
    expect(text(el.querySelector(".people__title"))).toBe("People using AIRA now (2)");
    const cards = [...el.querySelectorAll<HTMLButtonElement>(".person")];
    expect(cards.map((c) => text(c.querySelector(".person__label")))).toEqual(["u2", "u1"]);
    expect(cards.map((c) => c.classList.contains("person--on"))).toEqual([false, true]);
    expect(cards.map((c) => c.getAttribute("aria-pressed"))).toEqual(["false", "true"]);
    expect(cards.map((c) => text(c.querySelector(".person__status")))).toEqual(["Thinking", "Guiding"]);
    expect(cards[0].querySelector(".person__status")!.className).toContain("person__status--thinking");
    expect(cards[1].querySelector(".person__status")!.className).toContain("person__status--guiding");
    expect(cards.map((c) => text(c.querySelector(".person__lang")))).toEqual(["English", "Tamil"]);
    act(() => { cards[0].click(); });
    expect(onFollow).toHaveBeenCalledWith("s2");
  });
  it("says so when no one is using AIRA, and never lists a room camera as a person", () => {
    const el = page(initialState(), "live");
    expect(text(el.querySelector(".people__title"))).toBe("People using AIRA now (0)");
    expect(text(el.querySelector(".people__empty"))).toBe("No one is using AIRA right now.");
    expect(el.querySelector(".person")).toBeNull();
    expect(text(el.querySelector(".cam__title"))).toBe("Camera");
    expect(findAllByText(el, "p", "Waiting for a phone…")).toHaveLength(1); // the camera's own empty state stays
    cleanup();
    const cam = page(stateOf([started, mon({ kind: "session", state: "start", lang: "en", uid: "camera:front", session: "cam1", ts: T0 + 500 })]), "live");
    expect([...cam.querySelectorAll(".person__label")].map(text)).toEqual(["u1"]);
    expect(text(cam.querySelector(".people__title"))).toBe("People using AIRA now (1)");
    expect(cam.querySelector(".room, .roomcam")).toBeNull();
  });
  it("names a session the page adopted before it was announced by its short id", () => {
    const el = page(stateOf([mon({ kind: "frame", jpeg_b64: "AAAA", w: 1, h: 1, purpose: "ask", session: "abcdef123" })]), "live");
    expect(text(el.querySelector(".cam__title"))).toBe("abcdef's view");
    expect(text(el.querySelector(".people__title"))).toBe("People using AIRA now (0)");
  });
});

describe("LivePage: camera", () => {
  it("paints the newest JPEG frame on a canvas through the drawer (one animation frame, newest frame only), large, in its own aspect ratio, with the live chip", () => {
    const d = fakeDraw();
    const el = page(stateOf([started, frame]), "live", { drawDeps: d.deps });
    const canvas = el.querySelector<HTMLCanvasElement>("canvas.camera__canvas")!;
    expect([canvas.getAttribute("width"), canvas.getAttribute("height")]).toEqual(["480", "640"]);
    expect(canvas.getAttribute("data-frame-ts")).toBe(String(T0 + 84_000));
    expect(el.querySelector<HTMLElement>(".camera")!.style.aspectRatio).toBe("480 / 640");
    expect(el.querySelector("img.camera__img, video, svg.camera__overlay, .legend")).toBeNull();
    expect(d.rafs).toHaveLength(1);
    d.tick();
    expect(d.decoded.map((f) => f.ts)).toEqual([T0 + 84_000]);
    expect(findAllByText(el, ".cam__chip", "live")).toHaveLength(1);
    expect(el.querySelector(".cam__chip")!.className).toContain("cam__chip--live");
    expect(el.querySelector(".cam__empty")).toBeNull();
    cleanup();
    const s2 = stateOf([started, frame, mon({ kind: "frame", jpeg_b64: "BBBB", w: 640, h: 480, purpose: "preview", ts: T0 + 84_300 })]);
    const e2 = fakeDraw();
    const el2 = page(s2, "live", { drawDeps: e2.deps });
    e2.tick();
    expect(e2.decoded.map((f) => f.src)).toEqual(["data:image/jpeg;base64,BBBB"]); // only the newest
    expect(el2.querySelector<HTMLElement>(".camera")!.style.aspectRatio).toBe("640 / 480");
  });

  it("plays the WebRTC video (muted, inline, autoplay) in the stream's size, with the live chip and no canvas or badge", () => {
    const stream = { id: "remote" } as unknown as MediaStream;
    const d = fakeDraw();
    const el = page(stateOf([started]), "live", { video: { mode: "webrtc", stream, session: "s1" }, drawDeps: d.deps });
    expect(el.querySelector("canvas.camera__canvas")).toBeNull();
    expect(d.rafs).toHaveLength(0);
    const video = el.querySelector<HTMLVideoElement>("video.camera__video")!;
    expect(video.muted).toBe(true);
    expect(video.hasAttribute("autoplay")).toBe(true);
    expect(video.hasAttribute("playsinline")).toBe(true);
    expect(el.querySelector<HTMLElement>(".camera")!.style.aspectRatio).toBe("640 / 480"); // the stream's size until its metadata says otherwise
    expect(findAllByText(el, ".cam__chip", "live")).toHaveLength(1);
    expect(el.querySelector(".camera__badge, [data-testid=video-badge], .cam__empty")).toBeNull();
  });

  it("shows the JPEG frame while WebRTC is only connecting", () => {
    const d = fakeDraw();
    const el = page(stateOf([started, frame]), "live", { video: { mode: "connecting", stream: null, session: "s1" }, drawDeps: d.deps });
    expect(el.querySelector("canvas.camera__canvas")).not.toBeNull();
    expect(el.querySelector("video")).toBeNull();
    expect(findAllByText(el, ".cam__chip", "live")).toHaveLength(1);
  });

  it("says what it is waiting for: not configured only without a live URL, else connecting, reconnecting or waiting for a phone; the chip says connecting while the socket is not up", () => {
    const sub = (el: HTMLElement) => text(el.querySelector(".cam__empty-sub"));
    const chip = (el: HTMLElement) => text(el.querySelector(".cam__chip"));
    const unconfigured = page(initialState(), "waiting", { configured: false });
    expect(sub(unconfigured)).toBe("AIRA Live is not configured for this dashboard.");
    expect(unconfigured.querySelector(".cam__chip")).toBeNull();
    cleanup();
    expect(sub(page(initialState(), "waiting"))).toBe("Connecting to AIRA Live…");
    cleanup();
    const connecting = page(initialState(), "connecting");
    expect(sub(connecting)).toBe("Connecting to AIRA Live…");
    expect(chip(connecting)).toBe("connecting");
    expect(connecting.querySelector(".cam__chip")!.className).toContain("cam__chip--connecting");
    cleanup();
    const offline = page(initialState(), "offline");
    expect(sub(offline)).toBe("Lost the connection to AIRA Live. Reconnecting…");
    expect(chip(offline)).toBe("connecting");
    expect(findAllByText(offline, ".lv-conn__text", "Reconnecting…")).toHaveLength(1);
    cleanup();
    const live = page(initialState(), "live");
    expect(findAllByText(live, "p", "Waiting for a phone…")).toHaveLength(1);
    expect(sub(live)).toBe("Connected. Waiting for the phone to start a session.");
    expect(live.querySelector(".cam__chip")).toBeNull();
    expect(text(live)).not.toContain("not configured");
  });

  it("a 4403 (rejected) is explained once, in the camera area, without mentioning any key, and the chip says refused", () => {
    const el = page(initialState(), "rejected");
    expect(REJECTED_TEXT).toBe("This address isn't allowed to watch AIRA Live. Open the dashboard from its own address (the server allows only its listed origins).");
    expect(text(el.querySelector(".cam__empty-title"))).toBe("AIRA Live refused this page");
    expect(text(el.querySelector(".cam__empty-sub"))).toBe(REJECTED_TEXT);
    expect(el.querySelector(".cam__pulse")).toBeNull(); // nothing is being waited for
    expect(findAllByText(el, ".cam__chip", "refused")).toHaveLength(1);
    expect(el.querySelector(".cam__chip")!.className).toContain("cam__chip--refused");
    expect(findAllByText(el, ".lv-conn__text", "Refused")).toHaveLength(1);
    expect(text(el)).not.toContain("key");
    expect(text(el)).not.toContain("Reconnecting");
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
    expect(text(el)).not.toContain("Waiting for a phone");
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
    expect(findAllByText(el, "p", "Waiting for a phone…")).toHaveLength(1);
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
  it("without an auth prop (the feed's own tests) the page behaves as signed in", () => {
    const el = page(initialState(), "connecting");
    expect(findAllByText(el, "p", "Waiting for a phone…")).toHaveLength(1);
    expect(el.querySelector(".signin, .lv-user")).toBeNull();
  });
  it("sign-in off (the default build): no gate, no user, no sign-out, the feed's own waiting text, and every refusal reads the same", () => {
    const a = auth("off");
    const el = page(initialState(), "live", { auth: a });
    expect(findAllByText(el, "p", "Waiting for a phone…")).toHaveLength(1);
    expect(el.querySelector(".signin, .signin__btn, .lv-user, .lv-conn__signout, input, form")).toBeNull();
    expect(text(el)).not.toMatch(/sign in|sign-in|signed in/i);
    cleanup();
    for (const conn of ["unauthorized", "unauthenticated"] as Conn[]) {
      const refused = page(initialState(), conn, { auth: a });
      expect(refused.querySelector(".signin, .signin__btn")).toBeNull();
      expect(text(refused.querySelector(".cam__empty-sub"))).toBe(REJECTED_TEXT);
      expect(findAllByText(refused, ".lv-conn__text", "Refused")).toHaveLength(1);
      expect(text(refused)).not.toMatch(/Google account|Sign in again|Sign-in expired|Not allowed/);
      cleanup();
    }
    const noProp = page(initialState(), "unauthorized");
    expect(text(noProp.querySelector(".cam__empty-sub"))).toBe(REJECTED_TEXT);
    expect(findAllByText(noProp, ".lv-conn__text", "Refused")).toHaveLength(1);
  });
});
