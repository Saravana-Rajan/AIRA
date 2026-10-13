// frontend/app/src/camera/looking.ts
// "AIRA is looking": which tool frame (ask, count, read, walk...) is being captured right now, for the phone's
// camera preview badge. A tiny pub/sub with no DOM; capture.ts reports each tool capture's start and end.
import type { FramePurpose } from "@aira/contracts";

/** Tool purposes the badge shows. Background streams (live, preview, describe, guide) never light it. */
const SHOWN: ReadonlySet<FramePurpose> = new Set<FramePurpose>([
  "ask", "count", "point", "identify", "identify_held", "invoice", "cash", "walk",
]);
/** Purposes that read printed text or notes ("Reading…"). */
const READING: ReadonlySet<FramePurpose> = new Set<FramePurpose>(["invoice", "cash"]);

export type LookingKind = "looking" | "counting" | "reading";

export function lookingKind(purpose: FramePurpose): LookingKind {
  if (purpose === "count") return "counting";
  return READING.has(purpose) ? "reading" : "looking";
}

export function showsLooking(purpose: FramePurpose): boolean {
  return SHOWN.has(purpose);
}

const active: FramePurpose[] = [];
const subs = new Set<(p: FramePurpose | null) => void>();
const emit = () => {
  const p = currentLooking();
  subs.forEach((fn) => { try { fn(p); } catch { /* a bad subscriber never blocks capture */ } });
};

/** The newest tool capture in progress, or null. */
export function currentLooking(): FramePurpose | null {
  return active.length ? active[active.length - 1] : null;
}

/** Marks a tool capture as started; returns its (idempotent) end fn. Purposes the badge ignores are a no-op. */
export function beginLooking(purpose: FramePurpose): () => void {
  if (!SHOWN.has(purpose)) return () => {};
  active.push(purpose);
  emit();
  let done = false;
  return () => {
    if (done) return;
    done = true;
    const i = active.lastIndexOf(purpose);
    if (i >= 0) active.splice(i, 1);
    emit();
  };
}

export function subscribeLooking(fn: (p: FramePurpose | null) => void): () => void {
  subs.add(fn);
  return () => { subs.delete(fn); };
}

/** Tests only. */
export function __resetLooking(): void {
  active.length = 0;
  subs.clear();
}
