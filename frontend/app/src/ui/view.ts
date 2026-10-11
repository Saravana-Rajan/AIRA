// Pure helpers behind the status chip, captions and task card. No DOM.
import type { Lang } from "@aira/contracts";
import { LABELS } from "../i18n/strings";
import type { StatusKind } from "./screenState";

/** Chip states from overrides §6 (the screen reducer's StatusKind), plus a local "offline". */
export type ChipKind = StatusKind | "offline";

export interface CountProgress { counted: number; expected: number | null }

/** Localised chip text. Counting carries the live tally: "Counting 12 / 30". */
export function statusLabel(kind: ChipKind, lang: Lang, count?: CountProgress | null): string {
  const base = LABELS[kind][lang];
  if (kind !== "counting" || !count) return base;
  return count.expected === null ? `${base} ${count.counted}` : `${base} ${count.counted} / ${count.expected}`;
}

/** Fraction of a delivery counted, clamped to 0..1; null when there is no expected total. */
export function progressOf(counted: number, expected: number | null): number | null {
  if (expected === null || !(expected > 0)) return null;
  return Math.min(1, Math.max(0, counted / expected));
}

export interface Caption { text: string; final: boolean }

/**
 * Builds one live caption from streamed transcripts. Partial chunks may be deltas
 * ("Two Sakthi", " curd") or cumulative ("Two Sakthi curd, 120"). A final usually carries
 * the whole sentence (ADK) and replaces the partials; a final that is only the last fragment
 * (shorter, and not a restart of the sentence) is appended instead. After a final, the next
 * message starts a new caption.
 */
export function mergeCaption(prev: Caption, next: Caption): Caption {
  if (prev.final || !prev.text) return { text: next.text, final: next.final };
  if (next.text.startsWith(prev.text)) return { text: next.text, final: next.final };
  if (next.final && next.text.trim().length >= prev.text.trim().length) return { text: next.text, final: true };
  return { text: prev.text + next.text, final: next.final };
}

/** Keeps the END of a long caption (the newest words), cut on a word boundary. */
export function captionTail(text: string, max = 140): string {
  const t = text.trim();
  if (t.length <= max) return t;
  const slice = t.slice(-max);
  const startsOnWord = t[t.length - max - 1] === " ";
  const sp = slice.indexOf(" ");
  const body = startsOnWord || sp < 0 || sp === slice.length - 1 ? slice : slice.slice(sp + 1);
  return "…" + body.trimStart();
}
