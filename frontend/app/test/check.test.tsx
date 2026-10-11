import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { DeviceCheck } from "../src/check/DeviceCheck";
import {
  SPEECH_RMS, SpeechDetector, askAiAboutCamera, cameraLine, deviceKind, httpBase, micLine, rmsOf, testMicrophone,
  type CameraResult, type MicResult,
} from "../src/check/checks";

// ---------------------------------------------------------------- pure helpers

describe("deviceKind", () => {
  it("phones from userAgentData or the user agent, everything else is a laptop", () => {
    expect(deviceKind({ userAgentData: { mobile: true } })).toBe("phone");
    expect(deviceKind({ userAgentData: { mobile: false }, userAgent: "Android" })).toBe("laptop");
    expect(deviceKind({ userAgent: "Mozilla/5.0 (Linux; Android 14) Mobile Safari" })).toBe("phone");
    expect(deviceKind({ userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)" })).toBe("laptop");
    expect(deviceKind(undefined)).toBe("laptop");
  });
});

describe("SpeechDetector", () => {
  it("passes after 300 ms of voice in total, and room hiss never adds up", () => {
    const d = new SpeechDetector();
    for (let i = 0; i < 100; i++) d.push(SPEECH_RMS / 4, 50);
    expect(d.passed).toBe(false);
    expect(d.push(0.1, 100)).toBe(false);
    d.push(0.001, 1000);                       // a pause does not reset it
    expect(d.push(0.05, 200)).toBe(true);
  });
  it("rmsOf", () => {
    expect(rmsOf(new Float32Array([0.5, -0.5, 0.5, -0.5]))).toBeCloseTo(0.5);
    expect(rmsOf(new Float32Array([]))).toBe(0);
  });
});

describe("httpBase", () => {
  it("turns the live socket address into the server's web address", () => {
    expect(httpBase("wss://aira-live-abc.a.run.app/ws")).toBe("https://aira-live-abc.a.run.app");
    expect(httpBase("ws://localhost:8080/ws")).toBe("http://localhost:8080");
    expect(httpBase("not a url")).toBe("");
  });
});

describe("micLine / cameraLine", () => {
  it("say what happened in the owner's language, and a camera pass repeats what the AI saw", () => {
    expect(micLine({ ok: true }, "en")).toBe("Microphone works.");
    expect(micLine({ ok: false, problem: "blocked" }, "en")).toContain("blocked");
    expect(cameraLine({ ok: true, seen: "a person at a desk" }, "en")).toBe("Camera works. I can see a person at a desk.");
    expect(cameraLine({ ok: true, seen: "மேசை" }, "ta")).toContain("மேசை");
    expect(cameraLine({ ok: false, problem: "dark" }, "hi")).toContain("अँधेरी");
  });
});

// ---------------------------------------------------------------- the AI camera call

function response(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

describe("askAiAboutCamera", () => {
  const opts = { base: "https://live.example", lang: "en" as const, device: "laptop" as const };

  it("posts the picture with the language, device and app key, and returns what the AI saw", async () => {
    const f = vi.fn(async () => response(200, { ok: true, seen: "a person at a desk", problem: null }));
    const r = await askAiAboutCamera("/9j/abc", { ...opts, key: "k1", fetch: f as unknown as typeof fetch });
    expect(r).toEqual({ ok: true, seen: "a person at a desk" });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://live.example/api/device-check/camera");
    expect(JSON.parse(init.body as string)).toEqual({ image: "/9j/abc", lang: "en", device: "laptop", key: "k1" });
  });

  it("maps every way it can fail", async () => {
    const run = async (res: Response | Error): Promise<CameraResult> =>
      askAiAboutCamera("/9j/abc", { ...opts, fetch: (async () => { if (res instanceof Error) throw res; return res; }) as typeof fetch });
    expect(await run(response(200, { ok: false, seen: "", problem: "covered" }))).toEqual({ ok: false, problem: "covered" });
    expect(await run(response(200, { ok: true, seen: "  " }))).toEqual({ ok: false, problem: "unclear" });
    expect(await run(response(200, { ok: false, problem: "weird" }))).toEqual({ ok: false, problem: "unclear" });
    expect(await run(response(429, {}))).toEqual({ ok: false, problem: "busy" });
    expect(await run(response(503, {}))).toEqual({ ok: false, problem: "unavailable" });
    expect(await run(response(403, {}))).toEqual({ ok: false, problem: "unavailable" });
    expect(await run(new TypeError("Failed to fetch"))).toEqual({ ok: false, problem: "offline" });
  });
});

// ---------------------------------------------------------------- the mic test

function fakeAudio(levels: number[], state: AudioContextState = "running") {
  const stop = vi.fn();
  const stream = { getTracks: () => [{ stop }] } as unknown as MediaStream;
  let i = 0;
  const ctx = {
    state,
    resume: vi.fn(async () => undefined),
    close: vi.fn(async () => undefined),
    createAnalyser: () => ({
      fftSize: 4,
      getFloatTimeDomainData: (buf: Float32Array) => buf.fill(levels[Math.min(i++, levels.length - 1)]),
    }),
    createMediaStreamSource: () => ({ connect: () => undefined }),
  } as unknown as AudioContext;
  let t = 0;
  return {
    stop,
    ctx,
    deps: {
      getUserMedia: async () => stream,
      makeContext: () => ctx,
      sleep: async () => { t += 50; },
      now: () => t,
      timeoutMs: 2000,
    },
  };
}

describe("testMicrophone", () => {
  it("passes when it hears a voice, and always closes the mic", async () => {
    const a = fakeAudio([0.001, 0.001, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]);
    const levels: number[] = [];
    expect(await testMicrophone({ ...a.deps, onLevel: (r) => levels.push(r) })).toEqual({ ok: true });
    expect(a.stop).toHaveBeenCalled();
    expect(levels.length).toBeGreaterThan(3);
  });
  it("is silent when nothing louder than room hiss comes in before the timeout", async () => {
    const a = fakeAudio([0.002]);
    expect(await testMicrophone(a.deps)).toEqual({ ok: false, problem: "silent" });
    expect(a.stop).toHaveBeenCalled();
  });
  it("tells a blocked mic from a missing one", async () => {
    const deny = async () => { throw Object.assign(new Error("no"), { name: "NotAllowedError" }); };
    const none = async () => { throw Object.assign(new Error("no"), { name: "NotFoundError" }); };
    const a = fakeAudio([0]);
    expect(await testMicrophone({ ...a.deps, getUserMedia: deny })).toEqual({ ok: false, problem: "blocked" });
    expect(await testMicrophone({ ...a.deps, getUserMedia: none })).toEqual({ ok: false, problem: "missing" });
  });
  it("asks for a tap when the browser keeps audio suspended", async () => {
    const a = fakeAudio([0.1], "suspended");
    expect(await testMicrophone(a.deps)).toEqual({ ok: false, problem: "suspended" });
    expect(a.stop).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------- the screen

let root: HTMLElement;
beforeEach(() => { root = document.createElement("div"); document.body.appendChild(root); });
afterEach(() => { act(() => render(null, root)); root.remove(); });

const settle = async () => { for (let i = 0; i < 10; i++) await act(async () => { await Promise.resolve(); }); };
const goBtn = () => root.querySelector<HTMLButtonElement>("button.check__go");
const retryBtns = () => root.querySelectorAll<HTMLButtonElement>("button.check__retry");

function queue<T>(...items: T[]) {
  const fn = vi.fn(async () => items.shift()!);
  return fn;
}

describe("DeviceCheck", () => {
  it("tests the mic, then the camera, says what the AI saw, and opens the conversation on the Start talking tap", async () => {
    const said: string[] = [];
    const onPass = vi.fn();
    const micTest = queue<MicResult>({ ok: true });
    const cameraTest = queue<CameraResult>({ ok: true, seen: "a person at a desk" });
    act(() => {
      render(<DeviceCheck lang="en" device="laptop" onPass={onPass} micTest={micTest} cameraTest={cameraTest}
        speak={(t) => said.push(t)} />, root);
    });
    await settle();
    expect(said[0]).toBe("Checking the microphone. Say: Hello AIRA.");
    expect(said).toContain("Checking the camera. Look at the laptop camera.");
    expect(said.at(-1)).toBe("Camera works. I can see a person at a desk. Microphone and camera work. Tap anywhere to start talking.");
    expect(root.querySelector('[role="status"]')!.textContent).toBe(said.at(-1));
    expect(onPass).not.toHaveBeenCalled();
    act(() => { goBtn()!.click(); goBtn()!.click(); });
    expect(onPass).toHaveBeenCalledTimes(1);           // a double tap opens the conversation once
  });

  it("a phone is asked to point at what is in front of it", async () => {
    const said: string[] = [];
    act(() => {
      render(<DeviceCheck lang="en" device="phone" onPass={() => undefined} micTest={queue<MicResult>({ ok: true })}
        cameraTest={queue<CameraResult>({ ok: true, seen: "milk packets" })} speak={(t) => said.push(t)} />, root);
    });
    await settle();
    expect(said).toContain("Checking the camera. Point the phone at what is in front of you.");
  });

  it("a silent mic stops there: no camera test, no way on, until a retry passes", async () => {
    const said: string[] = [];
    const micTest = queue<MicResult>({ ok: false, problem: "silent" }, { ok: true });
    const cameraTest = queue<CameraResult>({ ok: true, seen: "a desk" });
    act(() => {
      render(<DeviceCheck lang="en" device="laptop" onPass={() => undefined} micTest={micTest} cameraTest={cameraTest}
        speak={(t) => said.push(t)} />, root);
    });
    await settle();
    expect(said.at(-1)).toContain("I heard nothing");
    expect(cameraTest).not.toHaveBeenCalled();
    expect(goBtn()).toBeNull();
    expect(root.querySelector(".check__step--failed")).not.toBeNull();
    act(() => { retryBtns()[0].click(); });
    await settle();
    expect(cameraTest).toHaveBeenCalledTimes(1);
    expect(goBtn()).not.toBeNull();
  });

  it("a dark camera picture is explained, and a retry that passes lets the owner on", async () => {
    const said: string[] = [];
    const cameraTest = queue<CameraResult>({ ok: false, problem: "dark" }, { ok: true, seen: "a desk" });
    act(() => {
      render(<DeviceCheck lang="en" device="laptop" onPass={() => undefined} micTest={queue<MicResult>({ ok: true })}
        cameraTest={cameraTest} speak={(t) => said.push(t)} />, root);
    });
    await settle();
    expect(said.at(-1)).toBe("The picture is too dark. Turn on a light, then try again.");
    expect(goBtn()).toBeNull();
    act(() => { retryBtns()[0].click(); });
    await settle();
    expect(goBtn()).not.toBeNull();
  });

  it("a hands-free start continues by itself once both pass, with no button", async () => {
    const onPass = vi.fn();
    act(() => {
      render(<DeviceCheck lang="ta" device="phone" autoContinue onPass={onPass} micTest={queue<MicResult>({ ok: true })}
        cameraTest={queue<CameraResult>({ ok: true, seen: "பால் பாக்கெட்டுகள்" })} speak={() => undefined} />, root);
    });
    await settle();
    expect(onPass).toHaveBeenCalledTimes(1);
    expect(goBtn()).toBeNull();
  });
});
