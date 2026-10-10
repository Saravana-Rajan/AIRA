import { initializeApp } from "firebase/app";
import { connectFirestoreEmulator, getFirestore } from "firebase/firestore";

// No Firebase Auth in this build: the demo shop is publicly readable and the dashboard never writes.
const app = initializeApp({
  apiKey: import.meta.env.VITE_FIREBASE_API_KEY,
  authDomain: import.meta.env.VITE_FIREBASE_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_FIREBASE_PROJECT_ID,
  storageBucket: import.meta.env.VITE_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: import.meta.env.VITE_FIREBASE_MESSAGING_SENDER_ID,
  appId: import.meta.env.VITE_FIREBASE_APP_ID,
});

/** Firestore database "aira" (never "(default)"). */
export const db = getFirestore(app, "aira");

if (import.meta.env.VITE_USE_EMULATOR === "1") {
  connectFirestoreEmulator(db, "localhost", Number(import.meta.env.VITE_FIRESTORE_EMULATOR_PORT ?? 8085));
}
