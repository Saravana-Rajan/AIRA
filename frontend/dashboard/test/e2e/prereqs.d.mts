// Types for prereqs.mjs (plain ESM so Node loads it without a build step).

type Env = Record<string, string | undefined>;

export declare const FIREBASE_TOOLS: string;

export declare function findJava(args: { env: Env; platform: string; works: (bin: string) => boolean }): string | null;
export declare function javaEnv(java: string | null, env: Env): Env;
export declare function findFirebase(args: { env: Env; works: (cmd: string) => boolean }): string[] | null;

export type RunPlan =
  | { action: "skip"; reason: string }
  | { action: "attach"; emulatorHost: string }
  | { action: "emulator"; java: string; firebase: string[] };

export declare function planRun(args: {
  hasDeps: boolean;
  emulatorHost: string | null;
  emulatorUp: boolean;
  java: string | null;
  firebase: string[] | null;
}): RunPlan;
