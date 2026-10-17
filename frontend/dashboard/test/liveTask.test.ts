import { describe, expect, it } from "vitest";
import { advanceTask, doneIndex, duration, isComplete, stepDurations, type Signal, type Task } from "../src/lib/liveTask";

const ev = (name: string, detail: Record<string, unknown> = {}): Signal => ({ kind: "event", name, detail });
const run = (sigs: Array<[Signal, number]>, t: Task | null = null) => sigs.reduce((acc, [s, ts]) => advanceTask(acc, s, ts), t);

describe("task stepper", () => {
  it("nothing opens a task but its first step", () => {
    expect(advanceTask(null, ev("guidance_start"), 1)).toBeNull();
    expect(advanceTask(null, { kind: "status", state: "guiding" }, 1)).toBeNull();
    expect(advanceTask(null, ev("tool_call", { tool: "stock_query" }), 1)).toBeNull();
  });

  it("find: Ask -> Point -> Guide -> Found, with a duration per step", () => {
    const t = run([
      [ev("tool_call", { tool: "product_find", args: { product: "Aavin milk" } }), 1000],
      [{ kind: "vision", target: "Aavin milk", found: false, context: "product_find", objects: 1 }, 2200],
      [ev("guidance_start"), 2500],
      [{ kind: "status", state: "guiding" }, 2600],
      [ev("guidance_found", { target: "Aavin milk" }), 9000],
    ])!;
    expect(t.kind).toBe("find");
    expect(t.title).toBe("Finding Aavin milk");
    expect(t.steps.map((s) => [s.label, s.doneAt])).toEqual([["Ask", 1000], ["Point", 2200], ["Guide", 2500], ["Found", 9000]]);
    expect(stepDurations(t)).toEqual(["0.0s", "1.2s", "0.3s", "6.5s"]);
    expect(isComplete(t)).toBe(true);
  });

  it("find: a pointing event without boxes does not count, and a new product_find restarts the task", () => {
    const t1 = run([
      [ev("tool_call", { tool: "product_find", args: { product: "curd" } }), 1],
      [{ kind: "vision", target: "curd", found: false, context: "product_find", objects: 0 }, 2],
    ])!;
    expect(doneIndex(t1)).toBe(0);
    const t2 = run([[ev("tool_call", { tool: "product_find", args: { product: "milk" } }), 10]], t1)!;
    expect(t2.title).toBe("Finding milk");
    expect(t2.startedAt).toBe(10);
    expect(doneIndex(t2)).toBe(0);
  });

  it("order: Propose -> Confirm -> Sent -> Vendor reply", () => {
    const t = run([
      [ev("confirm_proposed", { kind: "order", prompt: "Order 30 L?" }), 1],
      [ev("confirm_resolved", { answer: "yes" }), 2],
      [ev("state", { entity: "order", id: "o1", state: "ORDER_PLACED" }), 3],
      [ev("notify", { text: "Vendor replied: ok" }), 4],
    ])!;
    expect(t.kind).toBe("order");
    expect(t.steps.map((s) => s.doneAt)).toEqual([1, 2, 3, 4]);
  });

  it("order: a 'no' leaves Confirm open and a notify before Sent is not a vendor reply", () => {
    const t = run([
      [ev("confirm_proposed", { kind: "order" }), 1],
      [ev("notify", { text: "something else" }), 2],
      [ev("confirm_resolved", { answer: "no" }), 3],
    ])!;
    expect(doneIndex(t)).toBe(0);
  });

  it("sale: Item -> Price -> Pay -> Done", () => {
    const t = run([
      [ev("tool_call", { tool: "sale_start", args: {} }), 1],
      [ev("state", { entity: "sale", id: "s1", state: "SALE_PENDING" }), 2],
      [ev("confirm_proposed", { kind: "sale" }), 3],
      [ev("confirm_resolved", { answer: "yes" }), 4],
    ])!;
    expect(t.kind).toBe("sale");
    expect(t.steps.map((s) => s.doneAt)).toEqual([1, 2, 3, 4]);
    const viaState = run([
      [ev("tool_call", { tool: "sale_start" }), 1],
      [ev("tool_call", { tool: "sale_pay" }), 3],
      [ev("state", { entity: "sale", id: "s1", state: "SALE_COMPLETE" }), 5],
    ])!;
    expect(viaState.steps.map((s) => s.doneAt)).toEqual([1, 3, 3, 5]);
  });

  it("a different task replaces the current one; a completed task is replaced by the same kind", () => {
    const find = run([[ev("tool_call", { tool: "product_find", args: { product: "milk" } }), 1]])!;
    const order = run([[ev("confirm_proposed", { kind: "order" }), 5]], find)!;
    expect(order.kind).toBe("order");
    const done = run([
      [ev("confirm_resolved", { answer: "yes" }), 6],
      [ev("order_sent", {}), 7],
      [ev("notify", { text: "ok" }), 8],
    ], order)!;
    expect(isComplete(done)).toBe(true);
    const again = run([[ev("confirm_proposed", { kind: "order" }), 20]], done)!;
    expect(again.startedAt).toBe(20);
    expect(doneIndex(again)).toBe(0);
  });

  it("formats durations", () => {
    expect(duration(0, 1234)).toBe("1.2s");
    expect(duration(0, 14_000)).toBe("14s");
    expect(duration(0, 125_000)).toBe("2m 05s");
  });
});
