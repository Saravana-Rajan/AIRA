// apps/app/src/bench/trials.ts
// Hand-guidance trial log for Sarmitha's bench/score_guidance.py.
// Output file: bench/data/guidance/trials.json
//   {"trials":[{"trial":1,"product":"sakthi_curd_1l","startedAtMs":0,"touchedAtMs":4200,"correctItem":true}]}
import type { ClientMsg, ServerMsg } from "@aira/contracts";

export interface GuidanceTrial {
  trial: number;
  product: string;
  startedAtMs: number;
  touchedAtMs: number | null;
  correctItem: boolean;
}

/** A trial with no touch within this window is a timeout (touchedAtMs = null). */
export const TRIAL_TIMEOUT_MS = 30_000;

interface Open { product: string; startedAtMs: number }
interface AwaitingVerdict { product: string; startedAtMs: number; touchedAtMs: number }

export class GuidanceTrialLog {
  readonly trials: GuidanceTrial[] = [];
  private product: string | null = null;
  private open: Open | null = null;
  private awaiting: AwaitingVerdict | null = null;

  /** @param lastTrial number of the last saved trial, so numbering continues across reloads. */
  constructor(private lastTrial = 0) {}

  /** The SKU being tested (e.g. "sakthi_curd_1l"). Falls back to the target label when unset. */
  setProduct(sku: string | null): void {
    this.product = sku && sku.trim() ? sku.trim() : null;
  }

  private record(product: string, startedAtMs: number, touchedAtMs: number | null, correctItem: boolean) {
    this.lastTrial += 1;
    this.trials.push({ trial: this.lastTrial, product, startedAtMs, touchedAtMs, correctItem });
  }

  /** Closes an untouched run once it has passed the timeout. Call on every message and on a timer. */
  tick(now: number): void {
    if (this.open && now - this.open.startedAtMs > TRIAL_TIMEOUT_MS) {
      this.record(this.open.product, this.open.startedAtMs, null, false);
      this.open = null;
    }
  }

  private verdict(correct: boolean) {
    if (!this.awaiting) return;
    this.record(this.awaiting.product, this.awaiting.startedAtMs, this.awaiting.touchedAtMs, correct);
    this.awaiting = null;
  }

  onOutgoing(m: ClientMsg, now: number): void {
    this.tick(now);
    if (m.t === "event" && m.name === "fingertip_in_target" && this.open) {
      // A touch whose verdict never arrived counts as not confirmed.
      if (this.awaiting) this.verdict(false);
      this.awaiting = { ...this.open, touchedAtMs: now };
      this.open = null;
    } else if (m.t === "stop") {
      // A run the tester stopped before the timeout is discarded, like the latency metric.
      this.open = null;
    }
  }

  onIncoming(m: ServerMsg, now: number): void {
    this.tick(now);
    if (m.t === "target") {
      if (!this.open) this.open = { product: this.product ?? m.label, startedAtMs: now };
    } else if (m.t === "cue") {
      if (m.cue === "match") this.verdict(true);
      else if (m.cue === "mismatch") this.verdict(false);
    } else if (m.t === "transcript" && m.role === "aira" && m.final && m.text.includes("✓")) {
      this.verdict(true);
    }
  }
}

/** Serializes to exactly the shape bench/score_guidance.py reads. */
export function toTrialsJson(trials: GuidanceTrial[]): string {
  return JSON.stringify({
    trials: trials.map((t) => ({
      trial: t.trial,
      product: t.product,
      startedAtMs: t.startedAtMs,
      touchedAtMs: t.touchedAtMs,
      correctItem: t.correctItem,
    })),
  }, null, 2) + "\n";
}
