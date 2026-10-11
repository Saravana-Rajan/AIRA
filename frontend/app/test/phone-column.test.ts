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

  it("Start is a rounded card inside the column that fills the rest of a phone screen, never less than 220px", () => {
    const start = block(onboardingCss, ".onboard__start");
    expect(start).toMatch(/flex:\s*1 1 auto/);
    expect(start).toMatch(/min-height:\s*220px/);
    expect(start).not.toMatch(/50dvh/); // no half-screen empty slab
    expect(start).toMatch(/border-radius:\s*var\(--onboard-radius\)/);
    expect(start).toMatch(/align-items:\s*center/);
    expect(start).toMatch(/justify-content:\s*center/);
    expect(start).toMatch(/text-align:\s*center/);
    expect(start).not.toMatch(/calc\(-1 \* var\(--gutter\)\)/); // no full-bleed negative margin
    expect(onboardingCss).toMatch(/--onboard-radius:\s*24px/);
    expect(onboardingCss).toMatch(/--onboard-start:\s*clamp\(/);
    expect(onboardingCss).toMatch(/--onboard-title:\s*clamp\(/);
  });

  it("the Start card is 240-280px tall on a wide screen, with a 64px badge, a pressed state and a focus ring", () => {
    const wide = onboardingCss.slice(onboardingCss.indexOf("@media (min-width: 600px)"));
    expect(block(wide, "  .onboard__start")).toMatch(/min-height:\s*2[4-8]0px/);
    expect(block(wide, "  .onboard__start")).toMatch(/max-height:\s*280px/);
    const badge = block(onboardingCss, ".onboard__badge");
    expect(badge).toMatch(/width:\s*64px/);
    expect(badge).toMatch(/height:\s*64px/);
    expect(badge).toMatch(/border-radius:\s*50%/);
    expect(badge).toMatch(/var\(--gradient\)/);
    expect(onboardingCss).toMatch(/\.onboard__start:active \{/);
    expect(block(onboardingCss, ".onboard__start:focus-visible")).toMatch(/outline:\s*var\(--focus-ring\)/);
    expect(block(onboardingCss, ".onboard__logo")).toMatch(/width:\s*48px/);
  });
});
