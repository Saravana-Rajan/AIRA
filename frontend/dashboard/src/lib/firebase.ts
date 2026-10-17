import { initializeApp } from "firebase/app";
import { connectFirestoreEmulator, getFirestore } from "firebase/firestore";
import { emulatorTarget, firebaseOptions, firestoreDatabaseId, missingFirebaseConfig } from "./firebaseConfig";

// No Firebase Auth in this build: the demo shop is publicly readable and the dashboard never writes.
// This module is loaded on demand (connect.ts lazyListen). Throwing here shows the page as "Offline"
// with a clear message instead of a blank screen.
const env = import.meta.env as Record<string, unknown>;

const missing = missingFirebaseConfig(env);
if (missing.length > 0) {
  throw new Error(`Firebase web config is missing: ${missing.join(", ")}`);
}

const app = initializeApp(firebaseOptions(env));

/** Firestore database from VITE_FIRESTORE_DATABASE: "aira", never the default database. */
export const db = getFirestore(app, firestoreDatabaseId(env));

const emulator = emulatorTarget(env);
if (emulator) connectFirestoreEmulator(db, emulator.host, emulator.port);
