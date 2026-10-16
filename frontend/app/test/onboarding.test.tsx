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
    expect(startBtn().textContent).toContain("தொடங்க தட்டவும்");
    expect(root.querySelector("main")!.getAttribute("lang")).toBe("ta");
    expect(root.textContent).toContain("எந்த மொழியிலும்");
  });

  it("falls back to English labels for a phone language it has no labels for", () => {
    mount({ onDone: () => {}, languages: ["ja-JP"] });
    expect(startBtn().textContent).toContain("Tap to start");
    expect(root.querySelector("main")!.getAttribute("lang")).toBe("en");
    expect(root.textContent).toContain("any language");
  });

  it("one Start tap finishes with no shop id, no fixed language and onboarded=true", () => {
    const onDone = vi.fn();
    mount({ onDone, languages: ["ta-IN"] });
    act(() => { startBtn().click(); });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith({ ...DEFAULT_SETTINGS, lang: "auto", onboarded: true });
    expect(onDone.mock.calls[0][0]).not.toHaveProperty("shopId");
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

  it("is one phone-width column: header, then Start, then the TalkBack row, then the Show-boxes row", () => {
    mount({ onDone: () => {}, languages: ["en"] });
    const col = root.querySelector(".onboard > .onboard__col")!;
    expect(col).not.toBeNull();
    const kids = Array.from(col.children);
    expect(kids.map((k) => k.tagName.toLowerCase())).toEqual(["header", "button", "label", "label"]);
    expect(kids[3].className).toBe("tb-switch tb-switch--boxes");
    expect(kids[0].className).toBe("onboard__head");
    expect(kids[0].querySelector("h1")!.textContent).toBe("AIRA");
    expect(kids[0].querySelector("img")!.getAttribute("width")).toBe("40");
    expect(kids[1]).toBe(startBtn());
    expect(kids[2].className).toBe("tb-switch");
    expect(kids[2].querySelector("input[type=checkbox]")).not.toBeNull();
    expect(startBtn().getAttribute("aria-labelledby")).toBe("aira-start-label");
    expect(startBtn().getAttribute("aria-describedby")).toBe("aira-start-hint");
  });

  it("Start holds the voice-screen orb, then 'Tap to start', then one muted line", () => {
    mount({ onDone: () => {}, languages: ["en"] });
    const kids = Array.from(startBtn().children);
    expect(kids.map((k) => k.className)).toEqual(["onboard__orb", "onboard__start-label", "onboard__start-hint"]);
    const orb = kids[0];
    expect(orb.getAttribute("aria-hidden")).toBe("true");
    expect(orb.querySelector(".orb .orb__core .orb__spark path")).not.toBeNull();
    expect(orb.querySelectorAll(".orb__waves .orb__wave")).toHaveLength(3);
    expect(kids[1].id).toBe("aira-start-label");
    expect(kids[1].textContent).toBe("Tap to start");
    expect(kids[2].id).toBe("aira-start-hint");
    expect(kids[2].textContent).toBe("Turns on the microphone and camera. Speak any language. AIRA answers in yours.");
  });

  it("has no dark card and no separate intro paragraph: the hint lives inside Start", () => {
    mount({ onDone: () => {}, languages: ["en"] });
    expect(root.querySelector(".onboard__intro")).toBeNull();
    expect(root.querySelector(".onboard__badge")).toBeNull();
    expect(root.querySelector(".onboard__card")).toBeNull();
    expect(root.querySelectorAll("p")).toHaveLength(0);
    expect(root.textContent!.split("any language")).toHaveLength(2); // said once, not twice
  });

  it("shows the AIRA title (no shop subheading) and labels every control for screen readers", () => {
    mount({ onDone: () => {}, languages: ["en"] });
    expect(root.querySelector("h1")!.textContent).toContain("AIRA");
    expect(root.querySelector(".onboard__shop")).toBeNull();      // the shop subheading was removed
    expect(root.textContent).not.toContain("Sunrise Dairy");
    for (const input of root.querySelectorAll("input")) expect(input.closest("label")).not.toBeNull();
  });
});
