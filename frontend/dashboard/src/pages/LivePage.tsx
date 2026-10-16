// The Live page: who is using AIRA right now, what the selected person's phone sees and what is being said.
// One thin bar on top (the connection and the people selector: one chip per live phone session, the followed
// one black), then that person's camera, large, with the conversation beside it (below it on a narrow
// screen): the followed session's live transcript, the person's words on the right and AIRA's replies on the
// left, partial captions updating in place, newest at the bottom. The picture is the WebRTC stream when the
// viewer has one, else the latest JPEG frame on a canvas. When the server relays audio, a Listen button in the
// conversation's head plays the person's microphone and AIRA's voice on this computer. AIRA's own look: light,
// Inter, soft panels; nothing else on the page.
import type { MonitorRole, MonitorVoice, OrientationAngle } from "@aira/contracts";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import { browserDeps, createFrameDrawer, type DrawDeps } from "../lib/frameDraw";
import {
  listSessions, sessionLabel, sinceText,
  type Connection, type Frame, type LiveState, type MonitorState, type SessionInfo, type TranscriptLine,
} from "../lib/monitor";
import type { VideoState } from "../lib/rtcViewer";
import type { AuthStatus, ViewerUser } from "../lib/viewerAuth";
import { RtcVideo } from "../components/RtcVideo";

/**
 * The viewer's Google sign-in as the page shows it (lib/useMonitor fills it; tests pass a value). Status
 * "off" (the default build: no sign-in) shows no gate and no user, exactly like no auth prop at all.
 */
export interface LiveAuth {
  status: AuthStatus;
  user: ViewerUser | null;
  error: string | null;
  onSignIn: () => void;
  onSignOut: () => void;
}

/** Hearing the followed session on this computer (lib/useMonitor fills it; tests pass a value). */
export interface LiveListen {
  /** The server relays audio to a monitor that asks (AIRA_MONITOR_AUDIO=1); false hides the button. */
  available: boolean;
  on: boolean;
  onToggle: () => void;
  /** Subscribes a meter to each voice's level (0-1) as chunks play; returns the unsubscribe. No meter without it. */
  levels?: (cb: (who: MonitorVoice, level: number) => void) => () => void;
}

export interface LivePageProps {
  state: LiveState;
  conn: Connection;
  /** Who is signed in to watch. Left out (older callers, tests of the feed itself): treated as signed in. */
  auth?: LiveAuth;
  /** Hearing the session. Left out: no button. */
  listen?: LiveListen;
  /** The WebRTC picture (lib/rtcViewer): played while mode is "webrtc", else the JPEG frames. */
  video?: VideoState;
  /** False when the dashboard has no AIRA Live address (VITE_AIRA_LIVE_WS): nothing to connect to. */
  configured?: boolean;
  /** Picks another of the shop's live sessions (`state.sessions`) to follow. */
  onFollow?: (sessionId: string) => void;
  shopName?: string;
  now?: () => number;
  /** The camera drawer's DOM hooks (tests inject fakes). */
  drawDeps?: DrawDeps;
}

const STATUS_TEXT: Record<MonitorState, string> = {
  listening: "Listening", thinking: "Thinking", guiding: "Guiding", found: "Found", idle: "Idle",
};

const CONN_TEXT: Record<Connection, string> = {
  waiting: "Not connected", connecting: "Connecting…", live: "Connected", offline: "Reconnecting…", rejected: "Refused",
  unauthenticated: "Sign-in expired", unauthorized: "Not allowed",
};

/** The server closed with 4403 before anything was sent: this page's Origin (or shop) is not on its list. */
export const REJECTED_TEXT = "This address isn't allowed to watch AIRA Live. Open the dashboard from its own address (the server allows only its listed origins).";
const UNAUTHORIZED_TEXT = "This Google account isn't allowed to watch AIRA Live. Ask the shop owner to add it.";

const LANG_NAME: Record<string, string> = { ta: "Tamil", hi: "Hindi", en: "English" };
const SPEAKER: Record<MonitorRole, string> = { owner: "Owner", customer: "Customer", aira: "AIRA" };
const VOICE: Record<MonitorVoice, string> = { owner: "Mic", aira: "AIRA" };
/** The conversation shows the newest this many lines (the reducer keeps MAX_LINES). */
export const MAX_SHOWN = 50;
/** Scrolled up more than this (px from the bottom): the viewer is reading, auto-scroll stops until they return. */
export const STICK_PX = 24;
export const LISTEN_TEXT = "Listen";
export const STOP_LISTEN_TEXT = "Stop";

/** A clock that ticks four times a second while the page is open, for the "since" text. */
function useNow(now: () => number): number {
  const [t, setT] = useState(now);
  useEffect(() => {
    const h = setInterval(() => setT(now()), 250);
    return () => clearInterval(h);
  }, []);
  return t;
}

export function LivePage({
  state, conn, auth, listen, video, configured = true, onFollow, shopName = "AIRA", now = Date.now, drawDeps = browserDeps,
}: LivePageProps) {
  const t = useNow(now);
  const liveVideo = video?.mode === "webrtc" && video.stream !== null;
  const hasPicture = state.frame !== null || liveVideo;
  // No sign-in in this build (status "off") or no auth prop: no gate, no user, every refusal reads the same.
  const noAuth = !auth || auth.status === "off";
  // What goes in the camera area when there is no picture: the sign-in states first, then the feed's.
  const gate = !noAuth ? signInGate(auth, conn) : null;
  const empty = gate ?? <Waiting conn={conn} configured={configured} />;
  const signedIn = !noAuth && auth.status === "signed_in";
  const connText = noAuth && (conn === "unauthenticated" || conn === "unauthorized") ? CONN_TEXT.rejected : CONN_TEXT[conn];
  const followed = state.session !== null ? state.sessions[state.session] ?? null : null;
  const camTitle = state.session === null ? "Camera" : `${followed ? sessionLabel(followed) : state.session.slice(0, 6)}'s view`;

  return (
    <div class="livepage">
      <div class="lv-bar">
      <header class="lv-head">
        <img class="lv-logo" src={`${import.meta.env.BASE_URL}aira-logo-192.png`} alt="" width={40} height={40} />
        <h2 class="lv-title">{shopName} - Live</h2>
        <div class="lv-conn">
          <span class={`lv-conn__dot lv-conn__dot--${state.session ? "on" : conn}`} aria-hidden="true" />
          <span class="lv-conn__text" role="status">{state.session ? "Session open" : connText}</span>
          {state.lang && <span class="lv-conn__lang">{LANG_NAME[state.lang] ?? state.lang}</span>}
        </div>
        {auth && signedIn && auth.user && (
          <div class="lv-user">
            <span class="lv-conn__user" title={auth.user.email ?? undefined}>{auth.user.email ?? auth.user.name ?? "Signed in"}</span>
            <button class="lv-conn__signout" type="button" onClick={auth.onSignOut}>Sign out</button>
          </div>
        )}
      </header>

      <People sessions={state.sessions} current={state.session} now={t} onFollow={onFollow} />
      </div>

      <section class="cam" aria-labelledby="cam-title">
        <div class="cam__head">
          <h3 id="cam-title" class="cam__title">{camTitle}</h3>
          <Chip conn={conn} hasPicture={hasPicture} />
        </div>
        <div class="cam__view">
          {hasPicture ? <Camera frame={state.frame} drawDeps={drawDeps} video={video} orientation={state.orientation} /> : empty}
        </div>
      </section>

      <Conversation lines={state.transcript} listen={listen} />
    </div>
  );
}

// ---------- the conversation: what the followed person and AIRA say, live ----------
/**
 * The followed session's transcript as chat bubbles: the person's words (Owner or Customer, as the feed's role
 * says) on the right, AIRA's replies on the left, a partial caption updating in place until its final line
 * replaces it. Newest at the bottom; the list follows new lines unless the viewer scrolled up to read, and
 * follows again once they return to the bottom. Switching person switches the transcript (the reducer starts
 * the new person's from a clean slate). The Listen button lives in its head when the server relays audio.
 */
function Conversation({ lines, listen }: { lines: TranscriptLine[]; listen?: LiveListen }) {
  const shown = lines.length > MAX_SHOWN ? lines.slice(lines.length - MAX_SHOWN) : lines;
  const ref = useRef<HTMLOListElement>(null);
  const stick = useRef(true);
  const last = shown[shown.length - 1];
  useEffect(() => {
    const el = ref.current;
    if (!el || !stick.current) return;
    try {
      el.scrollTop = el.scrollHeight;
    } catch {
      /* jsdom or a detached node */
    }
  }, [last?.id, last?.text, shown.length]);
  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_PX;
  };
  return (
    <section class="chat" aria-labelledby="chat-title">
      <div class="chat__head">
        <h3 id="chat-title" class="chat__title">Conversation</h3>
        {listen?.available && <ListenControl listen={listen} />}
      </div>
      {shown.length === 0 ? (
        <p class="chat__empty">Nothing said yet.</p>
      ) : (
        <ol class="chat__list" ref={ref} onScroll={onScroll} aria-live="polite" aria-relevant="additions text">
          {shown.map((l) => (
            <li key={l.id} class={`msg msg--${l.role}${l.final ? "" : " msg--partial"}`}>
              {l.role === "aira" && <span class="msg__avatar" aria-hidden="true">A</span>}
              <div class="msg__bubble">
                <span class="msg__who">{SPEAKER[l.role]}</span>
                <p class="msg__text">{l.text}</p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** The Listen toggle (a click: the user gesture browsers need to start sound) and, while on, a small level meter per voice. */
function ListenControl({ listen }: { listen: LiveListen }) {
  return (
    <div class="listen">
      {listen.on && listen.levels && <Meter levels={listen.levels} />}
      <button class={`listen__btn${listen.on ? " listen__btn--on" : ""}`} type="button" aria-pressed={listen.on} onClick={listen.onToggle}
        title={listen.on ? "Stop playing this person's microphone and AIRA's voice" : "Play this person's microphone and AIRA's voice on this computer"}>
        <svg class="listen__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M11 5 6 9H3v6h3l5 4z" />
          {listen.on
            ? <g><path d="M15.5 8.8a4 4 0 0 1 0 6.4" /><path d="M18.2 6.3a7.5 7.5 0 0 1 0 11.4" /></g>
            : <path d="m16.5 9.5 5 5m0-5-5 5" />}
        </svg>
        <span class="listen__label">{listen.on ? STOP_LISTEN_TEXT : LISTEN_TEXT}</span>
      </button>
    </div>
  );
}

/** Two tiny bars (Mic, AIRA) driven straight from the player's levels, outside the render loop. */
function Meter({ levels }: { levels: NonNullable<LiveListen["levels"]> }) {
  const bars = useRef<Record<MonitorVoice, HTMLSpanElement | null>>({ owner: null, aira: null });
  useEffect(() => levels((who, level) => {
    const el = bars.current[who];
    if (el) el.style.transform = `scaleX(${Math.min(1, level * 3).toFixed(3)})`; // speech sits around 0.1-0.3 RMS: shown fuller
  }), [levels]);
  return (
    <span class="meter" aria-hidden="true">
      {(["owner", "aira"] as MonitorVoice[]).map((who) => (
        <span key={who} class={`meter__voice meter__voice--${who}`}>
          <span class="meter__label">{VOICE[who]}</span>
          <span class="meter__bar"><span class="meter__fill" ref={(el) => { bars.current[who] = el; }} /></span>
        </span>
      ))}
    </span>
  );
}

// ---------- the people using AIRA now ----------
/**
 * Everyone using AIRA right now (the shop's live phone sessions, newest first), one card each: who, their
 * language, since when and what AIRA is doing for them. The page follows one of them at a time (the black
 * card); clicking another switches to it. Room cameras are not people: the reducer keeps them apart.
 */
function People({ sessions, current, now, onFollow }: { sessions: LiveState["sessions"]; current: string | null; now: number; onFollow?: (id: string) => void }) {
  const people = listSessions(sessions);
  return (
    <section class="people" aria-labelledby="people-title">
      <h3 id="people-title" class="people__title">People using AIRA now ({people.length})</h3>
      {people.length === 0 ? (
        <p class="people__empty">No one is using AIRA right now.</p>
      ) : (
        <ul class="people__list">
          {people.map((p) => <Person key={p.id} info={p} on={p.id === current} now={now} onFollow={onFollow} />)}
        </ul>
      )}
    </section>
  );
}

function Person({ info, on, now, onFollow }: { info: SessionInfo; on: boolean; now: number; onFollow?: (id: string) => void }) {
  return (
    <li class="people__item">
      <button class={`person${on ? " person--on" : ""}`} type="button" aria-pressed={on} onClick={() => onFollow?.(info.id)}>
        <span class="person__label">{sessionLabel(info)}</span>
        <span class="person__meta">
          <span class="person__lang">{LANG_NAME[info.lang] ?? info.lang}</span>
          <span class="person__since">{sinceText(info.startedAt, now)}</span>
        </span>
        <span class={`person__status person__status--${info.status}`}>{STATUS_TEXT[info.status]}</span>
      </button>
    </li>
  );
}

// ---------- the camera ----------
/** The tiny chip beside the camera title: live with a picture, connecting while the socket is not up, refused on a 4403. */
function Chip({ conn, hasPicture }: { conn: Connection; hasPicture: boolean }) {
  if (hasPicture) {
    return (
      <span class="cam__chip cam__chip--live" role="status"><span class="cam__chip-dot" aria-hidden="true" />live</span>
    );
  }
  if (conn === "rejected") return <span class="cam__chip cam__chip--refused" role="status">refused</span>;
  if (conn === "connecting" || conn === "offline") return <span class="cam__chip cam__chip--connecting" role="status">connecting</span>;
  return null;
}

function waitingText(conn: Connection, configured: boolean): string {
  // With no sign-in in the build there is no account to blame: every refusal is the server's refusal of the page.
  if (conn === "rejected" || conn === "unauthenticated" || conn === "unauthorized") return REJECTED_TEXT;
  if (!configured) return "AIRA Live is not configured for this dashboard.";
  if (conn === "live") return "Connected. Waiting for the phone to start a session.";
  if (conn === "offline") return "Lost the connection to AIRA Live. Reconnecting…";
  return "Connecting to AIRA Live…";
}

/**
 * The sign-in states, in the camera area: no Firebase config, checking, signed out (the Google button), the
 * server's refusal of the account (4403 after auth) or of the token (4401 twice: sign in again). Null when the
 * viewer is signed in and allowed, so the feed's own waiting text shows.
 */
function signInGate(auth: LiveAuth, conn: Connection) {
  const who = auth.user?.email ?? auth.user?.name ?? null;
  if (auth.status === "unconfigured") {
    return (
      <div class="cam__empty" role="status">
        <p class="cam__empty-title">Live Monitor sign-in isn't configured for this dashboard.</p>
        <p class="cam__empty-sub">The build has no Firebase web config (VITE_FIREBASE_PROJECT_ID and VITE_FIREBASE_API_KEY), so there is no Google sign-in to watch with.</p>
      </div>
    );
  }
  if (auth.status === "loading") {
    return (
      <div class="cam__empty" role="status">
        <span class="cam__pulse" aria-hidden="true" />
        <p class="cam__empty-title">Checking sign-in…</p>
      </div>
    );
  }
  if (auth.status === "signed_out") {
    return (
      <div class="signin">
        <h4 class="signin__title">Watch AIRA Live</h4>
        <p class="signin__text">Sam's camera and the shop's conversations are private: only the Google accounts the shop owner listed can watch.</p>
        <button class="signin__btn" type="button" onClick={auth.onSignIn}>Sign in with Google to watch AIRA Live</button>
        {auth.error && <p class="signin__error" role="alert">{auth.error}</p>}
      </div>
    );
  }
  if (conn === "unauthorized") {
    return (
      <div class="signin">
        <p class="signin__text" role="alert">{UNAUTHORIZED_TEXT}</p>
        {who && <p class="signin__who">Signed in as {who}</p>}
        <button class="signin__btn" type="button" onClick={auth.onSignOut}>Sign out</button>
      </div>
    );
  }
  if (conn === "unauthenticated") {
    return (
      <div class="signin">
        <p class="signin__text" role="alert">Your sign-in expired.</p>
        <button class="signin__btn" type="button" onClick={auth.onSignIn}>Sign in again</button>
        {auth.error && <p class="signin__error" role="alert">{auth.error}</p>}
      </div>
    );
  }
  return null;
}

function Waiting({ conn, configured }: { conn: Connection; configured: boolean }) {
  const refused = conn === "rejected" || conn === "unauthenticated" || conn === "unauthorized";
  return (
    <div class="cam__empty" role="status">
      {!refused && <span class="cam__pulse" aria-hidden="true" />}
      <p class="cam__empty-title">{refused ? "AIRA Live refused this page" : "Waiting for a phone…"}</p>
      <p class="cam__empty-sub">{waitingText(conn, configured)}</p>
    </div>
  );
}

/** A canvas that always shows the newest frame it was given (decoded off the main thread when the browser can). */
function FrameCanvas({ frame, deps, class: cls }: { frame: Frame; deps: DrawDeps; class: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawer = useMemo(() => createFrameDrawer(() => ref.current, deps), [deps]);
  useEffect(() => () => drawer.stop(), [drawer]);
  useEffect(() => {
    drawer.push(frame);
  }, [drawer, frame]);
  return <canvas ref={ref} class={cls} width={frame.w} height={frame.h} data-frame-ts={frame.ts} aria-hidden="true" />;
}

/** The size of the WebRTC picture before its metadata says otherwise (the phone sends ~640x480). */
const RTC_DEFAULT_SIZE = { w: 640, h: 480 };

/** The picture, in its own aspect ratio: the WebRTC video when the viewer is connected, else the latest JPEG frame on a canvas. */
function Camera({ frame, drawDeps, video, orientation = 0 }: { frame: Frame | null; drawDeps: DrawDeps; video?: VideoState; orientation?: OrientationAngle }) {
  const live = video?.mode === "webrtc" ? video.stream : null;
  const [videoSize, setVideoSize] = useState(RTC_DEFAULT_SIZE);
  if (!frame && !live) return null;
  const w = live ? videoSize.w : frame!.w;
  const h = live ? videoSize.h : frame!.h;
  // Only the JPEG picture is turned here, by the phone's screen angle; the WebRTC <video> rotates itself through
  // the browser's own video-orientation (CVO) and reports its already-rotated size, so it is left alone.
  const angle = live ? 0 : orientation;
  const sideways = angle === 90 || angle === 270;
  return (
    <div class="camera" style={{ aspectRatio: sideways ? `${h} / ${w}` : `${w} / ${h}` }}>
      {live ? (
        <RtcVideo stream={live} onSize={setVideoSize} />
      ) : angle === 0 ? (
        <FrameCanvas frame={frame!} deps={drawDeps} class="camera__canvas" />
      ) : (
        // Turn the JPEG picture to match the phone. The box aspect was swapped above when the phone is sideways,
        // so the holder is sized in the box's OWN dimensions (cqh/cqw): for a sideways phone it is box-height wide
        // and box-width tall, so turning it 90deg maps it exactly onto the box and the frame fills it with no black
        // bars; at 180deg the box keeps the frame shape, so 100% x 100% already fills. `angle` is the single knob to
        // flip (to 360 - angle) if a real device shows the picture backwards. WebRTC is intentionally left unrotated.
        <div
          class="camera__rot"
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            width: sideways ? "100cqh" : "100%",
            height: sideways ? "100cqw" : "100%",
            transform: `translate(-50%, -50%) rotate(${angle}deg)`,
          }}
        >
          <FrameCanvas frame={frame!} deps={drawDeps} class="camera__canvas" />
        </div>
      )}
    </div>
  );
}
