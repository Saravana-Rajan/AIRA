export interface Browser { channel: string | undefined; name: string }

export function findBrowser(o: {
  env: Record<string, string | undefined>;
  chromiumPath: string | null;
  exists: (path: string) => boolean;
  platform?: string;
}): Browser | null;

export function planRun(o: {
  hasDeps: boolean;
  browser: Browser | null;
  uv: string | null;
}): { action: "run" } | { action: "skip"; reason: string };
