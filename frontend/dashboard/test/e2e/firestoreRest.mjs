// Seeds the Firestore emulator over its REST API, so the e2e test needs no firebase-admin.
// Writes go in as the emulator "owner" (rules bypassed), which only the emulator accepts; the
// dashboard itself still reads through the rules in shared/infra/firestore.rules.

/** demo-* projects keep the emulator fully offline: nothing can reach a real Firebase project. */
export const E2E_PROJECT = "demo-aira";
/** The only database AIRA uses (build overrides §1). */
export const E2E_DATABASE = "aira";

/** "host:port" (FIRESTORE_EMULATOR_HOST) -> { host, port, hostPort }, or null. */
export function parseEmulatorHost(value) {
  const s = typeof value === "string" ? value.trim() : "";
  const m = /^(\[[^\]]+\]|[^:\s]+):(\d+)$/.exec(s);
  if (!m) return null;
  const port = Number(m[2]);
  if (!Number.isInteger(port) || port <= 0 || port >= 65536) return null;
  return { host: m[1].replace(/^\[|\]$/g, ""), port, hostPort: s };
}

/** A JS value as a Firestore REST Value. */
export function toValue(v) {
  if (v === null || v === undefined) return { nullValue: null };
  if (typeof v === "boolean") return { booleanValue: v };
  if (typeof v === "number") {
    if (!Number.isFinite(v)) throw new TypeError(`Firestore cannot store ${v}`);
    return Number.isInteger(v) ? { integerValue: String(v) } : { doubleValue: v };
  }
  if (typeof v === "string") return { stringValue: v };
  if (v instanceof Date) return { timestampValue: v.toISOString() };
  if (Array.isArray(v)) return { arrayValue: { values: v.map(toValue) } };
  if (typeof v === "object") return { mapValue: { fields: toFields(v) } };
  throw new TypeError(`Firestore cannot store a ${typeof v}`);
}

/** An object as Firestore REST fields; undefined fields are left out. */
export function toFields(obj) {
  const out = {};
  for (const [k, v] of Object.entries(obj)) if (v !== undefined) out[k] = toValue(v);
  return out;
}

/** Full resource name of a document path such as "shops/sunrise_dairy/products/x". */
export function docName(projectId, databaseId, path) {
  const parts = String(path).split("/");
  if (parts.length % 2 !== 0 || parts.some((p) => p === "")) throw new Error(`Not a document path: "${path}"`);
  return `projects/${projectId}/databases/${databaseId}/documents/${path}`;
}

const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "0.0.0.0"]);

/** Seeding and clearing only ever talk to a local emulator for a demo-* project. */
function emulatorBase(emulatorHost, projectId) {
  const parsed = parseEmulatorHost(emulatorHost);
  if (!parsed || !LOCAL_HOSTS.has(parsed.host)) {
    throw new Error(`Not a local Firestore emulator: "${emulatorHost}"`);
  }
  if (!String(projectId).startsWith("demo-")) {
    throw new Error(`The e2e test only writes to demo-* projects, not "${projectId}"`);
  }
  return `http://${parsed.hostPort}`;
}

async function check(res, what) {
  if (res.ok) return;
  let text = "";
  try {
    text = await res.text();
  } catch {
    // no body
  }
  throw new Error(`${what} failed: ${res.status} ${text}`.trim());
}

/** Writes every doc (full replace) in one atomic commit. */
export async function commitDocs({ emulatorHost, projectId, databaseId, docs, fetchImpl = fetch }) {
  const base = emulatorBase(emulatorHost, projectId);
  const writes = docs.map((d) => ({ update: { name: docName(projectId, databaseId, d.path), fields: toFields(d.data) } }));
  const res = await fetchImpl(`${base}/v1/projects/${projectId}/databases/${databaseId}/documents:commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer owner" },
    body: JSON.stringify({ writes }),
  });
  await check(res, "Seeding the Firestore emulator");
}

/** Deletes every document in the emulator's database (emulator-only endpoint). */
export async function clearDatabase({ emulatorHost, projectId, databaseId, fetchImpl = fetch }) {
  const base = emulatorBase(emulatorHost, projectId);
  const res = await fetchImpl(`${base}/emulator/v1/projects/${projectId}/databases/${databaseId}/documents`, {
    method: "DELETE",
  });
  await check(res, "Clearing the Firestore emulator");
}

/** True when something answers HTTP at the emulator address. */
export async function emulatorReachable(emulatorHost, fetchImpl = fetch, timeoutMs = 2000) {
  const parsed = parseEmulatorHost(emulatorHost);
  if (!parsed) return false;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    await fetchImpl(`http://${parsed.hostPort}/`, { signal: ctrl.signal });
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}
