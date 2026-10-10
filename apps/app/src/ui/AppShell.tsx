import "./app.css";
import { useEffect, useState } from "preact/hooks";
import type { Lang } from "@aira/contracts";
import { Onboarding, shopName } from "./Onboarding";
import { Screen } from "./Screen";
import { ScreenReaderRegion } from "./ScreenReaderRegion";
import type { ScreenState } from "./screenState";
import { statusLabel, type CountProgress } from "./view";
import { LABELS } from "../i18n/strings";
import { loadSettings } from "../core/settings";
import type { Settings } from "../core/types";
import { wireApp, type AppView, type WiredApp, type WireOpts } from "../core/wire";

type LabelKey = keyof typeof LABELS;
const BY_ENGLISH = new Map<string, LabelKey>(
  (Object.keys(LABELS) as LabelKey[]).map((k) => [LABELS[k].en, k]),
);

/**
 * The screen reducer writes English chip labels. Shows the standard ones in the owner's
 * language ("Ready" → "தயார்", "Counting 12 / 30" → "எண்ணுகிறேன் 12 / 30"); others stay as sent.
 */
export function localizeStatus(s: ScreenState, lang: Lang, progress: CountProgress | null): ScreenState {
  const { kind, label } = s.status;
  let next = label;
  if (kind === "counting" && progress) next = statusLabel("counting", lang, progress);
  else {
    const k = BY_ENGLISH.get(label);
    if (k) next = LABELS[k][lang];
  }
  return next === label ? s : { ...s, status: { kind, label: next } };
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
};

/**
 * The whole phone app: onboarding until the one Start tap, then the single gesture surface
 * (status chip, voice orb, captions, task card) plus the screen-reader live region.
 * main.tsx only has to render <AppShell />.
 */
export function AppShell({ wire = wireApp, wsUrl, connect, initial, languages, onWired }: Props) {
  const [saved] = useState<Settings>(() => initial ?? loadSettings());
  const [w, setW] = useState<WiredApp | null>(null);
  const [settings, setSettings] = useState<Settings>(saved);
  const view = useWiredView(w);

  useEffect(() => () => { w?.dispose(); }, [w]);

  if (!w) {
    return (
      <Onboarding
        initial={saved}
        languages={languages}
        onDone={(s) => {
          const wired = wire({ settings: s, wsUrl, connect }); // synchronous: still inside the tap
          try {
            onWired?.(wired);
          } catch (e) {
            console.error("[aira] onWired hook failed", e); // the voice app keeps working
          }
          setSettings(s);
          setW(wired);
        }}
      />
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
        onTalk={w.onTalk}
        onStop={w.onStop}
      />
    </>
  );
}
