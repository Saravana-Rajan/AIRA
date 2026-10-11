import "./onboarding.css";
import { useEffect, useRef, useState } from "preact/hooks";
import type { Lang } from "@aira/contracts";
import type { Settings } from "../core/types";
import { langFromPhone, phoneLanguages } from "../core/lang";
import { DEFAULT_SETTINGS, DEMO_SHOP_ID } from "../core/settings";
import { ONBOARDING } from "../i18n/strings";

export { langFromPhone };

/** Shop names shown on screen. One user, one shop in this build. */
export const SHOP_NAMES: Record<string, string> = { [DEMO_SHOP_ID]: "Sunrise Dairy" };
export const shopName = (id: string): string => SHOP_NAMES[id] ?? id;

type Props = {
  onDone(s: Settings): void;
  /** Saved settings. A returning owner keeps the TalkBack choice and speech rate. */
  initial?: Settings;
  /** The phone's languages (tests inject them). */
  languages?: readonly string[];
};

/** The four-point spark from the AIRA logo. */
const SPARK = "M50 4 C54 34 66 46 96 50 C66 54 54 66 50 96 C46 66 34 54 4 50 C34 46 46 34 50 4 Z";

/**
 * One big Start tap. That tap is the user gesture the browser needs to unlock audio, the mic,
 * the camera and the wake lock, so `onDone` must run its setup synchronously inside it.
 * There is nothing to pick: AIRA answers in whatever language the speaker uses, turn by turn. The labels on
 * this screen are written in the phone's language (English for one the app has no labels for), which is
 * also the hint for AIRA's very first line. Start takes focus, so a TalkBack user hears "Start" first and
 * can begin with one double-tap. The whole screen sits in one phone-width column (tokens.css
 * `--phone-width`), so a laptop shows a centred phone rather than a page stretched edge to edge.
 * The Start card is a centred stack (badge, "Start", one-line hint) that fills the rest of a phone
 * screen, so it is still one huge tap target without being an empty slab.
 */
export function Onboarding({ onDone, initial, languages }: Props) {
  const base: Settings = { ...DEFAULT_SETTINGS, ...initial };
  const [lang] = useState<Lang>(() => langFromPhone(languages ?? phoneLanguages()));
  const [srMode, setSr] = useState<boolean>(base.screenReaderMode);
  const done = useRef(false);
  const start = useRef<HTMLButtonElement>(null);

  // Focus for TalkBack and keyboards, without drawing a focus ring on a finger-driven page load.
  useEffect(() => { start.current?.focus({ preventScroll: true, focusVisible: false } as FocusOptions); }, []);

  const finish = () => {
    if (done.current) return; // a double tap must not run setup twice
    done.current = true;
    onDone({ ...base, screenReaderMode: srMode, shopId: base.shopId || DEMO_SHOP_ID, onboarded: true });
  };

  const t = (k: keyof typeof ONBOARDING) => ONBOARDING[k][lang];

  return (
    <main class="onboard" lang={lang}>
      <div class="onboard__col">
      <header class="onboard__head">
        <img class="onboard__logo" src="/icons/aira-logo-192.png" alt="" width={48} height={48} />
        <span class="onboard__titles">
          <h1 class="onboard__title">AIRA</h1>
          <span class="onboard__shop">{shopName(base.shopId || DEMO_SHOP_ID)}</span>
        </span>
      </header>
      <p class="onboard__intro">{t("howTo")} {t("anyLanguage")}</p>

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
        <span class="onboard__badge" aria-hidden="true">
          <svg class="onboard__spark" viewBox="0 0 100 100" focusable="false">
            <path d={SPARK} fill="currentColor" />
          </svg>
        </span>
        <span id="aira-start-label" class="onboard__start-label">{t("start")}</span>
        <span id="aira-start-hint" class="onboard__start-hint">{t("startHint")}</span>
      </button>
      </div>
    </main>
  );
}
