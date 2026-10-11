// Turns a Firestore document into a plain object the selectors can use:
// adds the doc id and converts Timestamp values (anywhere in the tree) to epoch millis.

/** The JS SDK Timestamp (and anything else exposing toMillis). */
type MillisTimestamp = { toMillis(): number };
/** A Timestamp serialised to JSON, e.g. by the server: { seconds, nanoseconds }. */
type SecondsTimestamp = { seconds: number; nanoseconds: number };
type TimestampValue = MillisTimestamp | SecondsTimestamp;

/** A field that may hold a Timestamp becomes millis; strings (ISO times) and other values stay as they are. */
type NormalizedField<V> =
  unknown extends V ? V
    : [Extract<V, TimestampValue>] extends [never] ? V
      : Exclude<V, TimestampValue> | number;

export type Normalized<T> = { [K in keyof T]: NormalizedField<T[K]> } & { id: string };

function isMillisTimestamp(v: unknown): v is MillisTimestamp {
  return typeof v === "object" && v !== null && typeof (v as MillisTimestamp).toMillis === "function";
}

function isSecondsTimestamp(v: unknown): v is SecondsTimestamp {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return false;
  const keys = Object.keys(v).filter((k) => k !== "toDate");
  const x = v as Record<string, unknown>;
  return keys.length === 2 && typeof x.seconds === "number" && typeof x.nanoseconds === "number";
}

function secondsToMs(t: SecondsTimestamp): number {
  return t.seconds * 1000 + Math.floor(t.nanoseconds / 1_000_000);
}

function convert(v: unknown): unknown {
  if (isMillisTimestamp(v)) return v.toMillis();
  if (isSecondsTimestamp(v)) return secondsToMs(v);
  if (Array.isArray(v)) return v.map(convert);
  if (typeof v === "object" && v !== null) {
    const out: Record<string, unknown> = {};
    for (const [k, inner] of Object.entries(v)) out[k] = convert(inner);
    return out;
  }
  return v;
}

export function normalize<T>(id: string, data: Record<string, unknown>): Normalized<T> {
  return { id, ...(convert(data) as Record<string, unknown>) } as Normalized<T>;
}

/**
 * Reads any time value the dashboard may see (millis, ISO string from seeds/fixtures,
 * a Timestamp) as epoch millis. Returns null when it is missing or unreadable.
 */
export function toMs(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string") {
    const ms = Date.parse(v);
    return Number.isNaN(ms) ? null : ms;
  }
  if (isMillisTimestamp(v)) return v.toMillis();
  if (isSecondsTimestamp(v)) return secondsToMs(v);
  return null;
}
