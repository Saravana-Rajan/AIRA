// frontend/app/src/camera/rtcSender.ts — live video to the dashboard over WebRTC, peer to peer.
//
// The dashboard's Live page asks for video with {t:"rtc", kind:"want"} (relayed by the server, which only passes
// the notes). This opens one RTCPeerConnection per asking monitor, adds the EXISTING camera track (the stream
// camera/capture.ts already opened: no second camera prompt) as a sendonly video-only transceiver capped at
// ~600 kbps / 24 fps / half resolution (1280x720 -> 640x360), and sends the offer; the dashboard answers. ICE is
// STUN only (no TURN: behind a strict NAT the dashboard times out and keeps its JPEG preview). `connected` tells
// the server to stop asking for JPEG preview frames; `bye` (ours on failure, hide or dispose; theirs on leave)
// ends it. No audio track is ever added. Pure over an injectable PeerConnection so tests use a fake.
import type { ClientMsg, RtcCandidate, RtcMsg, ServerMsg } from "@aira/contracts";

export const ICE_SERVERS: RTCIceServer[] = [{ urls: "stun:stun.l.google.com:19302" }];
export const MAX_BITRATE = 600_000;
export const MAX_FPS = 24;
/** The camera opens at 1280x720 (backCamera.ts); half of that is the ~640x480 the dashboard needs. */
export const SCALE_DOWN = 2;

/** The slice of RTCPeerConnection this uses; a real one satisfies it, tests pass a fake. */
export interface PeerLike {
  addTransceiver(track: MediaStreamTrack, init: RTCRtpTransceiverInit): RTCRtpTransceiver;
  createOffer(): Promise<RTCSessionDescriptionInit>;
  setLocalDescription(d: RTCSessionDescriptionInit): Promise<void>;
  setRemoteDescription(d: RTCSessionDescriptionInit): Promise<void>;
  addIceCandidate(c: RTCIceCandidateInit): Promise<void>;
  close(): void;
  readonly connectionState: string;
  readonly localDescription: { sdp?: string } | null;
  onicecandidate: ((ev: { candidate: RTCIceCandidate | null }) => void) | null;
  onconnectionstatechange: (() => void) | null;
}
export type PeerCtor = new (config: RTCConfiguration) => PeerLike;

export interface RtcSenderDeps {
  send: (m: ClientMsg) => boolean;
  /** The camera stream the app already opened (null when the camera is off). */
  getStream: () => Promise<MediaStream | null>;
  /** Default: the browser's RTCPeerConnection; undefined means no WebRTC (every want is refused). */
  PeerConnection?: PeerCtor;
  iceServers?: RTCIceServer[];
  /** Tells when the page hides (closes every connection). Default: visibilitychange + pagehide. */
  visibility?: { subscribe(fn: (hidden: boolean) => void): () => void };
}

export interface RtcSender {
  /** Feed every server message; only {t:"rtc"} ones matter. */
  onServerMsg(m: ServerMsg): void;
  /** Open peer connections right now. */
  peers(): number;
  /** Bye to every monitor and close (page hidden, camera stopped). The sender stays usable. */
  closeAll(): void;
  /** closeAll, then ignore everything. */
  dispose(): void;
}

interface Peer {
  pc: PeerLike;
  remoteSet: boolean;
  pending: RTCIceCandidateInit[];
  queue: Promise<void>;
}

function browserVisibility(): RtcSenderDeps["visibility"] {
  return {
    subscribe(fn) {
      if (typeof document === "undefined" || typeof window === "undefined") return () => {};
      const sync = () => fn(document.visibilityState === "hidden");
      const hide = () => fn(true);
      document.addEventListener("visibilitychange", sync);
      window.addEventListener("pagehide", hide);
      return () => {
        document.removeEventListener("visibilitychange", sync);
        window.removeEventListener("pagehide", hide);
      };
    },
  };
}

function defaultPeer(): PeerCtor | undefined {
  const g = globalThis as { RTCPeerConnection?: unknown };
  return typeof g.RTCPeerConnection === "function" ? (g.RTCPeerConnection as PeerCtor) : undefined;
}

const toInit = (c: RtcCandidate): RTCIceCandidateInit => ({
  candidate: c.candidate, sdpMid: c.sdpMid ?? null, sdpMLineIndex: c.sdpMLineIndex ?? null,
});

export function createRtcSender(deps: RtcSenderDeps): RtcSender {
  const PC = "PeerConnection" in deps ? deps.PeerConnection : defaultPeer();
  const iceServers = deps.iceServers ?? ICE_SERVERS;
  const peers = new Map<string, Peer>();
  let disposed = false;

  const say = (kind: RtcMsg["kind"], to: string, extra: Partial<RtcMsg> = {}) => {
    try {
      deps.send({ t: "rtc", kind, to, ...extra } as ClientMsg);
    } catch {
      /* the socket is gone: the server will say bye for us */
    }
  };

  const drop = (id: string, bye: boolean) => {
    const p = peers.get(id);
    if (!p) return;
    peers.delete(id);
    p.pc.onicecandidate = null;
    p.pc.onconnectionstatechange = null;
    try { p.pc.close(); } catch { /* already closed */ }
    if (bye) say("bye", id);
  };

  const open = async (id: string) => {
    drop(id, false);
    if (!PC) {
      say("bye", id);
      return;
    }
    let stream: MediaStream | null = null;
    try {
      stream = await deps.getStream();
    } catch {
      stream = null;
    }
    const track = stream?.getVideoTracks?.()[0];
    if (disposed) return;
    if (!stream || !track) {
      say("bye", id);
      return;
    }
    let pc: PeerLike;
    try {
      pc = new PC({ iceServers });
    } catch {
      say("bye", id);
      return;
    }
    const peer: Peer = { pc, remoteSet: false, pending: [], queue: Promise.resolve() };
    peers.set(id, peer);
    pc.onicecandidate = (ev) => {
      const c = ev.candidate;
      if (!c || peers.get(id) !== peer) return;
      say("ice", id, { candidate: { candidate: c.candidate, sdpMid: c.sdpMid ?? null, sdpMLineIndex: c.sdpMLineIndex ?? null } });
    };
    pc.onconnectionstatechange = () => {
      if (peers.get(id) !== peer) return;
      const s = pc.connectionState;
      if (s === "connected") say("connected", id);
      else if (s === "failed" || s === "disconnected" || s === "closed") drop(id, true);
    };
    try {
      const tx = pc.addTransceiver(track, {
        direction: "sendonly",
        streams: [stream],
        sendEncodings: [{ maxBitrate: MAX_BITRATE, maxFramerate: MAX_FPS, scaleResolutionDownBy: SCALE_DOWN }],
      });
      try {
        const params = tx.sender.getParameters();
        const encodings = params.encodings?.length ? params.encodings : [{}];
        await tx.sender.setParameters({
          ...params,
          encodings: encodings.map((e) => ({ ...e, maxBitrate: MAX_BITRATE, maxFramerate: MAX_FPS, scaleResolutionDownBy: SCALE_DOWN })),
          degradationPreference: "maintain-framerate",
        } as RTCRtpSendParameters);
      } catch {
        /* a browser that refuses parameters still sends; the encoder picks its own rate */
      }
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      if (peers.get(id) !== peer) return;
      say("offer", id, { sdp: pc.localDescription?.sdp ?? offer.sdp ?? "" });
    } catch {
      if (peers.get(id) === peer) drop(id, true);
    }
  };

  const enqueue = (peer: Peer, job: () => Promise<void>) => {
    peer.queue = peer.queue.then(job, job).catch(() => {});
  };

  const handle = (m: RtcMsg) => {
    if (disposed) return;
    const id = m.from;
    if (m.kind === "want") {
      void open(id);
      return;
    }
    const peer = peers.get(id);
    if (!peer) return;
    if (m.kind === "bye") {
      drop(id, false);
    } else if (m.kind === "answer" && typeof m.sdp === "string") {
      const sdp = m.sdp;
      enqueue(peer, async () => {
        await peer.pc.setRemoteDescription({ type: "answer", sdp });
        peer.remoteSet = true;
        for (const c of peer.pending.splice(0)) await peer.pc.addIceCandidate(c);
      });
    } else if (m.kind === "ice" && m.candidate) {
      const c = toInit(m.candidate);
      enqueue(peer, async () => {
        if (peer.remoteSet) await peer.pc.addIceCandidate(c);
        else peer.pending.push(c);
      });
    }
  };

  const closeAll = () => {
    for (const id of [...peers.keys()]) drop(id, true);
  };

  const unsubscribe = (deps.visibility ?? browserVisibility())!.subscribe((hidden) => {
    if (hidden) closeAll();
  });

  return {
    onServerMsg: (m) => {
      if (m.t === "rtc") handle(m);
    },
    peers: () => peers.size,
    closeAll,
    dispose: () => {
      if (disposed) return;
      closeAll();
      disposed = true;
      unsubscribe();
    },
  };
}
