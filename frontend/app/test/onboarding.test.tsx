import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { Onboarding } from "../src/ui/Onboarding";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import type { Settings } from "../src/core/types";

let root: HTMLElement;
beforeEach(() => { root = document.createElement("div"); document.body.appendChild(root); });
afterEach(() => { act(() => render(null, root)); root.remove(); });

function mount(props: { onDone(s: Settings): void; initial?: Settings; languages?: readonly string[] }) {
  act(() => { render(<Onboarding {...props} />, root); });
}
const startBtn = () => root.querySelector<HTMLButtonElement>("button.onboard__start")!;
const talkback = () => root.querySelector<HTMLInputElement>("input[type=checkbox]")!;

describe("Onboarding", () => {
  it("has no language picker: AIRA follows whoever speaks", () => {
    mount({ onDone: () => {}, languages: ["ta-IN"] });
    expect(root.querySelectorAll("input[type=radio]")).toHaveLength(0);
    expect(root.querySelector("fieldset")).toBeNull();
    expect(root.textContent).not.toContain("Pick a language");
  });

  it("writes its own labels in the phone's language (only a hint for the first line)", () => {
    mount({ onDone: () => {}, languages: ["ta-IN"] });
    expect(startBtn().textContent).toContain("தொடங்கு");
    expect(root.querySelector("main")!.getAttribute("lang")).toBe("ta");
    expect(root.textContent).toContain("எந்த மொழியிலும்");
  });

  it("falls back to English labels for a phone language it has no labels for", () => {
    mount({ onDone: () => {}, languages: ["ja-JP"] });
    expect(startBtn().textContent).toContain("Start");
    expect(root.querySelector("main")!.getAttribute("lang")).toBe("en");
    expect(root.textContent).toContain("any language");
  });

  it("one Start tap finishes with the demo shop, no fixed language and onboarded=true", () => {
    const onDone = vi.fn();
    mount({ onDone, languages: ["ta-IN"] });
    act(() => { startBtn().click(); });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith({ ...DEFAULT_SETTINGS, lang: "auto", shopId: "sunrise_dairy", onboarded: true });
  });

  it("the TalkBack switch turns on screen-reader mode", () => {
    const onDone = vi.fn();
    mount({ onDone, languages: ["en"] });
    expect(talkback().checked).toBe(false);
    act(() => { talkback().click(); });
    expect(talkback().checked).toBe(true);
    act(() => { startBtn().click(); });
    expect(onDone.mock.calls[0][0].screenReaderMode).toBe(true);
  });

  it("a double tap on Start still finishes only once", () => {
    const onDone = vi.fn();
    mount({ onDone });
    act(() => { startBtn().click(); startBtn().click(); });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("a returning owner keeps the TalkBack choice and speech rate", () => {
    const onDone = vi.fn();
    const saved: Settings = { ...DEFAULT_SETTINGS, screenReaderMode: true, speechRate: 1.4, onboarded: true };
    mount({ onDone, initial: saved, languages: ["ta-IN"] });
    expect(talkback().checked).toBe(true);
    act(() => { startBtn().click(); });
    expect(onDone).toHaveBeenCalledWith({ ...saved, onboarded: true });
  });

  it("puts focus on Start, so TalkBack reads it first and one double-tap starts", () => {
    mount({ onDone: () => {} });
    expect(document.activeElement).toBe(startBtn());
  });

  it("keeps the whole screen inside one phone-width column with Start as the last thing in it", () => {
    mount({ onDone: () => {}, languages: ["en"] });
    const col = root.querySelector(".onboard > .onboard__col")!;
    expect(col).not.toBeNull();
    expect(col.querySelector("h1")).not.toBeNull();
    expect(col.querySelector("input[type=checkbox]")).not.toBeNull();
    expect(col.lastElementChild).toBe(startBtn());
    expect(startBtn().getAttribute("aria-labelledby")).toBe("aira-start-label");
    expect(startBtn().getAttribute("aria-describedby")).toBe("aira-start-hint");
  });

  it("Start is a centred stack: gradient badge, then the title, then the subtitle", () => {
    mount({ onDone: () => {}, languages: ["en"] });
    const kids = Array.from(startBtn().children);
    expect(kids.map((k) => k.className)).toEqual(["onboard__badge", "onboard__start-label", "onboard__start-hint"]);
    const badge = kids[0];
    expect(badge.getAttribute("aria-hidden")).toBe("true");
    expect(badge.querySelector("svg.onboard__spark")).not.toBeNull();
    expect(kids[1].id).toBe("aira-start-label");
    expect(kids[1].textContent).toBe("Start");
    expect(kids[2].id).toBe("aira-start-hint");
    expect(kids[2].textContent).toBe("Turns on the microphone and camera.");
  });

  it("the intro is one short line and the header is logo, name, shop", () => {
    mount({ onDone: () => {}, languages: ["en"] });
    const intro = root.querySelectorAll(".onboard__intro");
    expect(intro).toHaveLength(1);
    expect(root.querySelector(".onboard__how")).toBeNull();
    expect(root.querySelector(".onboard__any")).toBeNull();
    expect(intro[0].textContent).toContain("Tap Start, then just talk.");
    expect(intro[0].textContent).toContain("any language");
    expect(root.querySelector(".onboard__head img")!.getAttribute("width")).toBe("48");
  });

  it("names the shop and labels every control for screen readers", () => {
    mount({ onDone: () => {}, languages: ["en"] });
    expect(root.textContent).toContain("Sunrise Dairy");
    expect(root.querySelector("h1")!.textContent).toContain("AIRA");
    for (const input of root.querySelectorAll("input")) expect(input.closest("label")).not.toBeNull();
  });
});
