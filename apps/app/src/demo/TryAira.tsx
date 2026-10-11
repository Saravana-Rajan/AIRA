// "Try AIRA": the judge demo page (/try and /?demo=1). Light theme only (overrides §6).
// Every scene runs through the real pipeline: the real wireApp() (audio playback, earcons,
// mic, confirm state), the real socket to aira-live and the seeded demo shop. Only the inputs
// change: recorded media stands in for the camera, and the soundbox clip for the microphone.
import { useEffect, useRef, useState } from "preact/hooks";
import type { ClientMsg, ServerMsg } from "@aira/contracts";
import { wireApp, APP_VERSION, type WiredApp, type WireOpts } from "../core/wire";
import { setFrameSource } from "../camera/source";
import { DAY_PARTS, SCENARIOS, type Scenario, type ScenarioMediaKind } from "./scenarios";
import { describe } from "./describe";
import { connectDemo, demoWireOpts } from "./connectDemo";
import { injectAudioFile } from "./audioInject";
import "../ui/tokens.css";
import "./try.css";

const MAX_LINES = 80;
/** The UPI prompt goes first; the soundbox clip follows once AIRA is listening for it. */
const UPI_AUDIO_DELAY_MS = 2500;

type Status = "idle" | "connecting" | "live" | "error";
type LineKind = "scene" | "aira" | "user" | "sent" | "ok" | "warn" | "error" | "event";
interface Line { id: number; kind: LineKind; text: string }
interface Pending { msg: ClientMsg; then?: () => void }

const MEDIA_LABEL: Record<ScenarioMediaKind | "voice", string> = {
  voice: "Voice only",
  video: "Camera clip",
  image: "Camera photo",
  audio: "Soundbox audio",
};

const BY_ID = new Map(SCENARIOS.map((s) => [s.id, s]));

function kindOf(text: string): LineKind {
  if (text.startsWith("AIRA:")) return "aira";
  if (text.startsWith("You:")) return "user";
  if (text.startsWith("✓")) return "ok";
  if (text.startsWith("Error")) return "error";
  if (text.startsWith("⚠") || text.startsWith("?") || text.startsWith("Confirm?")) return "warn";
  return "event";
}

/** "3 · Count a crate" → { num: "3", name: "Count a crate" }. */
export function splitTitle(title: string): { num: string; name: string } {
  const i = title.indexOf(" · ");
  return i < 0 ? { num: "", name: title } : { num: title.slice(0, i), name: title.slice(i + 3) };
}

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  } catch {
    return false;
  }
}

function isNarrow(): boolean {
  try {
    return window.matchMedia?.("(max-width: 899px)").matches ?? false;
  } catch {
    return false;
  }
}

type Props = {
  /** Wires the app inside the judge's first tap. Defaults to the real wireApp; tests inject fakes. */
  wire?: (o: WireOpts) => WiredApp;
  /** aira-live WebSocket URL (default: VITE_AIRA_LIVE_WS). */
  wsUrl?: string;
  /** Runs right after wiring, still inside the tap: e.g. wireCameraAndGuidance(), so request_frame is answered. */
  onWired?(w: WiredApp): void;
  /** Plays a soundbox clip into the `audio` stream (default: injectAudioFile). */
  injectAudio?: typeof injectAudioFile;
  /** Delay between the UPI prompt and the soundbox clip. */
  audioDelayMs?: number;
};

export function TryAira({
  wire = wireApp,
  wsUrl,
  onWired,
  injectAudio = injectAudioFile,
  audioDelayMs = UPI_AUDIO_DELAY_MS,
}: Props = {}) {
  const [status, setStatus] = useState<Status>("idle");
  const [mock, setMock] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const [confirm, setConfirm] = useState<{ id: string; prompt: string } | null>(null);
  const [active, setActive] = useState<Scenario | null>(null);
  const [runId, setRunId] = useState(0);
  const [missing, setMissing] = useState<string | null>(null);
  const [typed, setTyped] = useState("");

  const wired = useRef<WiredApp | null>(null);
  const live = useRef(false);
  const off = useRef<(() => void) | null>(null);
  const queue = useRef<Pending[]>([]);
  const timers = useRef<number[]>([]);
  const audioAbort = useRef<AbortController | null>(null);
  const lineId = useRef(0);
  const logRef = useRef<HTMLOListElement>(null);
  const liveRef = useRef<HTMLElement>(null);

  function addLine(text: string, kind: LineKind = kindOf(text)) {
    const line = { id: ++lineId.current, kind, text };
    setLines((l) => [...l.slice(-(MAX_LINES - 1)), line]);
  }

  function flushQueue() {
    const w = wired.current;
    if (!w) return;
    const pending = queue.current;
    queue.current = [];
    for (const p of pending) {
      if (w.app.send(p.msg)) p.then?.();
      else queue.current.push(p);
    }
  }

  /** Sends now when the demo session is live, otherwise as soon as it is ready. */
  function sendOrQueue(msg: ClientMsg, then?: () => void) {
    const w = wired.current;
    if (w && live.current && w.app.send(msg)) {
      then?.();
      return;
    }
    queue.current.push({ msg, then });
  }

  function onServer(m: ServerMsg) {
    if (m.t === "ready") {
      live.current = true;
      setMock(m.mock);
      setStatus("live");
      flushQueue();
      return;
    }
    const line = describe(m);
    if (line) addLine(line);
    if (m.t === "confirm") setConfirm({ id: m.id, prompt: m.prompt });
  }

  /** Wires the real app once, inside the judge's tap (it unlocks audio), and joins the demo shop. */
  function ensureConnected(): WiredApp | null {
    if (wired.current) return wired.current;
    let w: WiredApp;
    try {
      w = wire(demoWireOpts(wsUrl));
    } catch (e) {
      setStatus("error");
      addLine(`Error (start): ${errText(e)}`, "error");
      return null;
    }
    wired.current = w;
    try {
      onWired?.(w);
    } catch (e) {
      console.error("[aira] onWired hook failed", e); // the demo keeps working without it
    }
    off.current = w.socket.on(onServer);
    setStatus("connecting");
    connectDemo(APP_VERSION, w.socket).catch((e) => {
      setStatus("error");
      addLine(`Error (connect): ${errText(e)}`, "error");
    });
    return w;
  }

  useEffect(() => {
    return () => {
      off.current?.();
      for (const t of timers.current) window.clearTimeout(t);
      audioAbort.current?.abort();
      setFrameSource(null); // Review Focus #1: never leave demo media in place of the real camera
      wired.current?.dispose();
      wired.current = null;
      live.current = false;
    };
  }, []);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines, confirm]);

  function scrollTo(el: Element | null) {
    el?.scrollIntoView?.({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "start" });
  }

  function startDemo() {
    ensureConnected();
    scrollTo(document.getElementById("try-day"));
  }

  function run(s: Scenario) {
    ensureConnected();
    audioAbort.current?.abort();
    audioAbort.current = null;
    setFrameSource(null); // the new scene's media registers itself once it has loaded
    setActive(s);
    setRunId((n) => n + 1);
    setMissing(null);
    setConfirm(null);
    addLine(splitTitle(s.title).name, "scene");
    addLine(`Sam: “${s.prompt}”`, "sent");
    const media = s.media;
    sendOrQueue({ t: "text", text: s.prompt }, media?.kind === "audio" ? () => playClip(media.src) : undefined);
    if (isNarrow()) scrollTo(liveRef.current);
  }

  function playClip(src: string) {
    const ctrl = new AbortController();
    audioAbort.current = ctrl;
    const t = window.setTimeout(() => {
      if (ctrl.signal.aborted) return;
      injectAudio(src, (m) => wired.current?.app.send(m) ?? false, { signal: ctrl.signal }).catch((e) => {
        if (ctrl.signal.aborted) return;
        setMissing(src);
        addLine(`Error (audio): ${errText(e)}`, "error");
      });
    }, audioDelayMs);
    timers.current.push(t);
  }

  function onMediaReady(el: HTMLVideoElement | HTMLImageElement) {
    setFrameSource(el); // request_frame is now answered from this media
  }

  function onMediaError(src: string) {
    setFrameSource(null);
    setMissing(src);
  }

  /** Never queued: a late "yes" to an order or a payment is worse than asking again. */
  function answer(a: "yes" | "no") {
    const w = wired.current;
    const c = confirm;
    if (!w || !c) return;
    let sent: boolean;
    if (w.confirm.pending?.id === c.id) {
      // The app's own confirm path: the same earcon, haptic and state as a double-tap / long-press.
      if (a === "yes") w.onTalk();
      else w.onStop();
      sent = w.confirm.pending?.id !== c.id;
    } else {
      sent = w.app.send({ t: "confirm", id: c.id, answer: a });
    }
    if (!sent) {
      addLine("Not sent: AIRA is not connected. Answer again when it is back.", "error");
      return;
    }
    addLine(`You answered: ${a === "yes" ? "Yes" : "No"}`, "sent");
    setConfirm(null);
  }

  function talk() {
    const w = ensureConnected();
    if (!w || confirm) return;
    w.onTalk();
  }

  function stop() {
    const w = wired.current;
    if (!w || confirm) return;
    w.onStop();
  }

  function submitTyped(e: Event) {
    e.preventDefault();
    const text = typed.trim();
    if (!text) return;
    ensureConnected();
    addLine(`You typed: “${text}”`, "sent");
    sendOrQueue({ t: "text", text });
    setTyped("");
  }

  const statusLabel =
    status === "live" ? (mock ? "Live (mock server)" : "Live")
    : status === "connecting" ? "Connecting…"
    : status === "error" ? "Not connected"
    : "Not started";

  const media = active?.media;
  const stageKey = `${active?.id ?? "none"}-${runId}`;

  return (
    <main class="try">
      <header class="try-hero">
        <img class="try-logo" src="/icons/aira-logo-512.png" alt="AIRA" width={176} height={176} />
        <div class="try-hero-text">
          <h1>A blind shopkeeper runs his own shop. Alone.</h1>
          <p class="try-lede">
            AIRA is a voice in one ear and a phone on the chest. It counts deliveries, checks bills, guides
            the hand to the right product and confirms every payment. Pick a moment from a day at Sunrise Dairy
            and watch it run on the real AIRA backend.
          </p>
          <div class="try-actions">
            <button type="button" class="try-cta" onClick={startDemo}>Try AIRA</button>
            <a class="try-link" href="/dashboard/">Open the Shop Dashboard</a>
          </div>
        </div>
      </header>

      <div class="try-body">
        <section id="try-day" class="try-day" aria-labelledby="try-day-title">
          <h2 id="try-day-title">A day at Sunrise Dairy</h2>
          <p class="try-note">
            Each moment sends what Sam would say. Camera photos, clips and the soundbox recording stand in
            for the phone's camera and microphone.
          </p>
          {DAY_PARTS.map((part) => (
            <section key={part.id} class="try-part" aria-labelledby={`try-part-${part.id}`}>
              <h3 id={`try-part-${part.id}`}>{part.title}</h3>
              <ol class="try-scenes">
                {part.scenarioIds.map((id) => {
                  const s = BY_ID.get(id)!;
                  const { num, name } = splitTitle(s.title);
                  const on = active?.id === s.id;
                  const kind = s.media?.kind ?? "voice";
                  return (
                    <li key={s.id}>
                      <button
                        type="button"
                        class={`try-scene${on ? " is-on" : ""}`}
                        data-scene={s.id}
                        aria-pressed={on}
                        onClick={() => run(s)}
                      >
                        <span class="try-scene-num" aria-hidden="true">{num}</span>
                        <span class="try-scene-body">
                          <strong>{name}</strong>
                          <span class="try-scene-blurb">{s.blurb}</span>
                          <span class={`try-tag try-tag--${kind}`}>{MEDIA_LABEL[kind]}</span>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            </section>
          ))}
        </section>

        <aside id="try-live" class="try-live" ref={liveRef} aria-labelledby="try-live-title">
          <div class="try-live-head">
            <h2 id="try-live-title">Live</h2>
            <span class={`try-chip try-chip--${status}`} role="status">{statusLabel}</span>
          </div>

          <figure class="try-stage" key={stageKey}>
            {!active && <p class="try-stage-note">Pick a moment. What AIRA's camera sees shows here.</p>}
            {active && missing && (
              <p class="try-stage-note">
                {media?.kind === "audio"
                  ? <>The soundbox clip <code>{missing}</code> is not recorded yet.</>
                  : <>The demo media <code>{missing}</code> is not recorded yet. AIRA uses this computer's camera instead, if you allow it.</>}
              </p>
            )}
            {active && !missing && media?.kind === "video" && (
              <video src={media.src} autoPlay muted loop playsInline
                aria-label={`Camera view for: ${splitTitle(active.title).name}`}
                onLoadedData={(e) => onMediaReady(e.currentTarget)}
                onError={() => onMediaError(media.src)} />
            )}
            {active && !missing && media?.kind === "image" && (
              <img src={media.src} alt={`Camera view for: ${splitTitle(active.title).name}`}
                onLoad={(e) => onMediaReady(e.currentTarget)}
                onError={() => onMediaError(media.src)} />
            )}
            {active && !missing && media?.kind === "audio" && (
              <p class="try-stage-note try-stage-audio">The shop's payment soundbox plays into AIRA's microphone stream.</p>
            )}
            {active && !media && <p class="try-stage-note">No camera needed. AIRA answers by voice.</p>}
            {active && <figcaption>Sam says: “{active.prompt}”</figcaption>}
          </figure>

          <section class="try-panel" aria-labelledby="try-panel-title">
            <h3 id="try-panel-title">What AIRA is doing</h3>
            <ol class="try-log" ref={logRef} aria-live="polite" aria-relevant="additions">
              {lines.length === 0 && <li class="try-line try-line--empty">Nothing yet. Pick a moment, talk, or type below.</li>}
              {lines.map((l) => <li key={l.id} class={`try-line try-line--${l.kind}`}>{l.text}</li>)}
            </ol>
            {confirm && (
              <div class="try-confirm" role="group" aria-label="AIRA is asking for confirmation">
                <p>{confirm.prompt}</p>
                <div class="try-confirm-actions">
                  <button type="button" class="try-yes" onClick={() => answer("yes")}>Yes</button>
                  <button type="button" class="try-no" onClick={() => answer("no")}>No</button>
                </div>
              </div>
            )}
          </section>

          <div class="try-voice">
            <button type="button" class="try-talk" onClick={talk} disabled={!!confirm}>Talk</button>
            <button type="button" class="try-stop" onClick={stop} disabled={!!confirm || status === "idle"}>Stop</button>
            <p class="try-voice-note">Have a microphone? Press Talk and speak as Sam.</p>
          </div>

          <form class="try-typed" onSubmit={submitTyped}>
            <label for="try-typed-input">No microphone? Type what Sam would say.</label>
            <input id="try-typed-input" value={typed} autoComplete="off"
              onInput={(e) => setTyped(e.currentTarget.value)} placeholder="For example: How much Sakthi curd is left?" />
            <button type="submit">Send</button>
          </form>
        </aside>
      </div>
    </main>
  );
}
