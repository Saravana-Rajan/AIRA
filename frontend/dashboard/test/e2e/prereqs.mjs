// Decides whether the e2e smoke test can run here. Pure: every check that touches the machine
// (running `java -version`, `firebase --version`) is passed in, so the decisions are unit-tested.
import { delimiter, dirname } from "node:path";

/** firebase-tools major used through npx when no `firebase` CLI is installed. */
export const FIREBASE_TOOLS = "firebase-tools@15";

/** JDKs that are often installed but not on PATH (macOS's /usr/bin/java is only a stub without one). */
const JAVA_CANDIDATES = {
  darwin: [
    "/opt/homebrew/opt/openjdk/bin/java",
    "/opt/homebrew/opt/openjdk@21/bin/java",
    "/opt/homebrew/opt/openjdk@17/bin/java",
    "/usr/local/opt/openjdk/bin/java",
    "/usr/local/opt/openjdk@21/bin/java",
    "/usr/local/opt/openjdk@17/bin/java",
  ],
};

/** The first Java that actually runs: JAVA_HOME, then PATH, then common JDK installs. Null if none. */
export function findJava({ env, platform, works }) {
  const tried = [];
  if (env.JAVA_HOME) tried.push(`${env.JAVA_HOME.replace(/\/+$/, "")}/bin/java`);
  tried.push("java", ...(JAVA_CANDIDATES[platform] ?? []));
  return tried.find((bin) => works(bin)) ?? null;
}

/** The environment for the emulator: a Java found outside PATH becomes JAVA_HOME and leads PATH. */
export function javaEnv(java, env) {
  if (!java || !java.includes("/")) return { ...env };
  const bin = dirname(java);
  return { ...env, JAVA_HOME: dirname(bin), PATH: [bin, env.PATH].filter(Boolean).join(delimiter) };
}

/** The command that runs firebase-tools: FIREBASE_BIN, `firebase` on PATH, else npx. Null if none works. */
export function findFirebase({ env, works }) {
  if (env.FIREBASE_BIN) return [env.FIREBASE_BIN];
  if (works("firebase")) return ["firebase"];
  if (works("npx")) return ["npx", "-y", FIREBASE_TOOLS];
  return null;
}

/**
 * attach: an emulator is already running at FIRESTORE_EMULATOR_HOST; run the tests against it.
 * emulator: start one with `firebase emulators:exec` (needs Java and firebase-tools).
 * skip: say why and exit 0, so machines without a JDK still pass `npm test`.
 */
export function planRun({ hasDeps, emulatorHost, emulatorUp, java, firebase }) {
  if (!hasDeps) {
    return { action: "skip", reason: "the e2e dev dependencies are not installed (@playwright/test, @axe-core/playwright)" };
  }
  if (emulatorHost) {
    return emulatorUp
      ? { action: "attach", emulatorHost }
      : { action: "skip", reason: `FIRESTORE_EMULATOR_HOST=${emulatorHost} is set, but no emulator answers there` };
  }
  if (!java) return { action: "skip", reason: "Java is not available; the Firestore emulator needs a JDK (set JAVA_HOME)" };
  if (!firebase) return { action: "skip", reason: "firebase-tools is not available (install it, or allow npx to fetch it)" };
  return { action: "emulator", java, firebase };
}
