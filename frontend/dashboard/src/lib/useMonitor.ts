// Wires the Live page to /monitor: the socket URL from the app's VITE_AIRA_LIVE_WS, one reconnecting client,
// and the reducer's state. By default (VITE_MONITOR_AUTH unset) there is no sign-in: the page connects as
// soon as it is open, the server streams every live session of the shop, and lib/firebaseAuth is never
// loaded (its dynamic import below is only reachable in the sign-in build, so it stays an unfetched chunk).
// With VITE_MONITOR_AUTH=1 the viewer's Google sign-in (lib/viewerAuth; lib/firebaseAuth in the browser,
// loaded only here and only with a Firebase config) is required: the client authenticates with its first
// message and connects only while a viewer is signed in; the server checks the account against its viewer
// list. An injected fake sign-in (tests) counts only in that build: the flag decides.
//
// What the page hears is sensitive (the shop's conversations and the owner's camera), so it lives in memory
// only and never outlives the connection: the reducer keeps the last frame and at most MAX_LINES transcript
// lines, and the state is dropped whenever the socket is not open (every close, the Live page being left, the
// page being unloaded (pagehide), signing out, access denied). A tab that is merely hidden stays connected.
// Nothing of it, and never the ID token, is written to any storage, the URL or the console.
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
import { authConfigured, authErrorText, monitorAuthRequired, type AuthStatus, type ViewerAuth, type ViewerUser } from "./viewerAuth";

export interface MonitorAuth {
  status: AuthStatus;
  user: ViewerUser | null;
  /** Why the last sign-in attempt failed, in words for the page; null otherwise. */
  error: string | null;
  signIn: () => void;
  signOut: () => void;
}

export interface Monitor {
  state: LiveState;
  conn: Connection;
  /** False when the app has no usable VITE_AIRA_LIVE_WS: there is nothing to connect to. */
  configured: boolean;
  auth: MonitorAuth;
  /** Follow another of the shop's live sessions (one of `state.sessions`); the current one's state is dropped. */
  follow: (sessionId: string) => void;
  /** The WebRTC picture: "webrtc" with a stream to play, else the JPEG preview ("connecting" while trying). */
  video: VideoState;
}

/** Test hooks: the socket factory and timers, a fake RTCPeerConnection for the viewer, and a fake sign-in. */
export type MonitorDeps = ClientDeps & { peer?: PeerCtor; auth?: ViewerAuth };

/** The browser sign-in, loaded on first use (one promise for the page's life). */
let browserAuth: Promise<ViewerAuth> | null = null;
function loadBrowserAuth(): Promise<ViewerAuth> {
  browserAuth ??= import("./firebaseAuth").then((m) => m.createViewerAuth());
  return browserAuth;
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

/**
 * The sign-in to use: none when the build asks for no sign-in (status "off", nothing loaded), else the
 * injected fake, the browser one when the build is configured, or "unconfigured".
 */
function useViewerAuth(required: boolean, enabled: boolean, env: Record<string, unknown>, injected?: ViewerAuth) {
  const configured = required && (Boolean(injected) || authConfigured(env));
  const [auth, setAuth] = useState<ViewerAuth | null>(required ? injected ?? null : null);
  const [status, setStatus] = useState<AuthStatus>(!required ? "off" : configured ? "loading" : "unconfigured");
  const [user, setUser] = useState<ViewerUser | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!required || injected || !configured || !enabled || auth) return;
    let alive = true;
    loadBrowserAuth().then(
      (a) => { if (alive) setAuth(a); },
      () => { if (alive) setStatus("unconfigured"); },
    );
    return () => {
      alive = false;
    };
  }, [required, injected, configured, enabled, auth]);

  useEffect(() => {
    if (!auth) return;
    return auth.subscribe((u) => {
      setUser(u);
      setStatus(u ? "signed_in" : "signed_out");
      if (u) setError(null);
    });
  }, [auth]);

  const signIn = () => {
    if (!auth) return;
    setError(null);
    auth.signIn().catch((err: unknown) => setError(authErrorText(err)));
  };
  const signOut = () => {
    auth?.signOut().catch(() => undefined);
  };
  return { auth, status, user, error, signIn, signOut };
}

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
  const authRequired = monitorAuthRequired(env);
  const { auth, status, user, error, signIn, signOut } = useViewerAuth(authRequired, enabled, env, deps?.auth);
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
    // Without sign-in the page connects as soon as it is open; with it, only while a viewer is signed in.
    const signedIn = authRequired ? auth !== null && status === "signed_in" : true;
    const canConnect = enabled && visible && url && signedIn && (deps || typeof WebSocket !== "undefined");
    if (!canConnect) {
      setConn("waiting");
      setState(initialState());
      return;
    }
    // One socket at a time. In the sign-in build a 4401 (the server did not take the token: expired, or minted
    // before a sign-in change) gets one silent retry with a freshly minted token; a 4401 straight after that
    // retry is shown to the viewer. The retry is earned back once the feed delivered something or after an
    // ordinary drop. Without sign-in there is no token to retry with: a 4401 is shown as it comes.
    let stop: (() => void) | null = null;
    let retried = false;
    let gone = false;
    const open = (force: boolean) => {
      // The token source is given only in the sign-in build: without it the client never sends an auth line.
      const getToken = authRequired && auth ? (force ? () => auth.idToken(true) : (f?: boolean) => auth.idToken(f)) : undefined;
      stop = openMonitor(
        url,
        (m) => {
          retried = false;
          setState((s) => reduce(s, m));
        },
        (c) => {
          if (gone) return;
          if (c === "offline") retried = false;
          if (c === "unauthenticated" && getToken && !retried) {
            retried = true;
            stop?.();
            setState(initialState());
            setConn("connecting");
            open(true);
            return;
          }
          setConn(c);
          if (c !== "live") setState(initialState()); // nothing of a session is kept while the socket is not open
        },
        { ...(deps ?? browserDeps), signal: viewer, getToken },
      );
    };
    open(false);
    return () => {
      gone = true;
      viewer.stop(); // bye while the socket is still open, then the connection is closed
      stop?.();
      setState(initialState());
    };
  }, [enabled, visible, url, deps, auth, status, authRequired]);

  const follow = (sessionId: string) => setState((s) => followSession(s, sessionId));
  return { state, conn, configured: url !== null, auth: { status, user, error, signIn, signOut }, follow, video };
}
