import type { AuditLog } from "@aira/contracts";
import { toMs, type Normalized } from "../normalize";
import { formatTimeIST } from "../format";

export interface AuditRow { id: string; timeText: string; actorLabel: string; actionText: string; outcome: string; evidenceUri: string | null }

const ACTORS: Record<string, string> = { owner: "Owner", aira: "AIRA", system: "System" };

export function auditRows(logs: Normalized<AuditLog>[]): AuditRow[] {
  const at = (l: Normalized<AuditLog>) => toMs(l.at);
  return [...logs]
    .sort((a, b) => (at(b) ?? 0) - (at(a) ?? 0))
    .map((l): AuditRow => {
      const words = String(l.action ?? "").replace(/_/g, " ");
      const ms = at(l);
      return {
        id: l.id,
        timeText: ms !== null ? formatTimeIST(ms) : "",
        actorLabel: ACTORS[String(l.actor)] ?? String(l.actor),
        actionText: words.charAt(0).toUpperCase() + words.slice(1),
        outcome: String(l.outcome ?? ""),
        evidenceUri: l.evidenceUri ?? null,
      };
    });
}
