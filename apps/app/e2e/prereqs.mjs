// Decides whether the phone app's browser test can run here. Pure: every check that touches the machine
// (a file exists, a command runs) is passed in, so the decisions are unit-tested (test/e2e.prereqs.test.ts).

/** Google Chrome installs that Playwright's "chrome" channel can drive when its own Chromium is missing. */
const CHROME_CANDIDATES = {
  darwin: ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
  linux: ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome"],
  win32: [
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ],
};

/**
 * The browser to run with: AIRA_E2E_CHANNEL, else Playwright's own Chromium (channel undefined), else the
 * machine's Google Chrome (channel "chrome"). Null when none is installed.
 * @returns {{ channel: string | undefined, name: string } | null}
 */
export function findBrowser({ env, chromiumPath, exists, platform = "darwin" }) {
  if (env.AIRA_E2E_CHANNEL) return { channel: env.AIRA_E2E_CHANNEL, name: env.AIRA_E2E_CHANNEL };
  if (chromiumPath && exists(chromiumPath)) return { channel: undefined, name: "Playwright Chromium" };
  const chrome = (CHROME_CANDIDATES[platform] ?? []).find((p) => exists(p));
  return chrome ? { channel: "chrome", name: "Google Chrome" } : null;
}

/**
 * run: everything is here. skip: say why and exit 0, so a machine without a browser still passes `npm test`.
 * @returns {{ action: "run" } | { action: "skip", reason: string }}
 */
export function planRun({ hasDeps, browser, uv }) {
  if (!hasDeps) {
    return { action: "skip", reason: "the e2e dev dependencies are not installed (@playwright/test, @axe-core/playwright)" };
  }
  if (!browser) {
    return { action: "skip", reason: "no browser: run `npx playwright install chromium` (or install Google Chrome)" };
  }
  if (!uv) return { action: "skip", reason: "uv is not installed; the mock backend (services/live) runs with it" };
  return { action: "run" };
}
