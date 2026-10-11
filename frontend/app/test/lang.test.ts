import { describe, it, expect } from "vitest";
import { langFromPhone, uiLangFor } from "../src/core/lang";

describe("langFromPhone (the hint for AIRA's very first line)", () => {
  it("maps the phone's locale list to a label language, English otherwise", () => {
    expect(langFromPhone(["ta-IN", "en-US"])).toBe("ta");
    expect(langFromPhone(["fr-FR", "hi"])).toBe("hi");
    expect(langFromPhone(["en-GB"])).toBe("en");
    expect(langFromPhone(["ja-JP"])).toBe("en");
    expect(langFromPhone([])).toBe("en");
  });
});

describe("uiLangFor (the language the server last detected)", () => {
  it("uses the labels of that language when the app has them, English for every other language", () => {
    expect(uiLangFor("ta")).toBe("ta");
    expect(uiLangFor("hi")).toBe("hi");
    expect(uiLangFor("en")).toBe("en");
    expect(uiLangFor("ja")).toBe("en");
    expect(uiLangFor("vi")).toBe("en");
    expect(uiLangFor("TA-IN")).toBe("ta");
  });
  it("keeps the current language when the server sent none", () => {
    expect(uiLangFor(undefined, "hi")).toBe("hi");
    expect(uiLangFor("", "ta")).toBe("ta");
    expect(uiLangFor(undefined)).toBe("en");
  });
});
