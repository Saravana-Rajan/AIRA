// Wires the Live page to /monitor: the key from the URL fragment or the tab, the socket URL from the app's
// VITE_AIRA_LIVE_WS, one reconnecting client that authenticates with its first message, and the reducer's
// state.
//
// What the page hears is sensitive (the shop's conversations and the owner's camera), so it lives in memory
// only and never outlives the connection: the reducer keeps the last frame and at most MAX_LINES transcript
// lines, and the state is dropped whenever the socket is not open (every close, the Live page being left, the
// page being unloaded (pagehide), access denied). A tab that is merely hidden stays connected. Nothing of it is
// written to any storage, the URL or the console.
// The monitor key is kept in sessionStorage for this tab only, read off the fragment once and removed from
// the address bar, forgotten when the server refuses it, and never logged.
import { useEffect, useMemo, useState } from "preact/hooks";
import {
  follow as followSession, initialState, monitorUrl, openMonitor, readKey, reduce, storeKey, stripKey,
  type ClientDeps, type Connection, type LiveState, type SocketLike,
} from "./monitor";

export interface Monitor {
  state: LiveState;
  conn: Connection;
  keyNeeded: boolean;
  /** False when the app has no usable VITE_AIRA_LIVE_WS: there is nothing to connect to. */
  configured: boolean;
  setKey: (k: string) => void;
  /** Follow another of the shop's live sessions (one of `state.sessions`); the current one's state is dropped. */
  follow: (sessionId: string) => void;
}

function sessionStore(): Storage | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null;
  }
}

/**
 * Takes #key=... off the address bar once it has been read (saved for this tab), without reloading. Called
 * from main.tsx before the first render, so the router never sees the key and it never reaches history.
 */
export function captureMonitorKey(): void {
  try {
    if (typeof location === "undefined") return;
    const key = readKey(location.hash, sessionStore());
    if (!key || !location.hash.includes("key=")) return;
    const hash = stripKey(location.hash);
    history.replaceState(history.state, "", `${location.pathname}${location.search}${hash}`);
  } catch {
    /* a sandboxed frame: the key stays visible until the next navigation, which is harmless */
  }
}

/** Forgets the key kept for this tab (a refused key, or leaving). */
export function forgetMonitorKey(): void {
  storeKey(null, sessionStore());
}

/**
 * False only while the page is being unloaded (pagehide), true again on pageshow. A monitor stays connected
 * while its tab is merely hidden (another tab or app in front): disconnecting on every tab switch made the
 * Live view drop and reconnect during a demo. Data is still memory-only and cleared on pagehide.
 */
function usePageVisible(): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const hide = () => setVisible(false);
    const show = () => setVisible(true);
    window.addEventListener("pagehide", hide);
    window.addEventListener("pageshow", show);
    return () => {
      window.removeEventListener("pagehide", hide);
      window.removeEventListener("pageshow", show);
    };
  }, []);
  return visible;
}

const browserDeps: ClientDeps = {
  // A WebSocket has every member of SocketLike; its handler types are just narrower (MessageEvent, CloseEvent).
  connect: (u) => new WebSocket(u) as unknown as SocketLike,
};

export function useMonitor(
  enabled: boolean,
  shopId: string | null,
  env: Record<string, unknown> = import.meta.env,
  deps?: ClientDeps,
): Monitor {
  const [key, setKeyState] = useState<string | null>(() => readKey(typeof location === "undefined" ? "" : location.hash, sessionStore()));
  const [state, setState] = useState<LiveState>(initialState);
  const [conn, setConn] = useState<Connection>("waiting");
  const visible = usePageVisible();
  const url = useMemo(() => (shopId ? monitorUrl(env, shopId) : null), [shopId]);

  useEffect(() => {
    if (key) captureMonitorKey();
  }, [key]);

  useEffect(() => {
    const canConnect = enabled && visible && url && key && (deps || typeof WebSocket !== "undefined");
    if (!canConnect) {
      setConn((c) => (c === "rejected" && !key ? c : "waiting"));
      setState(initialState());
      return;
    }
    const stop = openMonitor(
      url,
      key,
      (m) => setState((s) => reduce(s, m)),
      (c) => {
        setConn(c);
        if (c !== "live") setState(initialState()); // nothing of a session is kept while the socket is not open
        if (c === "rejected") {
          forgetMonitorKey(); // a refused key is not kept for a retry
          setKeyState(null);
        }
      },
      deps ?? browserDeps,
    );
    return () => {
      stop();
      setState(initialState());
    };
  }, [enabled, visible, url, key, deps]);

  const setKey = (k: string) => {
    storeKey(k, sessionStore());
    setKeyState(k);
  };
  const follow = (sessionId: string) => setState((s) => followSession(s, sessionId));
  return { state, conn, keyNeeded: !key, configured: url !== null, setKey, follow };
}
