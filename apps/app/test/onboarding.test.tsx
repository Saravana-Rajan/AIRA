import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { Onboarding, langFromPhone } from "../src/ui/Onboarding";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import type { Settings } from "../src/core/types";

let root: HTMLElement;
beforeEach(() => { root = document.createElement("div"); document.body.appendChild(root); });
afterEach(() => { act(() => render(null, root)); root.remove(); });

function mount(props: { onDone(s: Settings): void; initial?: Settings; languages?: readonly string[] }) {
  act(() => { render(<Onboarding {...props} />, root); });
}
const startBtn = () => root.querySelector<HTMLButtonElement>("button.onboard__start")!;
const radio = (l: string) => root.querySelector<HTMLInputElement>(`input[type=radio][value=${l}]`)!;
const talkback = () => root.querySelector<HTMLInputElement>("input[type=checkbox]")!;

describe("langFromPhone", () => {
  it("maps the phone's languages to ta, hi or en", () => {
    expect(langFromPhone(["ta-IN", "en-US"])).toBe("ta");
    expect(langFromPhone(["fr-FR", "hi"])).toBe("hi");
    expect(langFromPhone(["en-GB"])).toBe("en");
    expect(langFromPhone(["fr-FR"])).toBe("en");
    expect(langFromPhone([])).toBe("en");
  });
});

describe("Onboarding", () => {
  it("offers Tamil, Hindi and English in their own scripts, picking the phone's language first", () => {
    mount({ onDone: () => {}, languages: ["ta-IN"] });
    expect(root.querySelectorAll("input[type=radio]")).toHaveLength(3);
    expect(root.textContent).toContain("தமிழ்");
    expect(root.textContent).toContain("हिन्दी");
    expect(root.textContent).toContain("English");
    expect(radio("ta").checked).toBe(true);
    expect(startBtn().textContent).toContain("தொடங்கு");
    expect(root.querySelector("main")!.getAttribute("lang")).toBe("ta");
  });

  it("switches its own labels to the language picked", () => {
    mount({ onDone: () => {}, languages: ["en-IN"] });
    expect(startBtn().textContent).toContain("Start");
    act(() => { radio("hi").click(); });
    expect(radio("hi").checked).toBe(true);
    expect(startBtn().textContent).toContain("शुरू करें");
    expect(root.querySelector("main")!.getAttribute("lang")).toBe("hi");
  });

  it("one Start tap finishes with the demo shop, the language and onboarded=true", () => {
    const onDone = vi.fn();
    mount({ onDone, languages: ["en-IN"] });
    act(() => { radio("ta").click(); });
    act(() => { startBtn().click(); });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith({ ...DEFAULT_SETTINGS, lang: "ta", shopId: "murugan_dairy", onboarded: true });
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

  it("a returning owner keeps the saved language and TalkBack choice, not the phone's language", () => {
    const onDone = vi.fn();
    const saved: Settings = { ...DEFAULT_SETTINGS, lang: "hi", screenReaderMode: true, speechRate: 1.4, onboarded: true };
    mount({ onDone, initial: saved, languages: ["ta-IN"] });
    expect(radio("hi").checked).toBe(true);
    expect(talkback().checked).toBe(true);
    act(() => { startBtn().click(); });
    expect(onDone).toHaveBeenCalledWith({ ...saved, onboarded: true });
  });

  it("puts focus on Start, so TalkBack reads it first and one double-tap starts", () => {
    mount({ onDone: () => {} });
    expect(document.activeElement).toBe(startBtn());
  });

  it("names the shop and groups the languages for screen readers", () => {
    mount({ onDone: () => {}, languages: ["en"] });
    expect(root.textContent).toContain("Murugan Dairy");
    const fs = root.querySelector("fieldset")!;
    expect(fs.querySelector("legend")!.textContent).toBe("Language");
    expect(root.querySelector("h1")!.textContent).toContain("AIRA");
    // every control is labelled
    for (const input of root.querySelectorAll("input")) expect(input.closest("label")).not.toBeNull();
  });
});
