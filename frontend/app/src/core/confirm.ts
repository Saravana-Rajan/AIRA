import type { ClientMsg } from "@aira/contracts";

/** Same lifetime as a server proposal (aira_live.confirm.PROPOSAL_TTL_S = 120). */
export const CONFIRM_TTL_MS = 120_000;

/**
 * The one confirmation the owner can answer with a gesture.
 * Double-tap = yes, long-press = no. Each pending confirm is answered at most once;
 * after an answer, or once it expires, gestures go back to talk/stop.
 */
export class ConfirmState {
  private p: { id: string; kind: string; at: number } | null = null;

  constructor(private now: () => number = () => Date.now(), private ttlMs = CONFIRM_TTL_MS) {}

  /** A newer confirm replaces any older one. */
  set(id: string, kind: string): void { this.p = { id, kind, at: this.now() }; }

  clear(): void { this.p = null; }

  get pending(): { id: string; kind: string } | null {
    if (this.p && this.now() - this.p.at > this.ttlMs) this.p = null;
    return this.p ? { id: this.p.id, kind: this.p.kind } : null;
  }

  /** Returns the confirm reply for a gesture, or null if nothing is pending (caller then talks/stops). */
  onGesture(g: "double" | "long"): ClientMsg | null {
    const p = this.pending;
    if (!p) return null;
    this.p = null;
    return { t: "confirm", id: p.id, answer: g === "double" ? "yes" : "no" };
  }
}
