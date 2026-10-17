// The money story behind the Try AIRA scenes. AIRA checks every rupee in code against the catalog, so a
// recorded note or soundbox clip that disagrees with these numbers makes its scene fail on stage.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { SCENARIOS, DEMO_MEDIA_FILES, type Scenario } from "../src/demo/scenarios";

const HERE = dirname(fileURLToPath(import.meta.url));
const catalog = JSON.parse(readFileSync(resolve(HERE, "../../../shared/catalog/sunrise_dairy.json"), "utf8"));
const curd = catalog.products.find((p: { sku: string }) => p.sku === "sakthi_curd_1l");
const PRICE = curd.price as number;       // ₹ per litre the customer pays
const COST = curd.costPrice as number;    // ₹ per litre agreed with the vendor
const readme = readFileSync(resolve(HERE, "../public/demo/README.md"), "utf8");

const scene = (id: string): Scenario => {
  const s = SCENARIOS.find((x) => x.id === id);
  if (!s) throw new Error(`no scene ${id}`);
  return s;
};

describe("demo money story", () => {
  it("uses the catalog's Sakthi curd 1 L prices", () => {
    expect([PRICE, COST]).toEqual([60, 50]);
  });

  it("scene 6: the vendor's cash is the 10 L ordered at the agreed price", () => {
    expect(scene("order").prompt).toContain("10 litres of Sakthi curd");
    expect(scene("pay-vendor").media?.rupees).toBe(10 * COST);
  });

  it("scene 8: the customer's cash is exactly the 2 L sale from scene 7, so no change photo is needed", () => {
    expect(scene("sale-guide").prompt).toContain("2 litres of Sakthi curd");
    expect(scene("sale-cash").media?.rupees).toBe(2 * PRICE);
  });

  it("scene 9: a fresh 1 L sale, and the soundbox announces exactly its total", () => {
    const upi = scene("sale-upi");
    expect(upi.prompt).toContain("1 litre of Sakthi curd");
    expect(upi.prompt).toMatch(/UPI/);
    expect(upi.media).toMatchObject({ kind: "audio", rupees: 1 * PRICE });
  });

  it("names each money file after the amount it shows", () => {
    for (const s of SCENARIOS) {
      if (s.media?.rupees === undefined) continue;
      expect(s.media.src).toMatch(new RegExp(`_${s.media.rupees}\\.(jpg|wav)$`));
    }
  });

  it("the recording spec lists every media file the scenes use, and none of the old ones", () => {
    for (const src of DEMO_MEDIA_FILES) {
      const name = src.replace("/demo/", "");
      expect(readme).toContain(`| \`${name}\` |`);
      expect(readme).toContain(`'${name}'`);
    }
    expect(readme).not.toMatch(/cash_vendor_500x2|cash_customer_100\b/);
  });
});
