import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render } from "preact";
import { act } from "preact/test-utils";
import { DeviceCheck } from "../src/check/DeviceCheck";
import {
  SPEECH_RMS, SpeechDetector, airaVoice, askAiAboutCamera, cameraLine, deviceKind, httpBase, micLine, rmsOf, testMicrophone,
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
        speak={(t) => { said.push(t); }} />, root);
    });
    await settle();
    expect(said[0]).toBe("Checking the microphone. Say: Hello AIRA.");
    expect(said).toContain("Checking the camera. Look at the laptop camera.");
    expect(said.slice(-2)).toEqual(["Camera works. I can see a person at a desk.",
      "Microphone and camera work. Tap anywhere to start talking."]);
    expect(root.querySelector('[role="status"]')!.textContent).toBe(said.at(-1));
    expect(onPass).not.toHaveBeenCalled();
    act(() => { goBtn()!.click(); goBtn()!.click(); });
    expect(onPass).toHaveBeenCalledTimes(1);           // a double tap opens the conversation once
  });

  it("a phone is asked to point at what is in front of it", async () => {
    const said: string[] = [];
    act(() => {
      render(<DeviceCheck lang="en" device="phone" onPass={() => undefined} micTest={queue<MicResult>({ ok: true })}
        cameraTest={queue<CameraResult>({ ok: true, seen: "milk packets" })} speak={(t) => { said.push(t); }} />, root);
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
        speak={(t) => { said.push(t); }} />, root);
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
        cameraTest={cameraTest} speak={(t) => { said.push(t); }} />, root);
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

// ---------------------------------------------------------------- the real camera path

describe("DeviceCheck with the real camera path", () => {
  it("previews a copy of the camera feed and never moves AIRA's own camera element (it would freeze)", async () => {
    const stream = { id: "cam-stream" } as unknown as MediaStream;
    const shared = document.createElement("video");
    Object.defineProperty(shared, "videoWidth", { value: 640 });
    Object.defineProperty(shared, "videoHeight", { value: 360 });
    shared.srcObject = stream;
    const capture = await import("../src/camera/capture");
    const start = vi.spyOn(capture, "startCamera").mockResolvedValue(shared);
    const grab = vi.spyOn(capture, "captureFrame").mockResolvedValue({ t: "frame", id: "f1", jpeg: "/9j/abc", w: 640, h: 360, ts: 1, purpose: "ask" });
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(response(200, { ok: true, seen: "a person in a room" }));
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const onPass = vi.fn();
      act(() => {
        render(<DeviceCheck lang="en" device="laptop" onPass={onPass} wsUrl="ws://localhost:8080/ws"
          micTest={queue<MicResult>({ ok: true })} speak={() => undefined} />, root);
      });
      for (let i = 0; i < 30; i++) { await act(async () => { await vi.advanceTimersByTimeAsync(100); }); }
      expect(start).toHaveBeenCalled();
      expect(grab).toHaveBeenCalledWith("ask");
      expect(fetchSpy.mock.calls[0][0]).toBe("http://localhost:8080/api/device-check/camera");
      const preview = root.querySelector(".check__preview video") as HTMLVideoElement;
      expect(preview).not.toBeNull();
      expect(preview).not.toBe(shared);
      expect(preview.srcObject).toBe(stream);
      expect(shared.parentElement).toBeNull();               // AIRA's element stays out of the page
      expect(goBtn()).not.toBeNull();
      act(() => render(null, root));
      expect(shared.parentElement).toBeNull();
    } finally {
      vi.useRealTimers();
      vi.restoreAllMocks();
    }
  });
});


// ---------------------------------------------------------------- AIRA's voice on the check screen

describe("airaVoice", () => {
  const wav = new Blob([new Uint8Array([82, 73, 70, 70, 1, 2, 3])], { type: "audio/wav" });
  const ok = () => ({ ok: true, status: 200, blob: async () => wav }) as unknown as Response;

  it("plays the line in AIRA's voice, fetched once even when prefetched first", async () => {
    const f = vi.fn(async () => ok());
    const play = vi.fn(async () => undefined);
    const fallback = vi.fn(async () => undefined);
    const v = airaVoice({ base: "https://live.example", key: "k1", fetch: f as unknown as typeof fetch, play, fallback });
    const line = { key: "camOk" as const, text: "Camera works. I can see a desk.", seen: "a desk" };
    v.prefetch(line, "en");
    await v.speak(line, "en", 1);
    expect(f).toHaveBeenCalledTimes(1);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://live.example/api/device-check/say");
    expect(JSON.parse(init.body as string)).toEqual({ line: "camOk", lang: "en", seen: "a desk", key: "k1" });
    expect(play).toHaveBeenCalledWith(wav);
    expect(fallback).not.toHaveBeenCalled();
  });

  it("falls back to the device's voice when the server has no voice, fails, or the clip will not play", async () => {
    for (const make of [
      () => ({ ok: true, status: 204, blob: async () => new Blob([]) }) as unknown as Response,
      () => ({ ok: false, status: 503, blob: async () => new Blob([]) }) as unknown as Response,
      () => { throw new TypeError("Failed to fetch"); },
    ]) {
      const fallback = vi.fn(async () => undefined);
      const v = airaVoice({ base: "b", fetch: (async () => make()) as typeof fetch, play: async () => undefined, fallback });
      await v.speak({ key: "micOk", text: "Microphone works." }, "en", 1);
      expect(fallback).toHaveBeenCalledWith("Microphone works.", "en", 1);
    }
    const fallback = vi.fn(async () => undefined);
    const v = airaVoice({ base: "b", fetch: (async () => ok()) as typeof fetch,
      play: async () => { throw new Error("NotAllowedError"); }, fallback });
    await v.speak({ key: "micOk", text: "Microphone works." }, "en", 1);
    expect(fallback).toHaveBeenCalledTimes(1);
  });
});

describe("DeviceCheck waits for AIRA to finish speaking", () => {
  it("opens the mic only after the instruction has been spoken, so AIRA's own voice cannot pass the test", async () => {
    let finishSpeaking: () => void = () => undefined;
    const speak = vi.fn((t: string) => (t.startsWith("Checking the microphone")
      ? new Promise<void>((r) => { finishSpeaking = r; }) : undefined));
    const micTest = queue<MicResult>({ ok: true });
    act(() => {
      render(<DeviceCheck lang="en" device="laptop" onPass={() => undefined} micTest={micTest}
        cameraTest={queue<CameraResult>({ ok: true, seen: "a desk" })} speak={speak} />, root);
    });
    await settle();
    expect(micTest).not.toHaveBeenCalled();                  // still speaking
    await act(async () => { finishSpeaking(); });
    await settle();
    expect(micTest).toHaveBeenCalledTimes(1);
  });
});

