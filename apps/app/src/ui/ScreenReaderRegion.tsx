import { useEffect, useState } from "preact/hooks";

let push: ((t: string) => void) | null = null;
let queued: string | null = null;

/**
 * Screen-reader mode output (Settings.screenReaderMode): TalkBack reads this region and the
 * app stays silent, so the same text is never spoken twice. Safe to call before the region
 * mounts; the latest text is delivered on mount.
 */
export function announceToRegion(text: string): void {
  if (push) push(text);
  else queued = text;
}

const VISUALLY_HIDDEN = {
  position: "absolute", width: "1px", height: "1px", margin: "-1px", padding: "0", border: "0",
  overflow: "hidden", clip: "rect(0 0 0 0)", clipPath: "inset(50%)", whiteSpace: "nowrap",
} as const;

/** The app's single aria-live region. Render it ONCE, outside the GestureSurface button. */
export function ScreenReaderRegion() {
  const [text, setText] = useState("");
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const mine = (t: string) => {
      // Clear first, then set: TalkBack re-reads a repeated prompt only if the content changes.
      if (timer) clearTimeout(timer);
      setText("");
      timer = setTimeout(() => setText(t), 30);
    };
    push = mine;
    if (queued !== null) { mine(queued); queued = null; }
    return () => {
      if (push === mine) push = null; // an older region unmounting must not silence a newer one
      if (timer) clearTimeout(timer);
    };
  }, []);
  return <div role="status" aria-live="polite" aria-atomic="true" style={VISUALLY_HIDDEN}>{text}</div>;
}
