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

  it("Start is a rounded card inside the column, at least half the phone screen tall", () => {
    const start = block(onboardingCss, ".onboard__start");
    expect(start).toMatch(/min-height:\s*max\(50dvh/);
    expect(start).toMatch(/border-radius:\s*var\(--onboard-radius\)/);
    expect(start).not.toMatch(/calc\(-1 \* var\(--gutter\)\)/); // no full-bleed negative margin
    expect(onboardingCss).toMatch(/--onboard-start:\s*clamp\(/);
    expect(onboardingCss).toMatch(/--onboard-title:\s*clamp\(/);
  });
});
