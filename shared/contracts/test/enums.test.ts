import { describe, it, expect } from "vitest";
import {
  LANGS,
  VERBOSITIES,
  FRAME_PURPOSES,
  CUE_NAMES,
  ENTITIES,
  CONFIRM_KINDS,
  CLIENT_EVENT_NAMES,
  ERROR_CODES,
  ORDER_STATES,
  SALE_STATES,
  PAYMENT_STATES,
  LEDGER_REASONS,
  PRODUCT_UNITS,
  DEMO_SHOP_ID,
  DEMO_SKUS,
  SUPPLIER_IDS,
  NORM_MAX,
} from "../src/index";

describe("enums match the interface registry", () => {
  it("languages, verbosity and frame purposes", () => {
    expect(LANGS).toEqual(["ta", "hi", "en"]);
    expect(VERBOSITIES).toEqual(["short", "detail"]);
    expect(FRAME_PURPOSES).toEqual(["ask", "count", "point", "identify", "invoice", "cash", "guide", "describe", "identify_held", "preview", "walk"]);
  });

  it("cue names include interrupted", () => {
    expect(CUE_NAMES).toEqual([
      "ack", "thinking", "done", "warn", "match", "mismatch", "unsure", "interrupted", "reconnect", "offline",
    ]);
  });

  it("entities, confirm kinds, client events and error codes", () => {
    expect(ENTITIES).toEqual(["order", "delivery", "sale", "payment", "inventory"]);
    expect(CONFIRM_KINDS).toEqual(["order", "payment", "sale", "discrepancy", "generic"]);
    expect(CLIENT_EVENT_NAMES).toEqual(["visibility", "network", "fingertip_in_target"]);
    expect(ERROR_CODES).toEqual(["network", "model_timeout", "camera", "permission", "unsupported", "internal"]);
  });

  it("state machines and ledger reasons", () => {
    expect(ORDER_STATES).toEqual([
      "DRAFT", "ORDER_PLACED", "DELIVERY_PENDING", "DELIVERY_RECONCILED", "DISCREPANCY",
      "PAYMENT_PENDING", "PAYMENT_CONFIRMED", "CLOSED",
    ]);
    expect(SALE_STATES).toEqual(["SALE_PENDING", "SALE_COMPLETED", "SALE_VOID"]);
    expect(PAYMENT_STATES).toEqual(["PENDING", "CONFIRMED", "FAILED"]);
    expect(LEDGER_REASONS).toEqual(["DELIVERY", "SALE", "RETURN", "ADJUST", "EXPIRED"]);
  });

  it("demo data: shop, 10 SKUs, suppliers and units", () => {
    expect(DEMO_SHOP_ID).toBe("sunrise_dairy");
    expect(DEMO_SKUS).toEqual([
      "aavin_milk_500ml", "aavin_milk_1l", "sakthi_curd_500ml", "sakthi_curd_1l", "aavin_buttermilk_200ml",
      "aavin_ghee_200ml", "aavin_ghee_500ml", "aavin_paneer_200g", "aavin_butter_100g", "arun_icecream_box",
    ]);
    expect(SUPPLIER_IDS).toEqual(["aavin_vendor", "sakthi_vendor", "arun_distributor"]);
    expect(PRODUCT_UNITS).toEqual(["L", "pack", "jar", "box"]);
    expect(NORM_MAX).toBe(1000);
  });

  it("lists are frozen at runtime", () => {
    expect(Object.isFrozen(CUE_NAMES)).toBe(true);
    expect(Object.isFrozen(DEMO_SKUS)).toBe(true);
  });
});
