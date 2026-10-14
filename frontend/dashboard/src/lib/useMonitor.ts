// Wires the Live page to /monitor: the socket URL from the app's VITE_AIRA_LIVE_WS, one reconnecting client
// and the reducer's state. There is no monitor key (removed by the owner's decision): the page connects as soon
// as it is open, and the server's only guard is its browser Origin allowlist.
//
// What the page hears is sensitive (the shop's conversations and the owner's camera), so it lives in memory
// only and never outlives the connection: the reducer keeps the last frame and at most MAX_LINES transcript
// lines, and the state is dropped whenever the socket is not open (every close, the Live page being left, the
// page being unloaded (pagehide), access denied). A tab that is merely hidden stays connected. Nothing of it is
// written to any storage, the URL or the console.
//
// Live video: a WebRTC viewer (lib/rtcViewer) rides on the same socket for signalling. It asks the followed
// session for video, and its state (`video`) says whether the page shows the peer-to-peer picture or the JPEG
// preview. It is stopped (bye, connection closed) whenever the socket is dropped, so nothing outlives the page.
import { useEffect, useMemo, useState } from "preact/hooks";
import {
  follow as followSession, initialState, monitorUrl, openMonitor, reduce,
  type ClientDeps, type Connection, type LiveState, type SocketLike,
} from "./monitor";
import { createRtcViewer, type PeerCtor, type VideoState } from "./rtcViewer";

export interface Monitor {
  state: LiveState;
  conn: Connection;
  /** False when the app has no usable VITE_AIRA_LIVE_WS: there is nothing to connect to. */
  configured: boolean;
  /** Follow another of the shop's live sessions (one of `state.sessions`); the current one's state is dropped. */
  follow: (sessionId: string) => void;
  /** The WebRTC picture: "webrtc" with a stream to play, else the JPEG preview ("connecting" while trying). */
  video: VideoState;
}

/** Test hooks: the socket factory and timers, plus a fake RTCPeerConnection for the viewer. */
export type MonitorDeps = ClientDeps & { peer?: PeerCtor };

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
  deps?: MonitorDeps,
): Monitor {
  const [state, setState] = useState<LiveState>(initialState);
  const [conn, setConn] = useState<Connection>("waiting");
  const visible = usePageVisible();
  const url = useMemo(() => (shopId ? monitorUrl(env, shopId) : null), [shopId]);
  const viewer = useMemo(
    () => createRtcViewer({ ...(deps?.peer ? { PeerConnection: deps.peer } : {}), setTimer: deps?.setTimer, clearTimer: deps?.clearTimer }),
    [deps],
  );
  const [video, setVideo] = useState<VideoState>(() => viewer.state());
  useEffect(() => viewer.subscribe(setVideo), [viewer]);
  useEffect(() => {
    viewer.want(state.session); // the followed session (null once it ends): video follows it
  }, [viewer, state.session]);

  useEffect(() => {
    const canConnect = enabled && visible && url && (deps || typeof WebSocket !== "undefined");
    if (!canConnect) {
      setConn("waiting");
      setState(initialState());
      return;
    }
    const stop = openMonitor(
      url,
      (m) => setState((s) => reduce(s, m)),
      (c) => {
        setConn(c);
        if (c !== "live") setState(initialState()); // nothing of a session is kept while the socket is not open
      },
      { ...(deps ?? browserDeps), signal: viewer },
    );
    return () => {
      viewer.stop(); // bye while the socket is still open, then the connection is closed
      stop();
      setState(initialState());
    };
  }, [enabled, visible, url, deps]);

  const follow = (sessionId: string) => setState((s) => followSession(s, sessionId));
  return { state, conn, configured: url !== null, follow, video };
}
