// apps/app/src/bench/collector.ts
import type { ClientMsg, ServerMsg } from "@aira/contracts";

export type Metric =
  | "first_audio_ms"
  | "count_update_ms"
  | "guidance_time_to_touch_ms"
  | "sale_e2e_ms"
  | "battery_drain_pct_per_hour";

export interface Sample { metric: Metric; value: number; ts: number }

/**
 * Watches socket traffic and records the four deck latencies.
 * - first_audio_ms: user's final transcript -> first `audio` chunk after it.
 * - count_update_ms: last `frame` sent with purpose `count` -> next `count` message.
 * - guidance_time_to_touch_ms: first `target` of a guidance run -> client `fingertip_in_target`.
 * - sale_e2e_ms: `state` sale SALE_PENDING -> SALE_COMPLETED for the same sale id.
 */
export class BenchCollector {
  readonly samples: Sample[] = [];
  private userTurnEndAt: number | null = null;
  private lastCountFrameAt: number | null = null;
  private guidanceStart: number | null = null;
  private salePending = new Map<string, number>();

  private push(metric: Metric, value: number, ts: number) {
    this.samples.push({ metric, value, ts });
  }

  onOutgoing(m: ClientMsg, now: number): void {
    if (m.t === "frame" && m.purpose === "count") this.lastCountFrameAt = now;
    else if (m.t === "event" && m.name === "fingertip_in_target" && this.guidanceStart !== null) {
      this.push("guidance_time_to_touch_ms", now - this.guidanceStart, now);
      this.guidanceStart = null;
    } else if (m.t === "stop") {
      this.guidanceStart = null;
      this.userTurnEndAt = null;
    }
  }

  onIncoming(m: ServerMsg, now: number): void {
    switch (m.t) {
      case "transcript":
        if (m.role === "user" && m.final) this.userTurnEndAt = now;
        break;
      case "audio":
        if (this.userTurnEndAt !== null) {
          this.push("first_audio_ms", now - this.userTurnEndAt, now);
          this.userTurnEndAt = null;
        }
        break;
      case "count":
        if (this.lastCountFrameAt !== null) {
          this.push("count_update_ms", now - this.lastCountFrameAt, now);
          this.lastCountFrameAt = null;
        }
        break;
      case "target":
        if (this.guidanceStart === null) this.guidanceStart = now;
        break;
      case "state":
        if (m.entity !== "sale") break;
        if (m.state === "SALE_PENDING") this.salePending.set(m.id, now);
        else if (m.state === "SALE_COMPLETED") {
          const start = this.salePending.get(m.id);
          if (start !== undefined) this.push("sale_e2e_ms", now - start, now);
          this.salePending.delete(m.id);
        } else if (m.state === "SALE_VOID") this.salePending.delete(m.id);
        break;
      case "cue":
        if (m.cue === "interrupted") this.userTurnEndAt = null;
        break;
    }
  }
}
