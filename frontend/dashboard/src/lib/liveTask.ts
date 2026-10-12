// The task stepper on the Live page: which job AIRA is on and how far it has got, read off the monitor
// stream. find: Ask -> Point -> Guide -> Found · order: Propose -> Confirm -> Sent -> Vendor reply ·
// sale: Item -> Price -> Pay -> Done. Pure: `advanceTask` takes the previous task and one signal.
export type TaskKind = "find" | "order" | "sale";
export interface Step { label: string; doneAt: number | null }
export interface Task { kind: TaskKind; title: string; startedAt: number; steps: Step[] }

export const TASK_STEPS: Record<TaskKind, readonly string[]> = {
  find: ["Ask", "Point", "Guide", "Found"],
  order: ["Propose", "Confirm", "Sent", "Vendor reply"],
  sale: ["Item", "Price", "Pay", "Done"],
};
const TITLES: Record<TaskKind, string> = { find: "Finding", order: "Ordering", sale: "Selling" };

export type Signal =
  | { kind: "event"; name: string; detail: Record<string, unknown> }
  | { kind: "vision"; target: string | null; found: boolean; context: string | null; objects: number }
  | { kind: "status"; state: string };

export const isComplete = (t: Task | null): boolean => t !== null && t.steps.every((s) => s.doneAt !== null);

function start(kind: TaskKind, ts: number, title?: string): Task {
  return { kind, title: title ?? TITLES[kind], startedAt: ts, steps: TASK_STEPS[kind].map((label) => ({ label, doneAt: null })) };
}

/** Marks step `i` (and every earlier step) done at `ts`, keeping earlier timestamps. */
function done(t: Task, i: number, ts: number): Task {
  const steps = t.steps.map((s, j) => (j <= i && s.doneAt === null ? { ...s, doneAt: ts } : s));
  return steps.some((s, j) => s !== t.steps[j]) ? { ...t, steps } : t;
}

/** The step index that is done; -1 before the first. */
export function doneIndex(t: Task): number {
  let i = -1;
  t.steps.forEach((s, j) => { if (s.doneAt !== null) i = j; });
  return i;
}

const str = (v: unknown) => (typeof v === "string" ? v : "");

/** Which task a signal opens, if any, with a title naming what is being found or ordered. */
function opens(sig: Signal, ts: number): Task | null {
  if (sig.kind === "event" && sig.name === "tool_call") {
    const tool = str(sig.detail.tool);
    const args = (sig.detail.args ?? {}) as Record<string, unknown>;
    if (/^product_find|^find_product/.test(tool)) return start("find", ts, `Finding ${str(args.product) || str(args.label) || "an item"}`.trim());
    if (/^sale_start/.test(tool)) return start("sale", ts);
    if (/^order_(propose|create|draft)|^propose_order|^supplier_order/.test(tool)) return start("order", ts);
  }
  if (sig.kind === "event" && sig.name === "confirm_proposed") {
    const k = str(sig.detail.kind);
    if (k === "order") return start("order", ts);
    if (k === "sale" || k === "payment") return start("sale", ts);
  }
  return null;
}

/**
 * The next task state after one signal. A task opens on its first step (a product_find call, an order
 * proposal, a sale_start call), advances on the signals that mean its later steps happened, and is replaced
 * when a different task opens or the same one starts again after completing.
 */
export function advanceTask(t: Task | null, sig: Signal, ts: number): Task | null {
  const opened = opens(sig, ts);
  if (opened && (t === null || t.kind !== opened.kind || isComplete(t) || (opened.kind === "find" && sig.kind === "event" && sig.name === "tool_call"))) {
    t = opened;
  }
  if (t === null) return null;
  switch (t.kind) {
    case "find":
      if (sig.kind === "event" && sig.name === "tool_call" && /^product_find|^find_product/.test(str(sig.detail.tool))) return done(t, 0, ts);
      if (sig.kind === "vision" && sig.target && sig.objects > 0 && !sig.found) return done(t, 1, ts);
      if (sig.kind === "event" && sig.name === "target") return done(t, 1, ts);
      if (sig.kind === "event" && sig.name === "guidance_start") return done(t, 2, ts);
      if (sig.kind === "status" && sig.state === "guiding") return done(t, 2, ts);
      if (sig.kind === "event" && sig.name === "guidance_found") return done(t, 3, ts);
      return t;
    case "order":
      if (sig.kind === "event" && sig.name === "confirm_proposed" && str(sig.detail.kind) === "order") return done(t, 0, ts);
      if (sig.kind === "event" && sig.name === "confirm_resolved" && str(sig.detail.answer) === "yes" && doneIndex(t) === 0) return done(t, 1, ts);
      if (sig.kind === "event" && sig.name === "state" && str(sig.detail.entity) === "order" && str(sig.detail.state) === "ORDER_PLACED") return done(t, 2, ts);
      if (sig.kind === "event" && sig.name === "order_sent") return done(t, 2, ts);
      if (sig.kind === "event" && sig.name === "notify" && doneIndex(t) >= 2) return done(t, 3, ts);
      if (sig.kind === "event" && sig.name === "state" && str(sig.detail.entity) === "order" && /DELIVERY|CONFIRMED|REPLIED/.test(str(sig.detail.state))) return done(t, 3, ts);
      return t;
    case "sale":
      if (sig.kind === "event" && sig.name === "tool_call" && /^sale_(start|add)/.test(str(sig.detail.tool))) return done(t, 0, ts);
      if (sig.kind === "event" && sig.name === "state" && str(sig.detail.entity) === "sale" && str(sig.detail.state) === "SALE_PENDING") return done(t, 1, ts);
      if (sig.kind === "event" && sig.name === "confirm_proposed" && /^(sale|payment)$/.test(str(sig.detail.kind))) return done(t, 2, ts);
      if (sig.kind === "event" && sig.name === "tool_call" && /^sale_pay/.test(str(sig.detail.tool))) return done(t, 2, ts);
      if (sig.kind === "event" && sig.name === "state" && /^(sale|payment)$/.test(str(sig.detail.entity)) && /COMPLETE|CONFIRMED|PAID|DONE/.test(str(sig.detail.state))) return done(t, 3, ts);
      if (sig.kind === "event" && sig.name === "confirm_resolved" && str(sig.detail.answer) === "yes" && doneIndex(t) === 2) return done(t, 3, ts);
      return t;
  }
  return t;
}

/** "1.2s" / "14s" / "2m 05s" between two timestamps. */
export function duration(fromMs: number, toMs: number): string {
  const s = Math.max(0, (toMs - fromMs) / 1000);
  if (s < 10) return `${s.toFixed(1)}s`;
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
}

/** The time each done step took, measured from the previous done step (or the task start). */
export function stepDurations(t: Task): (string | null)[] {
  let prev = t.startedAt;
  return t.steps.map((s) => {
    if (s.doneAt === null) return null;
    const d = duration(prev, s.doneAt);
    prev = s.doneAt;
    return d;
  });
}
