// frontend/app/test/count.ux.test.ts
import { describe, it, expect } from "vitest";
import { createCountUx } from "../src/camera/countUx";

function setup() {
  const said: string[] = []; const earcons: string[] = [];
  const progress: Array<[string, number, number | null, boolean]> = [];
  const ux = createCountUx({
    speak: (s) => said.push(s),
    earcon: (n) => earcons.push(n),
    lang: () => "en",
    onProgress: (p) => progress.push([p.sku, p.counted, p.expected, p.final]),
  });
  return { ux, said, earcons, progress };
}
const c = (counted: number, final = false, expected: number | null = 30, sku = "sakthi-curd-1l") =>
  ({ t: "count" as const, sku, counted, expected, final, confidence: 0.9 });

describe("count UX", () => {
  it("speaks each new number with a tick (ack earcon)", () => {
    const g = setup();
    g.ux.onServerMsg(c(1)); g.ux.onServerMsg(c(2));
    expect(g.said).toEqual(["1", "2"]);
    expect(g.earcons).toEqual(["ack", "ack"]);
  });
  it("does not repeat a duplicate count", () => {
    const g = setup();
    g.ux.onServerMsg(c(5)); g.ux.onServerMsg(c(5));
    expect(g.said).toEqual(["5"]);
  });
  it("reads back the final count once, with expected", () => {
    const g = setup();
    g.ux.onServerMsg(c(28, true)); g.ux.onServerMsg(c(28, true));
    expect(g.said).toEqual(["28 counted, expected 30."]);
    expect(g.earcons).toEqual(["done"]);
  });
  it("reads back without expected when the order has none (never invents one)", () => {
    const g = setup();
    g.ux.onServerMsg(c(12, true, null));
    expect(g.said).toEqual(["12 counted."]);
  });
  it("reads back in the owner's language", () => {
    const said: string[] = [];
    const ux = createCountUx({ speak: (s) => said.push(s), earcon: () => {}, lang: () => "ta" });
    ux.onServerMsg(c(28, true));
    expect(said).toEqual(["28 எண்ணப்பட்டது, எதிர்பார்த்தது 30."]);
  });
  it("ignores non-count messages", () => {
    const g = setup();
    g.ux.onServerMsg({ t: "cue", cue: "ack" });
    expect(g.said).toEqual([]);
    expect(g.progress).toEqual([]);
  });
  it("the same number for a different SKU is spoken", () => {
    const g = setup();
    g.ux.onServerMsg(c(3)); g.ux.onServerMsg(c(3, false, 10, "aavin-milk-500ml"));
    expect(g.said).toEqual(["3", "3"]);
  });
  it("a new counting run for the same SKU can read back its own final", () => {
    const g = setup();
    g.ux.onServerMsg(c(30, true));
    g.ux.onServerMsg(c(1)); // recount starts
    g.ux.onServerMsg(c(30, true));
    expect(g.said).toEqual(["30 counted, expected 30.", "1", "30 counted, expected 30."]);
  });
  it("reports progress for the status chip, without duplicates", () => {
    const g = setup();
    g.ux.onServerMsg(c(12)); g.ux.onServerMsg(c(12)); g.ux.onServerMsg(c(28, true)); g.ux.onServerMsg(c(28, true));
    expect(g.progress).toEqual([
      ["sakthi-curd-1l", 12, 30, false],
      ["sakthi-curd-1l", 28, 30, true],
    ]);
  });
  it("works without the optional progress listener", () => {
    const said: string[] = [];
    const ux = createCountUx({ speak: (s) => said.push(s), earcon: () => {}, lang: () => "en" });
    ux.onServerMsg(c(7));
    expect(said).toEqual(["7"]);
  });
});
