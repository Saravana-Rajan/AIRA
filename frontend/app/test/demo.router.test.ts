import { describe, it, expect } from "vitest";
import { routeFor } from "../src/demo/router";

describe("routeFor", () => {
  it("routes /try and /bench, everything else to the app", () => {
    expect(routeFor("/try")).toBe("try");
    expect(routeFor("/try/")).toBe("try");
    expect(routeFor("/bench")).toBe("bench");
    expect(routeFor("/")).toBe("app");
    expect(routeFor("/trying")).toBe("app");
    expect(routeFor("")).toBe("app");
  });

  it("opens the judge demo page for /?demo=1 (overrides §6)", () => {
    expect(routeFor("/", "?demo=1")).toBe("try");
    expect(routeFor("/", "?x=2&demo=1")).toBe("try");
    expect(routeFor("/", "?demo=0")).toBe("app");
    expect(routeFor("/bench", "?demo=1")).toBe("bench");
  });
});
