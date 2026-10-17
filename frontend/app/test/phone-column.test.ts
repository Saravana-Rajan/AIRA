import { describe, it, expect } from "vitest";
import onboardingCss from "../src/ui/onboarding.css?raw";
import tryCss from "../src/demo/try.css?raw";
import benchCss from "../src/bench/bench.css?raw";

/** The CSS block for one selector (first match). */
function block(css: string, selector: string): string {
  const i = css.indexOf(`\n${selector} {`);
  expect(i, `${selector} block`).toBeGreaterThan(-1);
  return css.slice(i, css.indexOf("}", i));
}

describe("one centred phone-width column on Start, /try and /bench", () => {
  it.each([
    ["onboarding", onboardingCss, ".onboard__col"],
    ["try", tryCss, ".try"],
    ["bench", benchCss, ".bench"],
  ])("%s column is capped at --phone-width and centred", (_name, css, selector) => {
    const b = block(css, selector);
    expect(b).toMatch(/max-width:\s*var\(--phone-width\)/);
    expect(b).toMatch(/width:\s*100%/);
    expect(css).toMatch(/@media \(min-width: 600px\)/);
  });

  it("every page uses the shared gutter and never scrolls sideways", () => {
    for (const css of [onboardingCss, tryCss, benchCss]) {
      expect(css).toMatch(/var\(--gutter\)/);
      expect(css).toMatch(/overflow-x:\s*hidden/);
      expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i);
      expect(css).not.toMatch(/prefers-color-scheme/);
      expect(css).toMatch(/prefers-reduced-motion:\s*reduce/);
    }
  });

  it("Start is transparent, fills the free height of the column and centres the orb and text", () => {
    const start = block(onboardingCss, ".onboard__start");
    expect(start).toMatch(/flex:\s*1 1 auto/);
    expect(start).toMatch(/width:\s*100%/);
    expect(start).toMatch(/background:\s*transparent/);
    expect(start).not.toMatch(/var\(--text\)\s*;\s*\n\s*color/); // no navy slab
    expect(start).not.toMatch(/gradient/);
    expect(start).not.toMatch(/50dvh/);
    expect(start).toMatch(/align-items:\s*center/);
    expect(start).toMatch(/justify-content:\s*center/);
    expect(start).toMatch(/text-align:\s*center/);
    expect(start).not.toMatch(/calc\(-1 \* var\(--gutter\)\)/); // no full-bleed negative margin
    expect(onboardingCss).toMatch(/--onboard-start:\s*clamp\(/);
    expect(onboardingCss).toMatch(/--onboard-title:\s*24px/);
    expect(onboardingCss).toMatch(/--onboard-shop:\s*15px/);
    expect(onboardingCss).toMatch(/--onboard-hint:\s*15px/);
  });

  it("the orb is the voice-screen orb at clamp(150px, 46vw, 210px), idle-pulsing, still under reduced motion", () => {
    const orb = block(onboardingCss, ".onboard__orb");
    expect(orb).toMatch(/--orb-size:\s*clamp\(150px, 46vw, 210px\)/);
    expect(onboardingCss).toMatch(/\.onboard\s*\{[^}]*--state:\s*var\(--brand-blue\)/);
    expect(block(onboardingCss, ".onboard__orb .orb__core")).toMatch(/animation:\s*onboard-idle/);
    expect(onboardingCss).toMatch(/@keyframes onboard-idle/);
    const calm = onboardingCss.slice(onboardingCss.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(block(calm, "  .onboard__orb .orb__core")).toMatch(/animation:\s*none/);
    expect(block(calm, "  .onboard__start:active .onboard__orb")).toMatch(/transform:\s*none/);
  });

  it("pressing scales the orb, focus draws a ring, the logo is 40px and TalkBack has a hairline above", () => {
    expect(block(onboardingCss, ".onboard__start:active .onboard__orb")).toMatch(/transform:\s*scale\(0\.9\d\)/);
    expect(block(onboardingCss, ".onboard__start:focus-visible")).toMatch(/outline:\s*var\(--focus-ring\)/);
    expect(block(onboardingCss, ".onboard__logo")).toMatch(/width:\s*40px/);
    const tb = block(onboardingCss, ".tb-switch");
    expect(tb).toMatch(/border-top:\s*1px solid var\(--border\)/);
    expect(tb).not.toMatch(/border-bottom/);
    expect(block(onboardingCss, ".onboard__col")).toMatch(/env\(safe-area-inset-bottom\)/);
  });
});
