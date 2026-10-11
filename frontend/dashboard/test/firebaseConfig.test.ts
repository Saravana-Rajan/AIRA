// Firebase web config for the dashboard comes only from build-time env (VITE_*). No Auth, no Storage.
import { describe, expect, it } from "vitest";
import {
  DEFAULT_DATABASE,
  emulatorTarget,
  firebaseOptions,
  firestoreDatabaseId,
  missingFirebaseConfig,
} from "../src/lib/firebaseConfig";

const FULL = {
  VITE_FIREBASE_API_KEY: " key-1 ",
  VITE_FIREBASE_AUTH_DOMAIN: "shop.firebaseapp.com",
  VITE_FIREBASE_PROJECT_ID: "shop-project",
  VITE_FIREBASE_STORAGE_BUCKET: "shop.appspot.com",
  VITE_FIREBASE_MESSAGING_SENDER_ID: "123",
  VITE_FIREBASE_APP_ID: "1:123:web:abc",
};

describe("firestoreDatabaseId", () => {
  it("defaults to the database aira", () => {
    expect(DEFAULT_DATABASE).toBe("aira");
    expect(firestoreDatabaseId({})).toBe("aira");
    expect(firestoreDatabaseId({ VITE_FIRESTORE_DATABASE: "   " })).toBe("aira");
  });

  it("reads VITE_FIRESTORE_DATABASE", () => {
    expect(firestoreDatabaseId({ VITE_FIRESTORE_DATABASE: " aira-test " })).toBe("aira-test");
  });

  it("never opens the (default) database, even when the env asks for it", () => {
    expect(firestoreDatabaseId({ VITE_FIRESTORE_DATABASE: "(default)" })).toBe("aira");
  });
});

describe("firebaseOptions", () => {
  it("maps the VITE_FIREBASE_* variables to the web config, trimmed", () => {
    expect(firebaseOptions(FULL)).toEqual({
      apiKey: "key-1",
      authDomain: "shop.firebaseapp.com",
      projectId: "shop-project",
      storageBucket: "shop.appspot.com",
      messagingSenderId: "123",
      appId: "1:123:web:abc",
    });
  });

  it("leaves out variables that are empty or not strings", () => {
    expect(firebaseOptions({ VITE_FIREBASE_PROJECT_ID: "p", VITE_FIREBASE_API_KEY: "", VITE_FIREBASE_APP_ID: 7 })).toEqual({ projectId: "p" });
  });
});

describe("missingFirebaseConfig", () => {
  it("is empty when the project id and API key are set", () => {
    expect(missingFirebaseConfig(FULL)).toEqual([]);
  });

  it("names the variables that must be set before the dashboard can read Firestore", () => {
    expect(missingFirebaseConfig({})).toEqual(["VITE_FIREBASE_PROJECT_ID", "VITE_FIREBASE_API_KEY"]);
    expect(missingFirebaseConfig({ VITE_FIREBASE_PROJECT_ID: "p" })).toEqual(["VITE_FIREBASE_API_KEY"]);
  });
});

describe("emulatorTarget", () => {
  it("is null unless VITE_USE_EMULATOR is 1", () => {
    expect(emulatorTarget({})).toBeNull();
    expect(emulatorTarget({ VITE_USE_EMULATOR: "0", VITE_FIRESTORE_EMULATOR_PORT: "9000" })).toBeNull();
  });

  it("points at 127.0.0.1 on port 8085 by default", () => {
    expect(emulatorTarget({ VITE_USE_EMULATOR: "1" })).toEqual({ host: "127.0.0.1", port: 8085 });
  });

  it("uses VITE_FIRESTORE_EMULATOR_PORT when it is a valid port", () => {
    expect(emulatorTarget({ VITE_USE_EMULATOR: "1", VITE_FIRESTORE_EMULATOR_PORT: "9090" })).toEqual({ host: "127.0.0.1", port: 9090 });
    expect(emulatorTarget({ VITE_USE_EMULATOR: "1", VITE_FIRESTORE_EMULATOR_PORT: "nope" })).toEqual({ host: "127.0.0.1", port: 8085 });
  });
});
