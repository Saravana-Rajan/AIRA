import "./onboarding.css";
import { useEffect, useRef, useState } from "preact/hooks";
import type { Lang } from "@aira/contracts";
import type { Settings } from "../core/types";
import { langFromPhone, phoneLanguages } from "../core/lang";
import { DEFAULT_SETTINGS, SHOP_NAME } from "../core/settings";
import { ONBOARDING } from "../i18n/strings";
import { VoiceOrb } from "./VoiceOrb";

export { langFromPhone };

export { SHOP_NAME };
/** The shop name shown on screen. One user, one shop in this build; the id argument is legacy and ignored. */
export const shopName = (_id?: string): string => SHOP_NAME;

type Props = {
  onDone(s: Settings): void;
  /** Saved settings. A returning owner keeps the TalkBack choice and speech rate. */
  initial?: Settings;
  /** The phone's languages (tests inject them). */
  languages?: readonly string[];
};

/**
 * One big Start tap. That tap is the user gesture the browser needs to unlock audio, the mic,
 * the camera and the wake lock, so `onDone` must run its setup synchronously inside it.
 * There is nothing to pick: AIRA answers in whatever language the speaker uses, turn by turn. The labels on
 * this screen are written in the phone's language (English for one the app has no labels for), which is
 * also the hint for AIRA's very first line. Start takes focus, so a TalkBack user hears "Tap to start"
 * first and can begin with one double-tap.
 *
 * Layout, top to bottom, in one phone-width column (tokens.css `--phone-width`): a compact header
 * (logo, AIRA, shop), then the Start button, which is the whole middle of the screen on a white page:
 * the same glowing orb as the voice screen (ui/VoiceOrb, styled by app.css), "Tap to start" and one
 * muted line. It fills all the free height, so a blind owner can tap anywhere in the middle. The
 * TalkBack switch is the last row, above the bottom safe area.
 */
export function Onboarding({ onDone, initial, languages }: Props) {
  const base: Settings = { ...DEFAULT_SETTINGS, ...initial };
  const [lang] = useState<Lang>(() => langFromPhone(languages ?? phoneLanguages()));
  const [srMode, setSr] = useState<boolean>(base.screenReaderMode);
  const [showBoxes, setShowBoxes] = useState<boolean>(base.showBoxes === true);
  const done = useRef(false);
  const start = useRef<HTMLButtonElement>(null);

  // Focus for TalkBack and keyboards, without drawing a focus ring on a finger-driven page load.
  useEffect(() => { start.current?.focus({ preventScroll: true, focusVisible: false } as FocusOptions); }, []);

  const finish = () => {
    if (done.current) return; // a double tap must not run setup twice
    done.current = true;
    onDone({ ...base, screenReaderMode: srMode, showBoxes, onboarded: true });
  };

  const t = (k: keyof typeof ONBOARDING) => ONBOARDING[k][lang];

  return (
    <main class="onboard" lang={lang}>
      <div class="onboard__col">
        <header class="onboard__head">
          <img class="onboard__logo" src="/icons/aira-logo-192.png" alt="" width={40} height={40} />
          <span class="onboard__titles">
            <h1 class="onboard__title">AIRA</h1>
            <span class="onboard__shop">{shopName()}</span>
          </span>
        </header>

        <button
          ref={start}
          type="button"
          class="onboard__start"
          aria-labelledby="aira-start-label"
          aria-describedby="aira-start-hint"
          onClick={finish}
        >
          <span class="onboard__orb" aria-hidden="true">
            <VoiceOrb />
          </span>
          <span id="aira-start-label" class="onboard__start-label">{t("tapToStart")}</span>
          <span id="aira-start-hint" class="onboard__start-hint">{t("startHint")} {t("anyLanguage")}</span>
        </button>

        <label class="tb-switch">
          <input class="tb-switch__input" type="checkbox" role="switch" checked={srMode} onChange={() => setSr(!srMode)} />
          <span class="tb-switch__track" aria-hidden="true"><span class="tb-switch__knob" /></span>
          <span class="tb-switch__text">
            <span class="tb-switch__label">{t("talkback")}</span>
            <span class="tb-switch__hint">{t("talkbackHint")}</span>
          </span>
        </label>

        <label class="tb-switch tb-switch--boxes">
          <input class="tb-switch__input" type="checkbox" role="switch" checked={showBoxes} onChange={() => setShowBoxes(!showBoxes)} />
          <span class="tb-switch__track" aria-hidden="true"><span class="tb-switch__knob" /></span>
          <span class="tb-switch__text">
            <span class="tb-switch__label">{t("showBoxes")}</span>
            <span class="tb-switch__hint">{t("showBoxesHint")}</span>
          </span>
        </label>
      </div>
    </main>
  );
}
