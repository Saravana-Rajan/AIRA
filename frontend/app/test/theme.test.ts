import { describe, it, expect } from "vitest";
import tokensCss from "../src/ui/tokens.css?raw";
import indexHtml from "../index.html?raw";
import manifestRaw from "../public/manifest.webmanifest?raw";

const sources = import.meta.glob<string>("../src/**/*.{css,ts,tsx}", { query: "?raw", import: "default", eager: true });

/** Value of a custom property declared on :root in tokens.css. */
function token(name: string): string | undefined {
  const m = tokensCss.match(new RegExp(`${name}\\s*:\\s*([^;]+);`));
  return m?.[1].trim();
}

describe("design tokens (light theme only)", () => {
  it.each([
    ["--bg", "#FFFFFF"],
    ["--surface", "#F5F8FD"],
    ["--border", "#E3E9F4"],
    ["--text", "#0B1F4B"],
    ["--text-muted", "#4A5A7A"],
    ["--brand-blue", "#1F6FFF"],
    ["--brand-cyan", "#22C3E6"],
    ["--brand-purple", "#7B4DFF"],
    ["--brand-orange", "#FF8A3D"],
    ["--ok", "#16A34A"],
    ["--danger", "#DC2626"],
  ])("%s is %s", (name, value) => {
    expect(token(name)?.toUpperCase()).toBe(value);
  });

  it("--gradient is the logo gradient", () => {
    expect(token("--gradient")?.replace(/\s+/g, " ")).toBe(
      "linear-gradient(135deg, #22C3E6, #1F6FFF 40%, #7B4DFF 70%, #FF8A3D)",
    );
  });

  it("uses Inter with the Tamil and Devanagari Noto fonts", () => {
    expect(token("--font-sans")).toBe('"Inter", "Noto Sans Tamil", "Noto Sans Devanagari", system-ui, sans-serif');
  });

  it("sets the phone type scale: body 18px, captions 28 to 32px, status 22px", () => {
    expect(token("--text-body")).toBe("18px");
    expect(token("--text-status")).toBe("22px");
    for (const name of ["--text-caption-user", "--text-caption-aira"]) {
      const px = Number(token(name)?.replace("px", ""));
      expect(px).toBeGreaterThanOrEqual(28);
      expect(px).toBeLessThanOrEqual(32);
    }
  });

  it("declares a light color scheme", () => {
    expect(tokensCss).toMatch(/color-scheme:\s*light/);
    expect(indexHtml).toMatch(/<meta name="color-scheme" content="light"/);
  });

  it("has no dark-mode styles anywhere", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(3);
    for (const [path, text] of Object.entries(sources)) {
      expect(text, path).not.toMatch(/prefers-color-scheme/);
    }
    expect(indexHtml).not.toMatch(/prefers-color-scheme/);
  });

  it("respects reduced motion", () => {
    const css = Object.entries(sources).filter(([p]) => p.endsWith(".css")).map(([, t]) => t).join("\n");
    expect(css).toMatch(/prefers-reduced-motion:\s*reduce/);
  });
});

describe("index.html and manifest", () => {
  it("loads the fonts from Google Fonts", () => {
    expect(indexHtml).toContain("https://fonts.googleapis.com/css2?");
    for (const family of ["Inter", "Noto+Sans+Tamil", "Noto+Sans+Devanagari"]) expect(indexHtml).toContain(`family=${family}`);
  });

  it("uses a white theme colour and the AIRA logo icons", () => {
    expect(indexHtml).toMatch(/<meta name="theme-color" content="#FFFFFF"/i);
    expect(indexHtml).toContain('href="/icons/aira-logo-192.png"');
    const manifest = JSON.parse(manifestRaw);
    expect(manifest.name).toBe("AIRA");
    expect(manifest.background_color.toUpperCase()).toBe("#FFFFFF");
    expect(manifest.theme_color.toUpperCase()).toBe("#FFFFFF");
    expect(manifest.icons.map((i: { src: string }) => i.src)).toEqual(["/icons/aira-logo-192.png", "/icons/aira-logo-512.png"]);
  });
});
