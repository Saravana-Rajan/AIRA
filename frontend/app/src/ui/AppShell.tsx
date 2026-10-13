import "./app.css";
import { useEffect, useRef, useState } from "preact/hooks";
import type { JSX } from "preact";
import type { Lang } from "@aira/contracts";
import { Onboarding, shopName } from "./Onboarding";
import { Screen } from "./Screen";
import { ScreenReaderRegion } from "./ScreenReaderRegion";
import { BoxOverlay } from "./BoxOverlay";
import { CameraPreview, CameraToggle, usePreviewShown } from "./CameraPreview";
import type { ScreenState } from "./screenState";
import { statusLabel, type CountProgress } from "./view";
import { LABELS, STRINGS } from "../i18n/strings";
import { loadSettings } from "../core/settings";
import { langFromPhone, phoneLanguages } from "../core/lang";
import { isInstalledLaunch, shouldAutoStart } from "../core/autostart";
import type { Settings } from "../core/types";
import { wireApp, type AppView, type WiredApp, type WireOpts } from "../core/wire";
import { DeviceCheck, type DeviceCheckProps } from "../check/DeviceCheck";

type LabelKey = keyof typeof LABELS;
const BY_ENGLISH = new Map<string, LabelKey>(
  (Object.keys(LABELS) as LabelKey[]).map((k) => [LABELS[k].en, k]),
);
/** Labels the reducer builds with a number or a count ("3 short", "12 / 30 counted"). */
const PARAMETERISED: Array<{ key: "short" | "extra" | "counted"; re: RegExp }> = [
  { key: "short", re: /^(\d+) short$/ },
  { key: "extra", re: /^(\d+) extra$/ },
  { key: "counted", re: /^(.+) counted$/ },
];

/** One English label from the reducer in the language the speaker last used; unknown text stays as sent. */
export function localizeLabel(label: string, lang: Lang): string {
  const k = BY_ENGLISH.get(label);
  if (k) return LABELS[k][lang];
  for (const p of PARAMETERISED) {
    const m = p.re.exec(label);
    if (m) return LABELS[p.key][lang].replace("{n}", m[1]);
  }
  return label;
}

/**
 * The screen reducer writes English chip and task-card labels. Shows the standard ones in the owner's
 * language ("Ready" → "தயார்", "Counting 12 / 30" → "எண்ணுகிறேன் 12 / 30", "Mismatch" → "பொருந்தவில்லை",
 * task "Delivery / 12 / 30 counted" → "டெலிவரி / 12 / 30 எண்ணப்பட்டது"); server text (a prompt, a summary) and
 * anything else stay as sent.
 */
export function localizeStatus(s: ScreenState, lang: Lang, progress: CountProgress | null): ScreenState {
  const { kind, label } = s.status;
  const next = kind === "counting" && progress ? statusLabel("counting", lang, progress) : localizeLabel(label, lang);
  let task = s.task;
  if (task) {
    const title = localizeLabel(task.title, lang);
    const detail = localizeLabel(task.detail, lang);
    if (title !== task.title || detail !== task.detail) task = { ...task, title, detail };
  }
  if (next === label && task === s.task) return s;
  return { ...s, status: { kind, label: next }, task };
}

function useWiredView(w: WiredApp | null): AppView | null {
  const [v, setV] = useState<AppView | null>(() => w?.view.get() ?? null);
  useEffect(() => {
    if (!w) return;
    setV(w.view.get());
    return w.view.subscribe(setV);
  }, [w]);
  return v;
}

type Props = {
  /** Called once inside the Start tap. Defaults to the real wireApp; tests inject a fake. */
  wire?: (o: WireOpts) => WiredApp;
  /** aira-live WebSocket URL (default: VITE_AIRA_LIVE_WS). */
  wsUrl?: string;
  /** false on the demo page, which connects the socket itself. */
  connect?: boolean;
  /** Saved settings (default: loadSettings()). */
  initial?: Settings;
  /** The phone's languages (tests inject them). */
  languages?: readonly string[];
  /** Runs right after wiring, still inside the Start tap: e.g. wireCameraAndGuidance(). */
  onWired?(w: WiredApp): void;
  /**
   * Skip the Start tap and open the mic on load (core/autostart.ts). Default: an onboarded owner opening the
   * installed app or `/?start=1`. The first launch ever always shows Start: the browser needs one gesture.
   */
  autoStart?: boolean;
  /**
   * The start-up device check shown between Start and the conversation (check/DeviceCheck.tsx): the mic must hear a
   * voice and the AI must see through the camera before anything is wired. null skips it (tests of the wiring).
   */
  deviceCheck?: ((p: DeviceCheckProps) => JSX.Element) | null;
};

/** Best-effort local speech for the Start screen (no wiring yet); silent where blocked or unsupported. */
function sayLocal(text: string, lang: Lang, rate: number): void {
  try {
    if (typeof speechSynthesis === "undefined") return;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = { ta: "ta-IN", hi: "hi-IN", en: "en-IN" }[lang];
    u.rate = rate;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  } catch {
    /* Chrome may refuse speech before a gesture; the role=status note below is read by TalkBack anyway */
  }
}

/**
 * The whole phone app: onboarding until the one Start tap, then the single gesture surface
 * (status chip, voice orb, captions, task card) plus the screen-reader live region.
 * main.tsx only has to render <AppShell />.
 */
export function AppShell({ wire = wireApp, wsUrl, connect, initial, languages, onWired, autoStart, deviceCheck = DeviceCheck }: Props) {
  const [saved] = useState<Settings>(() => initial ?? loadSettings());
  const [w, setW] = useState<WiredApp | null>(null);
  const [settings, setSettings] = useState<Settings>(saved);
  // true once a gesture-less start was refused: Start is back, with a spoken "Tap anywhere to start"
  const [fallback, setFallback] = useState(false);
  // Settings waiting for the device check to pass; auto = the hands-free start (no Start tap behind it).
  const [pending, setPending] = useState<{ s: Settings; auto: boolean } | null>(null);
  const checked = useRef(false); // passed once on this page: a later Start tap does not test again
  const unmounted = useRef(false);
  const Check = deviceCheck;
  const view = useWiredView(w);
  const [previewShown, togglePreview] = usePreviewShown();

  useEffect(() => () => { w?.dispose(); }, [w]);

  const wireNow = (s: Settings): WiredApp => {
    const wired = wire({ settings: s, wsUrl, connect });
    try {
      onWired?.(wired);
    } catch (e) {
      console.error("[aira] onWired hook failed", e); // the voice app keeps working
    }
    setSettings(s);
    setW(wired);
    return wired;
  };

  // ---- hands-free start ----
  // Wires on load, with no tap. The mic opens only if the browser lets a page that was granted the mic inside
  // an earlier Start tap reopen it without a gesture (installed apps and Chrome's persisted permission do).
  // When it refuses (first launch after a browser reset, a cleared permission, a new phone) the gesture-less
  // wiring is thrown away and the Start screen comes back.
  useEffect(() => () => { unmounted.current = true; }, []);

  const autoWire = (s: Settings) => {
    const wired = wireNow(s);
    void wired.startListening().then((ok) => {
      if (unmounted.current || ok) return;
      setW(null); // the [w] effect above disposes it
      setFallback(true);
      const lang = langFromPhone(languages ?? phoneLanguages());
      sayLocal(STRINGS.tapToStart[lang], lang, s.speechRate);
    });
  };

  // Every start goes through the device check first, the hands-free one included (it continues on its own).
  const begin = (s: Settings, auto: boolean) => {
    if (!Check || checked.current) {
      if (auto) autoWire(s);
      else wireNow(s); // synchronous: still inside the tap
      return;
    }
    setPending({ s, auto });
  };

  useEffect(() => {
    const auto = autoStart ?? shouldAutoStart({ onboarded: saved.onboarded, search: location.search, installed: isInstalledLaunch() });
    if (!auto || !saved.onboarded) return;
    begin(saved, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on mount
  }, []);

  if (!w && pending && Check) {
    const p = pending;
    return (
      <Check
        lang={langFromPhone(languages ?? phoneLanguages())}
        rate={p.s.speechRate}
        wsUrl={wsUrl}
        autoContinue={p.auto}
        onPass={() => {
          checked.current = true;
          setPending(null);
          if (p.auto) autoWire(p.s);
          else wireNow(p.s); // inside the "Start talking" tap: the gesture the voice app needs
        }}
      />
    );
  }

  if (!w) {
    const onboarding = (
      <Onboarding
        initial={saved}
        languages={languages}
        onDone={(s) => { begin(s, false); }} // the check first; wiring stays synchronous inside a tap
      />
    );
    if (!fallback) return onboarding;
    // "Tap anywhere": a tap outside the Start button (and off the TalkBack switch) presses Start. The
    // synthetic click runs inside the real tap's handler, so the user gesture still unlocks the mic.
    const tapAnywhere = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (t?.closest("button.onboard__start, label, input")) return;
      (e.currentTarget as HTMLElement).querySelector<HTMLButtonElement>("button.onboard__start")?.click();
    };
    const lang = langFromPhone(languages ?? phoneLanguages());
    return (
      <div class="autostart-fallback" onClick={tapAnywhere}>
        <p role="status" aria-live="polite" class="autostart-note" lang={lang}>{STRINGS.tapToStart[lang]}</p>
        {onboarding}
      </div>
    );
  }

  const v = view ?? w.view.get();
  return (
    <>
      <ScreenReaderRegion />
      <Screen
        state={localizeStatus(v.screen, v.lang, v.progress)}
        level={v.level}
        shopName={shopName(settings.shopId)}
        lang={v.lang}
        progress={v.progress}
        confirmPending={v.confirmPending}
        preview={previewShown ? <CameraPreview /> : null}
        onTalk={w.onTalk}
        onStop={w.onStop}
      />
      <CameraToggle shown={previewShown} onToggle={togglePreview} />
      {/* The small corner preview only when the big card is hidden: never two previews at once. */}
      {settings.showBoxes && !previewShown && <BoxOverlay />}
    </>
  );
}
