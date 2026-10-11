// frontend/app/src/check/DeviceCheck.tsx
// The screen between the Start tap and the conversation. It checks the microphone (does it hear a voice?)
// and then the camera (the AI looks at one picture and says what it sees), speaking every step aloud in AIRA's own
// voice (checks.airaVoice), and listening to the mic only after AIRA has finished speaking.
// The conversation opens only after both pass: through the big "Start talking" button, which is the user
// gesture the voice app needs, or straight away in a hands-free start (autoContinue).
import "./check.css";
import { useEffect, useMemo, useRef, useState } from "preact/hooks";
import type { Lang } from "@aira/contracts";
import { CHECK } from "../i18n/strings";
import { captureFrame, startCamera } from "../camera/capture";
import { appKey, wsDefault } from "../core/wire";
import {
  airaVoice, askAiAboutCamera, cameraVoice, deviceKind, httpBase, instructionVoice, micVoice, playBlob,
  speakLocalAsync, testMicrophone, voiceLine, type CameraResult, type DeviceKind, type MicResult, type VoiceLine,
} from "./checks";

type Step = "waiting" | "checking" | "passed" | "failed";

export type DeviceCheckProps = {
  lang: Lang;
  onPass(): void;
  /** Hands-free start: open the conversation as soon as both checks pass, without the Start talking tap. */
  autoContinue?: boolean;
  rate?: number;
  wsUrl?: string;
  device?: DeviceKind;
  /** Test seams; the defaults use the real mic, camera and aira-live. */
  micTest?: (onLevel: (rms: number) => void) => Promise<MicResult>;
  cameraTest?: (preview: HTMLElement | null) => Promise<CameraResult>;
  /** Speaks a line; may resolve when it has finished (the default does), so the mic is only opened afterwards. */
  speak?: (text: string, lang: Lang, rate: number) => void | Promise<void>;
};

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

async function waitForPicture(v: HTMLVideoElement, ms = 4000): Promise<boolean> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (v.videoWidth > 0 && v.videoHeight > 0) return true;
    await sleep(100);
  }
  return false;
}

function realMicTest(onLevel: (rms: number) => void): Promise<MicResult> {
  return testMicrophone({
    getUserMedia: (c) => navigator.mediaDevices.getUserMedia(c),
    makeContext: () => new AudioContext(),
    onLevel,
  });
}

function realCameraTest(lang: Lang, device: DeviceKind, wsUrl: string) {
  return async (preview: HTMLElement | null): Promise<CameraResult> => {
    let video: HTMLVideoElement;
    try {
      video = await startCamera(); // the same stream the voice app reuses afterwards
    } catch {
      return { ok: false, problem: "blocked" };
    }
    // The preview is a second <video> on the same stream. Never move AIRA's own camera element into this screen:
    // a video taken out of the page again is paused by the browser, and every later picture would be this frozen
    // frame (seen live: two questions 33 s apart got the identical picture).
    if (preview && !preview.querySelector("video")) {
      const pv = document.createElement("video");
      pv.muted = true;
      pv.setAttribute("playsinline", "true");
      pv.className = device === "laptop" ? "check__video check__video--mirror" : "check__video";
      pv.srcObject = video.srcObject;
      preview.replaceChildren(pv);
      void pv.play().catch(() => undefined);
    }
    if (!(await waitForPicture(video))) return { ok: false, problem: "blank" };
    await sleep(1200); // let the exposure settle before the picture the AI judges
    let jpeg: string;
    try {
      jpeg = (await captureFrame("ask")).jpeg;
    } catch {
      return { ok: false, problem: "blank" };
    }
    return askAiAboutCamera(jpeg, { base: httpBase(wsUrl), lang, device, key: appKey() });
  };
}

export function DeviceCheck(props: DeviceCheckProps) {
  const { lang, onPass, autoContinue = false, rate = 1 } = props;
  const device = props.device ?? deviceKind();
  const wsUrl = props.wsUrl ?? wsDefault();
  const voice = useMemo(
    () => airaVoice({ base: httpBase(wsUrl), key: appKey(), play: playBlob, fallback: speakLocalAsync }),
    [wsUrl],
  );
  const micTest = props.micTest ?? realMicTest;
  const cameraTest = props.cameraTest ?? realCameraTest(lang, device, wsUrl);

  const [mic, setMic] = useState<Step>("waiting");
  const [cam, setCam] = useState<Step>("waiting");
  const [micText, setMicText] = useState("");
  const [camText, setCamText] = useState("");
  const [line, setLine] = useState(CHECK.intro[lang]);
  const [level, setLevel] = useState(0);
  const preview = useRef<HTMLDivElement>(null);
  const alive = useRef(true);
  const passedOnce = useRef(false);

  /** Shows the line at once and resolves when it has been spoken (AIRA's voice, or the test seam). */
  const say = async (line: VoiceLine): Promise<void> => {
    if (!alive.current) return;
    setLine(line.text);
    if (props.speak) await props.speak(line.text, lang, rate);
    else await voice.speak(line, lang, rate);
  };

  const runCamera = async () => {
    setCam("checking");
    setCamText(""); // the instruction is the spoken line at the top; the card only shows the result
    await say(instructionVoice(device, lang));
    if (!alive.current) return;
    const r = await cameraTest(preview.current);
    if (!alive.current) return;
    const result = cameraVoice(r, lang);
    setCamText(result.text);
    setCam(r.ok ? "passed" : "failed");
    await say(result);
    if (!r.ok || !alive.current) return;
    if (autoContinue) finish(); // AIRA has finished speaking, so the conversation's voice does not talk over it
    else await say(voiceLine("allGood", lang));
  };

  const runMic = async () => {
    setMic("checking");
    setMicText("");
    // Listen only after the instruction has been spoken: AIRA's own voice must never pass the mic test.
    await say(voiceLine("micSay", lang));
    if (!alive.current) return;
    const r = await micTest((rms) => { if (alive.current) setLevel(rms); });
    if (!alive.current) return;
    setLevel(0);
    const result = micVoice(r, lang);
    setMicText(result.text);
    setMic(r.ok ? "passed" : "failed");
    if (!r.ok) return say(result);
    await runCamera();
  };

  const finish = () => {
    if (passedOnce.current) return; // a double tap must not open the conversation twice
    passedOnce.current = true;
    onPass();
  };

  useEffect(() => {
    alive.current = true;
    if (!props.speak) {                    // warm AIRA's voice for the lines that come next
      voice.prefetch(voiceLine("micSay", lang), lang);
      voice.prefetch(instructionVoice(device, lang), lang);
      voice.prefetch(voiceLine("allGood", lang), lang);
    }
    void runMic();
    return () => {
      alive.current = false;
      const pv = preview.current?.querySelector("video");
      if (pv) { pv.srcObject = null; pv.remove(); } // only the preview copy; the stream stays open for the voice app
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on mount
  }, []);

  const both = mic === "passed" && cam === "passed";
  const label = (s: Step) => ({ waiting: CHECK.waiting, checking: CHECK.checking, passed: CHECK.passed, failed: CHECK.failed }[s][lang]);

  return (
    <main class="check" lang={lang} aria-labelledby="check-title">
      <div class="check__col">
        <header class="check__head">
          <img class="check__logo" src="/icons/aira-logo-192.png" alt="" width={40} height={40} />
          <h1 id="check-title" class="check__title">{CHECK.title[lang]}</h1>
        </header>

        <p class="check__line" role="status" aria-live="polite">{line}</p>

        <ol class="check__steps">
          <li class={`check__step check__step--${mic}`}>
            <div class="check__row">
              <span class="check__name">{CHECK.micStep[lang]}</span>
              <span class="check__pill">{label(mic)}</span>
            </div>
            {micText && <p class="check__detail">{micText}</p>}
            {mic === "checking" && (
              <div class="check__meter" aria-hidden="true">
                <span style={{ width: `${Math.min(100, Math.round(level * 1000))}%` }} />
              </div>
            )}
            {mic === "failed" && (
              <button type="button" class="check__retry" onClick={() => { void runMic(); }}>{CHECK.tryAgain[lang]}</button>
            )}
          </li>

          <li class={`check__step check__step--${cam}`}>
            <div class="check__row">
              <span class="check__name">{CHECK.camStep[lang]}</span>
              <span class="check__pill">{label(cam)}</span>
            </div>
            {camText && <p class="check__detail">{camText}</p>}
            <div class="check__preview" ref={preview} hidden={cam === "waiting"} aria-hidden="true" />
            {cam === "failed" && (
              <button type="button" class="check__retry" onClick={() => { void runCamera(); }}>{CHECK.tryAgain[lang]}</button>
            )}
          </li>
        </ol>

        {both && !autoContinue && (
          <button type="button" class="check__go" onClick={finish} aria-describedby="check-go-hint">
            <span class="check__go-label">{CHECK.startTalking[lang]}</span>
            <span id="check-go-hint" class="check__go-hint">{CHECK.allGood[lang]}</span>
          </button>
        )}
      </div>
    </main>
  );
}
