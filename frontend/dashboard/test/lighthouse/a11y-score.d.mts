// Types for a11y-score.mjs (plain ESM so Node runs it without a build step).

export declare const DEFAULT_MIN: number;

export declare function checkScore(report: unknown, min: number): { ok: boolean; score: number | null };

export interface FailedAudit { id: string; title: string; weight: number }

export declare function failedAudits(report: unknown): FailedAudit[];
