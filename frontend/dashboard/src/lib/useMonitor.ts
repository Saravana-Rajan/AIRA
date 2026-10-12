// Wires the Live page to /monitor: the key from the URL fragment or the tab, the socket URL from the app's
// VITE_AIRA_LIVE_WS, one reconnecting client that authenticates with its first message, and the reducer's
// state.
import { useEffect, useMemo, useState } from "preact/hooks";
import {
  initialState, monitorUrl, openMonitor, readKey, reduce, storeKey, stripKey, type Connection, type LiveState, type SocketLike,
} from "./monitor";

export interface Monitor { state: LiveState; conn: Connection; keyNeeded: boolean; setKey: (k: string) => void }

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

export function useMonitor(enabled: boolean, shopId: string | null, env: Record<string, unknown> = import.meta.env): Monitor {
  const [key, setKeyState] = useState<string | null>(() => readKey(typeof location === "undefined" ? "" : location.hash, sessionStore()));
  const [state, setState] = useState<LiveState>(initialState);
  const [conn, setConn] = useState<Connection>("waiting");
  const url = useMemo(() => (shopId ? monitorUrl(env, shopId) : null), [shopId]);

  useEffect(() => {
    if (key) captureMonitorKey();
  }, [key]);

  useEffect(() => {
    if (!enabled || !url || !key || typeof WebSocket === "undefined") {
      setConn("waiting");
      return;
    }
    return openMonitor(
      url,
      key,
      (m) => setState((s) => reduce(s, m)),
      (c) => setConn(c),
      // A WebSocket has every member of SocketLike; its handler types are just narrower (MessageEvent, CloseEvent).
      { connect: (u) => new WebSocket(u) as unknown as SocketLike },
    );
  }, [enabled, url, key]);

  const setKey = (k: string) => {
    storeKey(k, sessionStore());
    setKeyState(k);
  };
  return { state, conn, keyNeeded: !key, setKey };
}
