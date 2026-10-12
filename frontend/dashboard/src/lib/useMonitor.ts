// Wires the Live page to /monitor: key from the URL or the tab, the socket URL from the app's
// VITE_AIRA_LIVE_WS, one reconnecting client, and the reducer's state.
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

/** Takes ?key= off the address bar once it has been read, without reloading or touching the hash route. */
function hideKeyInAddress(): void {
  try {
    if (typeof history === "undefined" || !location.search.includes("key=")) return;
    history.replaceState(history.state, "", `${location.pathname}${stripKey(location.search)}${location.hash}`);
  } catch {
    /* a sandboxed frame: the key stays visible, which is harmless */
  }
}

export function useMonitor(enabled: boolean, shopId: string | null, env: Record<string, unknown> = import.meta.env): Monitor {
  const [key, setKeyState] = useState<string | null>(() => readKey(typeof location === "undefined" ? "" : location.search, sessionStore()));
  const [state, setState] = useState<LiveState>(initialState);
  const [conn, setConn] = useState<Connection>("waiting");
  const url = useMemo(() => (shopId && key ? monitorUrl(env, shopId, key) : null), [shopId, key]);

  useEffect(() => {
    if (key) hideKeyInAddress();
  }, [key]);

  useEffect(() => {
    if (!enabled || !url || typeof WebSocket === "undefined") {
      setConn("waiting");
      return;
    }
    return openMonitor(
      url,
      (m) => setState((s) => reduce(s, m)),
      (c) => setConn(c),
      // A WebSocket has every member of SocketLike; its handler types are just narrower (MessageEvent, CloseEvent).
      { connect: (u) => new WebSocket(u) as unknown as SocketLike },
    );
  }, [enabled, url]);

  const setKey = (k: string) => {
    storeKey(k, sessionStore());
    setKeyState(k);
  };
  return { state, conn, keyNeeded: !key, setKey };
}
