import "./onboarding.css";
import { useEffect, useRef, useState } from "preact/hooks";
import { LANGS, type Lang } from "@aira/contracts";
import type { Settings } from "../core/types";
import { DEFAULT_SETTINGS, DEMO_SHOP_ID } from "../core/settings";
import { LANG_NAMES, ONBOARDING } from "../i18n/strings";

/** Shop names shown on screen. One user, one shop in this build. */
export const SHOP_NAMES: Record<string, string> = { [DEMO_SHOP_ID]: "Murugan Dairy" };
export const shopName = (id: string): string => SHOP_NAMES[id] ?? id;

/** The first of the phone's languages that AIRA speaks; English otherwise. */
export function langFromPhone(languages: readonly string[] = []): Lang {
  for (const l of languages) {
    const base = l.toLowerCase().split("-")[0];
    if ((LANGS as readonly string[]).includes(base)) return base as Lang;
  }
  return "en";
}

const phoneLanguages = (): readonly string[] => {
  try {
    return navigator.languages?.length ? navigator.languages : [navigator.language];
  } catch {
    return [];
  }
};

type Props = {
  onDone(s: Settings): void;
  /** Saved settings. A returning owner keeps the saved language and TalkBack choice. */
  initial?: Settings;
  /** The phone's languages (tests inject them). */
  languages?: readonly string[];
};

/** The four-point spark from the AIRA logo. */
const SPARK = "M50 4 C54 34 66 46 96 50 C66 54 54 66 50 96 C46 66 34 54 4 50 C34 46 46 34 50 4 Z";

/**
 * One big Start tap. That tap is the user gesture the browser needs to unlock audio, the mic,
 * the camera and the wake lock, so `onDone` must run its setup synchronously inside it.
 * The labels follow the language picked here. Start takes focus, so a TalkBack user hears
 * "Start" first and can begin with one double-tap.
 */
export function Onboarding({ onDone, initial, languages }: Props) {
  const base: Settings = { ...DEFAULT_SETTINGS, ...initial };
  const [lang, setLang] = useState<Lang>(() =>
    initial?.onboarded ? base.lang : langFromPhone(languages ?? phoneLanguages()),
  );
  const [srMode, setSr] = useState<boolean>(base.screenReaderMode);
  const done = useRef(false);
  const start = useRef<HTMLButtonElement>(null);

  // Focus for TalkBack and keyboards, without drawing a focus ring on a finger-driven page load.
  useEffect(() => { start.current?.focus({ preventScroll: true, focusVisible: false } as FocusOptions); }, []);

  const finish = () => {
    if (done.current) return; // a double tap must not run setup twice
    done.current = true;
    onDone({ ...base, lang, screenReaderMode: srMode, shopId: base.shopId || DEMO_SHOP_ID, onboarded: true });
  };

  const t = (k: keyof typeof ONBOARDING) => ONBOARDING[k][lang];

  return (
    <main class="onboard" lang={lang}>
      <header class="onboard__head">
        <img class="onboard__logo" src="/icons/aira-logo-192.png" alt="" width={64} height={64} />
        <span class="onboard__titles">
          <h1 class="onboard__title">AIRA</h1>
          <span class="onboard__shop">{shopName(base.shopId || DEMO_SHOP_ID)}</span>
        </span>
      </header>
      <p class="onboard__how">{t("howTo")}</p>

      <fieldset class="onboard__langs">
        <legend class="onboard__legend">{t("language")}</legend>
        <span class="onboard__tiles">
          {LANGS.map((l) => (
            <label key={l} class="lang-tile" data-checked={l === lang ? "true" : undefined}>
              <input
                class="lang-tile__input"
                type="radio"
                name="aira-lang"
                value={l}
                checked={l === lang}
                onChange={() => setLang(l)}
              />
              <span class="lang-tile__native" lang={l}>{LANG_NAMES[l].native}</span>
              {LANG_NAMES[l].english !== LANG_NAMES[l].native ? (
                <span class="lang-tile__english" lang="en">{LANG_NAMES[l].english}</span>
              ) : null}
              <svg class="lang-tile__tick" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path d="M5 12.5l4.5 4.5L19 7.5" />
              </svg>
            </label>
          ))}
        </span>
      </fieldset>

      <label class="tb-switch">
        <input class="tb-switch__input" type="checkbox" role="switch" checked={srMode} onChange={() => setSr(!srMode)} />
        <span class="tb-switch__track" aria-hidden="true"><span class="tb-switch__knob" /></span>
        <span class="tb-switch__text">
          <span class="tb-switch__label">{t("talkback")}</span>
          <span class="tb-switch__hint">{t("talkbackHint")}</span>
        </span>
      </label>

      <button
        ref={start}
        type="button"
        class="onboard__start"
        aria-labelledby="aira-start-label"
        aria-describedby="aira-start-hint"
        onClick={finish}
      >
        <svg class="onboard__spark" viewBox="0 0 100 100" aria-hidden="true" focusable="false">
          <defs>
            <linearGradient id="aira-spark-grad" x1="0" y1="0" x2="1" y2="1">
              <stop class="onboard__stop onboard__stop--1" offset="0" />
              <stop class="onboard__stop onboard__stop--2" offset="0.4" />
              <stop class="onboard__stop onboard__stop--3" offset="0.7" />
              <stop class="onboard__stop onboard__stop--4" offset="1" />
            </linearGradient>
          </defs>
          <path d={SPARK} fill="url(#aira-spark-grad)" />
        </svg>
        <span id="aira-start-label" class="onboard__start-label">{t("start")}</span>
        <span id="aira-start-hint" class="onboard__start-hint">{t("startHint")}</span>
      </button>
    </main>
  );
}
