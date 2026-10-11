// Firebase web config for the Shop Dashboard, read from build-time env (VITE_*). Pure, so it is tested
// without starting Firebase. Build overrides §1: Firestore database "aira" only, no Auth, no Storage.
import type { FirebaseOptions } from "firebase/app";

type Env = Record<string, unknown>;

/** The only Firestore database AIRA uses. "(default)" is never opened. */
export const DEFAULT_DATABASE = "aira";

const str = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

/** VITE_FIRESTORE_DATABASE, or "aira". A request for "(default)" still gets "aira". */
export function firestoreDatabaseId(env: Env): string {
  const id = str(env.VITE_FIRESTORE_DATABASE);
  return id && id !== "(default)" ? id : DEFAULT_DATABASE;
}

const OPTION_ENV: Array<[keyof FirebaseOptions, string]> = [
  ["apiKey", "VITE_FIREBASE_API_KEY"],
  ["authDomain", "VITE_FIREBASE_AUTH_DOMAIN"],
  ["projectId", "VITE_FIREBASE_PROJECT_ID"],
  ["storageBucket", "VITE_FIREBASE_STORAGE_BUCKET"],
  ["messagingSenderId", "VITE_FIREBASE_MESSAGING_SENDER_ID"],
  ["appId", "VITE_FIREBASE_APP_ID"],
];

/** The Firebase web config from the VITE_FIREBASE_* variables. Empty ones are left out. */
export function firebaseOptions(env: Env): FirebaseOptions {
  const out: FirebaseOptions = {};
  for (const [key, name] of OPTION_ENV) {
    const v = str(env[name]);
    if (v) out[key] = v;
  }
  return out;
}

/** The variables Firestore cannot work without that are not set (empty when the config is usable). */
export function missingFirebaseConfig(env: Env): string[] {
  return ["VITE_FIREBASE_PROJECT_ID", "VITE_FIREBASE_API_KEY"].filter((name) => !str(env[name]));
}

const DEFAULT_EMULATOR_PORT = 8085;

/** Where the Firestore emulator listens when VITE_USE_EMULATOR is "1" (tests); null otherwise. */
export function emulatorTarget(env: Env): { host: string; port: number } | null {
  if (str(env.VITE_USE_EMULATOR) !== "1") return null;
  const port = Number(str(env.VITE_FIRESTORE_EMULATOR_PORT));
  return { host: "127.0.0.1", port: Number.isInteger(port) && port > 0 && port < 65536 ? port : DEFAULT_EMULATOR_PORT };
}
