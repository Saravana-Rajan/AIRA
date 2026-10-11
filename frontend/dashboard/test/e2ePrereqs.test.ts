// @vitest-environment node
// `node test/e2e/run.mjs` runs the smoke test when the Firestore emulator can run here and skips it
// (exit 0, with the reason) when it cannot: no Java, no firebase-tools or no Playwright.
import { describe, expect, it } from "vitest";
import { findFirebase, findJava, javaEnv, planRun } from "./e2e/prereqs.mjs";

const works = (ok: string[]) => (cmd: string) => ok.includes(cmd);

describe("findJava", () => {
  it("prefers JAVA_HOME", () => {
    expect(findJava({ env: { JAVA_HOME: "/jdk" }, platform: "linux", works: works(["/jdk/bin/java", "java"]) })).toBe("/jdk/bin/java");
  });

  it("uses java on PATH when it runs", () => {
    expect(findJava({ env: {}, platform: "linux", works: works(["java"]) })).toBe("java");
  });

  it("skips the macOS /usr/bin/java stub that has no JDK behind it and finds a Homebrew JDK", () => {
    const found = findJava({ env: {}, platform: "darwin", works: works(["/opt/homebrew/opt/openjdk@21/bin/java"]) });
    expect(found).toBe("/opt/homebrew/opt/openjdk@21/bin/java");
  });

  it("returns null when no Java runs", () => {
    expect(findJava({ env: { JAVA_HOME: "/broken" }, platform: "darwin", works: works([]) })).toBeNull();
  });
});

describe("javaEnv", () => {
  it("points JAVA_HOME and PATH at a Java found outside PATH", () => {
    const env = javaEnv("/opt/homebrew/opt/openjdk@21/bin/java", { PATH: "/usr/bin" });
    expect(env.JAVA_HOME).toBe("/opt/homebrew/opt/openjdk@21");
    expect(env.PATH?.split(":")[0]).toBe("/opt/homebrew/opt/openjdk@21/bin");
  });

  it("changes nothing for java already on PATH", () => {
    expect(javaEnv("java", { PATH: "/usr/bin" })).toEqual({ PATH: "/usr/bin" });
  });
});

describe("findFirebase", () => {
  it("uses FIREBASE_BIN, then firebase on PATH, then npx firebase-tools", () => {
    expect(findFirebase({ env: { FIREBASE_BIN: "/x/firebase" }, works: works([]) })).toEqual(["/x/firebase"]);
    expect(findFirebase({ env: {}, works: works(["firebase", "npx"]) })).toEqual(["firebase"]);
    const npx = findFirebase({ env: {}, works: works(["npx"]) });
    expect(npx?.slice(0, 2)).toEqual(["npx", "-y"]);
    expect(npx?.[2]).toMatch(/^firebase-tools@\d+$/);
  });

  it("returns null when firebase-tools cannot be run (offline, no npx)", () => {
    expect(findFirebase({ env: {}, works: works([]) })).toBeNull();
  });
});

describe("planRun", () => {
  const ready: Parameters<typeof planRun>[0] = { hasDeps: true, emulatorHost: null, emulatorUp: false, java: "java", firebase: ["firebase"] };

  it("starts the emulator when Java and firebase-tools are there", () => {
    expect(planRun(ready)).toEqual({ action: "emulator", java: "java", firebase: ["firebase"] });
  });

  it("uses an emulator that is already running, without needing Java", () => {
    expect(planRun({ ...ready, emulatorHost: "127.0.0.1:8085", emulatorUp: true, java: null, firebase: null })).toEqual({
      action: "attach",
      emulatorHost: "127.0.0.1:8085",
    });
  });

  it("skips, with the reason, when something is missing", () => {
    const cases: Array<[Partial<typeof ready>, RegExp]> = [
      [{ hasDeps: false }, /@playwright\/test/],
      [{ java: null }, /Java/],
      [{ firebase: null }, /firebase-tools/],
      [{ emulatorHost: "127.0.0.1:8085", emulatorUp: false }, /FIRESTORE_EMULATOR_HOST/],
    ];
    for (const [change, reason] of cases) {
      const plan = planRun({ ...ready, ...change });
      expect(plan.action).toBe("skip");
      expect(plan.action === "skip" && plan.reason).toMatch(reason);
    }
  });
});
