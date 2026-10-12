// The Live page's WebRTC viewer: near-real-time video from Sam's phone, peer to peer, with the JPEG preview
// as the automatic fallback.
//
// `want(sessionId)` sends {t:"rtc", kind:"want"} over the /monitor socket (the server relays it to that phone
// session of the shop); the phone offers a sendonly video track, this answers recvonly, both trickle ICE
// (STUN only: no TURN, so behind a strict NAT nothing connects and the preview stays). Until the connection
// reaches "connected" the mode is "connecting" and the page keeps drawing JPEG frames; after CONNECT_TIMEOUT_MS
// without it, or when the connection fails, disconnects or the phone says bye, the mode is "preview" again and a
// new want goes out RETRY_MS later. `attach`/`detach` follow the socket (a closed socket drops the connection
// without a bye: nowhere to send it); `stop` is the page leaving. No DOM here: the page plays `stream`.
import type { RtcCandidate, RtcClientMsg, RtcMsg } from "@aira/contracts";

export const CONNECT_TIMEOUT_MS = 6000;
export const RETRY_MS = 15_000;
export const ICE_SERVERS: RTCIceServer[] = [{ urls: "stun:stun.l.google.com:19302" }];

export type VideoMode = "preview" | "connecting" | "webrtc";
export interface VideoState {
  mode: VideoMode;
  /** The remote video while mode is "webrtc" (the page plays it in a <video>). */
  stream: MediaStream | null;
  /** The session asked for video. */
  session: string | null;
}

/** The slice of RTCPeerConnection this uses; a real one satisfies it, tests pass a fake. */
export interface PeerLike {
  setRemoteDescription(d: RTCSessionDescriptionInit): Promise<void>;
  createAnswer(): Promise<RTCSessionDescriptionInit>;
  setLocalDescription(d: RTCSessionDescriptionInit): Promise<void>;
  addIceCandidate(c: RTCIceCandidateInit): Promise<void>;
  close(): void;
  readonly connectionState: string;
  ontrack: ((ev: { track: MediaStreamTrack; streams: readonly MediaStream[] }) => void) | null;
  onicecandidate: ((ev: { candidate: RTCIceCandidate | null }) => void) | null;
  onconnectionstatechange: (() => void) | null;
}
export type PeerCtor = new (config: RTCConfiguration) => PeerLike;

export interface ViewerDeps {
  /** Default: the browser's RTCPeerConnection; undefined means no WebRTC (the preview only). */
  PeerConnection?: PeerCtor;
  iceServers?: RTCIceServer[];
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (t: unknown) => void;
}

/** What the monitor socket gives the viewer: a way to send, and its messages. */
export interface SignalPort {
  attach(send: (m: RtcClientMsg) => boolean): void;
  detach(): void;
  receive(m: RtcMsg): void;
}

export interface RtcViewer extends SignalPort {
  /** Ask this session for video (null: none). The same session again changes nothing. */
  want(sessionId: string | null): void;
  state(): VideoState;
  subscribe(fn: (s: VideoState) => void): () => void;
  /** Bye, close and forget everything (the page is leaving). */
  stop(): void;
}

function defaultPeer(): PeerCtor | undefined {
  const g = globalThis as { RTCPeerConnection?: unknown };
  return typeof g.RTCPeerConnection === "function" ? (g.RTCPeerConnection as PeerCtor) : undefined;
}

const toInit = (c: RtcCandidate): RTCIceCandidateInit => ({
  candidate: c.candidate, sdpMid: c.sdpMid ?? null, sdpMLineIndex: c.sdpMLineIndex ?? null,
});

export function createRtcViewer(deps: ViewerDeps = {}): RtcViewer {
  const PC = "PeerConnection" in deps ? deps.PeerConnection : defaultPeer();
  const iceServers = deps.iceServers ?? ICE_SERVERS;
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((t) => clearTimeout(t as ReturnType<typeof setTimeout>));
  const listeners = new Set<(s: VideoState) => void>();

  let send: ((m: RtcClientMsg) => boolean) | null = null;
  let session: string | null = null;
  let mode: VideoMode = "preview";
  let stream: MediaStream | null = null;
  let pc: PeerLike | null = null;
  let remoteSet = false;
  let pending: RTCIceCandidateInit[] = [];
  let queue: Promise<void> = Promise.resolve();
  let timer: unknown = null;

  const state = (): VideoState => ({ mode, stream, session });
  const notify = () => {
    for (const fn of listeners) {
      try { fn(state()); } catch { /* a listener must not break the viewer */ }
    }
  };
  const set = (m: VideoMode, s: MediaStream | null) => {
    if (m === mode && s === stream) return;
    mode = m;
    stream = s;
    notify();
  };
  const say = (kind: RtcClientMsg["kind"], extra: Partial<RtcClientMsg> = {}) => {
    if (!send || !session) return;
    try { send({ t: "rtc", kind, to: session, ...extra }); } catch { /* the socket is closing */ }
  };
  const clearTimers = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
  };
  const closePc = () => {
    const p = pc;
    pc = null;
    remoteSet = false;
    pending = [];
    queue = Promise.resolve();
    if (p) {
      p.ontrack = null;
      p.onicecandidate = null;
      p.onconnectionstatechange = null;
      try { p.close(); } catch { /* already closed */ }
    }
  };

  /** Back to the preview; `bye` says so to the phone; `retry` asks again later. */
  const fallback = (bye: boolean, retry: boolean) => {
    clearTimers();
    if (bye && (pc || mode !== "preview")) say("bye");
    closePc();
    set("preview", null);
    if (retry && session && send) timer = setTimer(() => { timer = null; ask(); }, RETRY_MS);
  };

  const ask = () => {
    if (!PC || !send || !session) return;
    clearTimers();
    closePc();
    say("want");
    set("connecting", null);
    timer = setTimer(() => { timer = null; if (mode !== "webrtc") fallback(true, true); }, CONNECT_TIMEOUT_MS);
  };

  const enqueue = (job: () => Promise<void>) => {
    const mine = (queue = queue.then(job, job).catch(() => {}));
    return mine;
  };

  const onOffer = (sdp: string) => {
    if (!PC) return;
    closePc();
    let p: PeerLike;
    try {
      p = new PC({ iceServers });
    } catch {
      fallback(true, true);
      return;
    }
    pc = p;
    p.ontrack = (ev) => {
      if (pc !== p) return;
      const s = ev.streams[0] ?? (typeof MediaStream === "function" ? new MediaStream([ev.track]) : null);
      if (mode === "webrtc") set("webrtc", s);
      else stream = s; // shown once connected
    };
    p.onicecandidate = (ev) => {
      const c = ev.candidate;
      if (!c || pc !== p) return;
      say("ice", { candidate: { candidate: c.candidate, sdpMid: c.sdpMid ?? null, sdpMLineIndex: c.sdpMLineIndex ?? null } });
    };
    p.onconnectionstatechange = () => {
      if (pc !== p) return;
      const s = p.connectionState;
      if (s === "connected") {
        clearTimers();
        set("webrtc", stream);
      } else if (s === "failed" || s === "disconnected" || s === "closed") {
        fallback(true, true);
      }
    };
    void enqueue(async () => {
      await p.setRemoteDescription({ type: "offer", sdp });
      remoteSet = true;
      for (const c of pending.splice(0)) await p.addIceCandidate(c);
      const answer = await p.createAnswer();
      await p.setLocalDescription(answer);
      if (pc === p) say("answer", { sdp: answer.sdp ?? "" });
    }).then(() => undefined, () => { if (pc === p) fallback(true, true); });
  };

  return {
    attach(s) {
      send = s;
      if (session) ask();
    },
    detach() {
      send = null;
      clearTimers();
      closePc();
      set("preview", null);
    },
    receive(m) {
      if (!session || m.from !== session) return;
      if (m.kind === "offer" && typeof m.sdp === "string") onOffer(m.sdp);
      else if (m.kind === "bye") fallback(false, true);
      else if (m.kind === "ice" && m.candidate && pc) {
        const p = pc;
        const c = toInit(m.candidate);
        void enqueue(async () => {
          if (pc !== p) return;
          if (remoteSet) await p.addIceCandidate(c);
          else pending.push(c);
        });
      }
    },
    want(id) {
      if (id === session) return;
      if (session) fallback(true, false);
      session = id;
      if (id) ask();
      notify();
    },
    state,
    subscribe(fn) {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
    stop() {
      fallback(true, false);
      session = null;
      notify();
    },
  };
}
