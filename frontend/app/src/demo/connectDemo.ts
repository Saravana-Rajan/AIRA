import type { ClientMsg } from "@aira/contracts";
import { app } from "../core/app";
import { DEFAULT_SETTINGS } from "../core/settings";
import type { WireOpts } from "../core/wire";
import { memStore } from "../net/offline-queue";
import { demoHello } from "./hello";

type DemoSocket = { close(): void; connect(h: Extract<ClientMsg, { t: "hello" }>): void };

/**
 * Reconnects the shared socket as a judge on the seeded demo shop.
 * No Firebase Auth (overrides §1): the hello carries no idToken and the
 * server accepts it because the deployed service sets AIRA_ALLOW_NO_TOKEN=1.
 */
export async function connectDemo(appVersion: string, socket: DemoSocket | undefined = app.socket): Promise<void> {
  if (!socket) throw new Error("AIRA is not connected yet: wire the app before connectDemo().");
  socket.close();
  socket.connect(demoHello(appVersion));
}

const noop = () => {};

/**
 * Options for the real wireApp() on the judge page. Everything is wired (audio playback,
 * earcons, mic, confirm state, request_frame via onWired) except:
 * - no owner hello (connect: false): connectDemo() says hello as a judge instead;
 * - no camera/mic prompt and no wake lock up front: demo media stands in for the camera
 *   and the mic is asked for only when the judge presses Talk;
 * - nothing saved: the owner's settings (storage: null) and offline ops (in-memory queue)
 *   on this browser are never read or overwritten.
 */
export function demoWireOpts(wsUrl?: string): WireOpts {
  return {
    settings: { ...DEFAULT_SETTINGS, lang: "auto", verbosity: "short", onboarded: true },
    wsUrl,
    connect: false,
    env: {
      storage: null,
      queueStore: () => memStore(),
      requestPermissions: noop,
      requestWakeLock: noop,
    },
  };
}
