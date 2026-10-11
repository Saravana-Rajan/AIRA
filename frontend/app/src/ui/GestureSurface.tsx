import type { ComponentChildren } from "preact";
import { useEffect, useRef } from "preact/hooks";

export const DOUBLE_TAP_MS = 350;
export const LONG_PRESS_MS = 600;

type Props = {
  label: string;
  onTalk(): void;
  onStop(): void;
  children?: ComponentChildren;
};

/**
 * The whole screen is ONE button: double-tap (< 350 ms) = talk, long-press (>= 600 ms) = stop.
 * Keyboard or screen-reader activation (a click with detail 0) also talks, and Escape stops.
 * Children are visual only (phrasing content); the button's accessible name is `label`.
 */
export function GestureSurface({ label, onTalk, onStop, children }: Props) {
  const lastTap = useRef(0);
  const pressTimer = useRef<number | null>(null);
  const longFired = useRef(false);
  const cb = useRef({ onTalk, onStop });
  cb.current = { onTalk, onStop };

  const clearPress = () => {
    if (pressTimer.current !== null) window.clearTimeout(pressTimer.current);
    pressTimer.current = null;
  };
  useEffect(() => clearPress, []);

  const down = (e: PointerEvent) => {
    if (e.isPrimary === false) return;
    longFired.current = false;
    clearPress();
    pressTimer.current = window.setTimeout(() => {
      pressTimer.current = null;
      longFired.current = true;
      lastTap.current = 0;
      cb.current.onStop();
    }, LONG_PRESS_MS);
  };

  const up = (e: PointerEvent) => {
    if (e.isPrimary === false) return;
    clearPress();
    if (longFired.current) {
      longFired.current = false;
      return;
    }
    const now = Date.now();
    if (lastTap.current && now - lastTap.current < DOUBLE_TAP_MS) {
      lastTap.current = 0;
      cb.current.onTalk();
    } else {
      lastTap.current = now;
    }
  };

  const click = (e: MouseEvent) => {
    // Pointer taps arrive as clicks with detail >= 1 and are handled above.
    if (e.detail === 0) cb.current.onTalk();
  };

  const key = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      cb.current.onStop();
    }
  };

  return (
    <button
      type="button"
      class="surface"
      aria-label={label}
      onPointerDown={down}
      onPointerUp={up}
      onPointerCancel={clearPress}
      onPointerLeave={clearPress}
      onClick={click}
      onKeyDown={key}
      onContextMenu={(e) => e.preventDefault()}
    >
      {children}
    </button>
  );
}
