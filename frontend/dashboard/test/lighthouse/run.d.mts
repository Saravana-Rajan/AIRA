// Types for run.mjs (plain ESM so Node runs it without a build step).

type Env = Record<string, string | undefined>;

export declare const LIGHTHOUSE: string;
export declare const PAGES: string[];

export interface LighthouseOptions { url: string | null; pages: string[]; min: number; require: boolean; out: string | null }

export declare function parseArgs(argv: string[], env: Env): LighthouseOptions;
export declare function forwardArgs(opts: LighthouseOptions, cwd: string): string[];
export declare function pageUrl(base: string, hash: string): string;
export declare function lighthouseArgs(args: { url: string; outputPath: string }): string[];
export declare function findChrome(args: { env: Env; platform: string; exists: (path: string) => boolean }): string | null;

export type LighthousePlan =
  | { action: "skip"; reason: string }
  | { action: "remote"; url: string; chrome: string }
  | { action: "attach"; emulatorHost: string; chrome: string }
  | { action: "emulator"; java: string; firebase: string[]; chrome: string };

export declare function planLighthouse(args: {
  url: string | null;
  emulatorHost: string | null;
  emulatorUp: boolean;
  java: string | null;
  firebase: string[] | null;
  chrome: string | null;
  npx: boolean;
}): LighthousePlan;

export declare function previewEnv(emulatorPort: number): Record<string, string>;

export interface PageResult { page: string; report: unknown | null; error?: string; outputPath?: string }

export declare function summarize(results: PageResult[], min: number): { ok: boolean; lines: string[] };
