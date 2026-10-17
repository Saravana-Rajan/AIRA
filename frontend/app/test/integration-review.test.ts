// Owner-review fixes (integration round): the phone header keeps the shop name, captions pair per turn,
// and the same sentence is never announced twice.
import { describe, it, expect, vi, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { initialScreen, reduceScreen } from "../src/ui/screenState";
import { wireApp, SPEAK_DEDUPE_MS, type WireEnv, type WiredApp } from "../src/core/wire";
import { app } from "../src/core/app";
import { DEFAULT_SETTINGS } from "../src/core/settings";
import { memStore } from "../src/net/offline-queue";

const here = dirname(fileURLToPath(import.meta.url));

describe("phone header: the shop name has priority", () => {
  const css = readFileSync(join(here, "../src/ui/app.css"), "utf8");
  const rule = (selector: string) => {
    const m = css.match(new RegExp(`${selector.replace(/[.\\s]/g, (c) => "\\" + c)}\\s*\\{([^}]*)\\}`));
    return m ? m[1] : "";
  };

  it("never truncates the name with an ellipsis", () => {
    const shop = rule(".brand__shop");
    expect(shop).not.toMatch(/text-overflow:\s*ellipsis/);
    expect(shop).not.toMatch(/white-space:\s*nowrap/);
  });

  it("lets the status chip wrap under the name on a narrow screen", () => {
    expect(rule(".topbar")).toMatch(/flex-wrap:\s*wrap/);
    expect(rule(".brand")).toMatch(/flex:\s*1\s+0\s+auto/); // the name keeps its full width; the chip moves
  });
});

describe("captions pair the right turn", () => {
  const ready = reduceScreen(initialScreen, { t: "ready", sessionId: "s", resume: "s", mock: true });

  it("a new 'You' line clears the previous AIRA reply until this turn's answer arrives", () => {
    let s = reduceScreen(ready, { t: "transcript", role: "user", text: "Count the curd delivery", final: true });
    s = reduceScreen(s, { t: "transcript", role: "aira", text: "Twenty-eight packs. Two short.", final: true });
    expect(s.airaText).toBe("Twenty-eight packs. Two short.");
    s = reduceScreen(s, { t: "transcript", role: "user", text: "Take the", final: false });
    expect(s.userText).toBe("Take the");
    expect(s.airaText).toBe(""); // the old answer is not shown next to the new question
    s = reduceScreen(s, { t: "transcript", role: "user", text: "Take the UPI payment", final: true });
    s = reduceScreen(s, { t: "transcript", role: "aira", text: "UPI received, 120 rupees.", final: true });
    expect([s.userText, s.airaText]).toEqual(["Take the UPI payment", "UPI received, 120 rupees."]);
  });

  it("a notice from AIRA replaces the last AIRA line but keeps the owner's line", () => {
    let s = reduceScreen(ready, { t: "transcript", role: "user", text: "Count the curd delivery", final: true });
    s = reduceScreen(s, { t: "transcript", role: "aira", text: "Soundbox: 120 rupees received.", final: true });
    expect([s.userText, s.airaText]).toEqual(["Count the curd delivery", "Soundbox: 120 rupees received."]);
  });
});

describe("no double speech", () => {
  const wired: WiredApp[] = [];
  afterEach(() => { wired.splice(0).forEach((w) => w.dispose()); vi.useRealTimers(); });

  function setup(screenReaderMode: boolean) {
    const env: Partial<WireEnv> = {
      createSocket: () => ({ connect() {}, send: () => true, on: () => () => {}, close() {} }) as never,
      createAudioContext: () => null,
      playEarcon: vi.fn(),
      vibrate: vi.fn(),
      speech: { speak: vi.fn(), cancel: vi.fn() },
      announce: vi.fn(),
      startMic: vi.fn(async () => ({ stop() {} })),
      queueStore: () => memStore(),
      storage: null,
      win: new EventTarget(),
      doc: Object.assign(new EventTarget(), { hidden: false }),
      requestWakeLock: vi.fn(),
      requestPermissions: vi.fn(),
    };
    const w = wireApp({ settings: { ...DEFAULT_SETTINGS, onboarded: true, screenReaderMode }, wsUrl: "ws://t/ws", connect: false, env });
    wired.push(w);
    return env;
  }

  it("announces the same sentence once to the aria-live region when it arrives twice at once", () => {
    vi.useFakeTimers();
    const env = setup(true);
    app.speak("Order 30 L Sakthi curd from Sakthi vendor?"); // the confirm prompt
    app.speak("Order 30 L Sakthi curd from Sakthi vendor?"); // the same sentence again (caption / card)
    expect(env.announce).toHaveBeenCalledTimes(1);
    app.speak("Did you send it?");
    expect(env.announce).toHaveBeenCalledTimes(2);
    vi.setSystemTime(Date.now() + SPEAK_DEDUPE_MS + 1);
    app.speak("Did you send it?"); // a real repeat later is announced again
    expect(env.announce).toHaveBeenCalledTimes(3);
  });

  it("applies the same rule to TTS", () => {
    const env = setup(false);
    app.speak("Offline");
    app.speak("Offline");
    expect(env.speech!.speak).toHaveBeenCalledTimes(1);
  });
});
