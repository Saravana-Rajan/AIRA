import "./screen-parts.css";
import type { ChipKind } from "./view";

/**
 * Top-bar status pill (overrides §6): ready blue, listening cyan with a pulse, thinking purple,
 * counting blue, warning/offline orange, stop solid red. Its colour follows its own `kind`, so it
 * also works outside `.screen`. Phrasing content only: it sits inside the GestureSurface button.
 * Build `label` with `statusLabel(kind, lang, count)`, or pass the screen reducer's label.
 */
export function StatusChip({ kind, label }: { kind: ChipKind; label: string }) {
  return (
    <span class="status-chip" data-kind={kind}>
      <span class="status-chip__dot" aria-hidden="true" />
      {label}
    </span>
  );
}
