// frontend/app/test/camera.backCamera.test.ts
// Chest-mounted phone: the BACK main camera must face forward. Fake mediaDevices only.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { pickBackDevice, ensureBackCamera, BACK_CAMERA_KEY, type DeviceLike } from "../src/camera/backCamera";

type Track = { kind: string; stop: ReturnType<typeof vi.fn>; getSettings: () => { facingMode?: string; deviceId?: string } };
const mkStream = (facingMode?: string, deviceId?: string) => {
  const track: Track = { kind: "video", stop: vi.fn(), getSettings: () => ({ facingMode, deviceId }) };
  return { track, stream: { active: true, getVideoTracks: () => [track], getTracks: () => [track] } as unknown as MediaStream };
};
const dev = (deviceId: string, label: string, kind = "videoinput"): DeviceLike => ({ deviceId, label, kind });

function fakeMD(opts: { first: MediaStream; devices: DeviceLike[]; second?: MediaStream; enumerateThrows?: boolean; secondThrows?: boolean }) {
  const getUserMedia = vi.fn(async (_c: MediaStreamConstraints) => {
    if (getUserMedia.mock.calls.length === 1) return opts.first;
    if (opts.secondThrows) throw new Error("NotReadableError");
    return opts.second ?? opts.first;
  });
  const enumerateDevices = vi.fn(async () => {
    if (opts.enumerateThrows) throw new Error("boom");
    return opts.devices as MediaDeviceInfo[];
  });
  return { getUserMedia, enumerateDevices } as unknown as MediaDevices & { getUserMedia: ReturnType<typeof vi.fn>; enumerateDevices: ReturnType<typeof vi.fn> };
}

const memStorage = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), map: m };
};

beforeEach(() => {
  try {
    localStorage.removeItem(BACK_CAMERA_KEY);
  } catch {
    /* ignore */
  }
});

describe("pickBackDevice", () => {
  it("returns null when no back camera is listed", () => {
    expect(pickBackDevice([dev("a", "Front Camera"), dev("b", "camera2 1, facing front"), dev("m", "Mic", "audioinput")])).toBeNull();
  });
  it("matches back, rear, environment and 'facing back' labels", () => {
    expect(pickBackDevice([dev("f", "Front"), dev("r", "Rear camera")])).toBe("r");
    expect(pickBackDevice([dev("f", "Front"), dev("b", "Back Camera")])).toBe("b");
    expect(pickBackDevice([dev("f", "Front"), dev("e", "environment lens")])).toBe("e");
    expect(pickBackDevice([dev("f", "camera2 1, facing front"), dev("c", "camera2 0, facing back")])).toBe("c");
  });
  it("prefers the main lens over ultra-wide, macro, telephoto and camera2 2+", () => {
    const devices = [
      dev("tele", "camera2 3, facing back (telephoto)"),
      dev("uw", "Back Ultra Wide Camera"),
      dev("macro", "camera2 4, facing back macro"),
      dev("main", "camera2 0, facing back"),
      dev("wide", "Back Wide-Angle Camera"),
      dev("front", "camera2 1, facing front"),
    ];
    expect(pickBackDevice(devices)).toBe("main");
    expect(pickBackDevice([...devices].reverse())).toBe("main");
  });
  it("prefers 'camera 0' style labels and falls back to the first plain back lens", () => {
    expect(pickBackDevice([dev("x", "Back Camera 2"), dev("y", "camera 0, facing back")])).toBe("y");
    expect(pickBackDevice([dev("uw", "Back Ultra Wide"), dev("tele", "Back Telephoto")])).toBe("uw");
  });
  it("ignores audio inputs even when the label says back", () => {
    expect(pickBackDevice([dev("m", "back mic", "audioinput")])).toBeNull();
  });
});

describe("ensureBackCamera", () => {
  it("keeps a first stream that already faces the environment", async () => {
    const { stream, track } = mkStream("environment", "env1");
    const md = fakeMD({ first: stream, devices: [dev("env1", "camera2 0, facing back")] });
    const st = memStorage();
    const r = await ensureBackCamera(md, st);
    expect(r.stream).toBe(stream);
    expect(r.facing).toBe("environment");
    expect(track.stop).not.toHaveBeenCalled();
    expect(md.getUserMedia).toHaveBeenCalledTimes(1);
    expect(md.getUserMedia.mock.calls[0][0]).toMatchObject({ video: { facingMode: { ideal: "environment" } } });
    expect(st.getItem(BACK_CAMERA_KEY)).toBe("env1");
  });
  it("switches a user-facing first stream to the back device", async () => {
    const first = mkStream("user", "front");
    const second = mkStream("environment", "back0");
    const md = fakeMD({ first: first.stream, second: second.stream, devices: [dev("front", "camera2 1, facing front"), dev("back0", "camera2 0, facing back")] });
    const st = memStorage();
    const r = await ensureBackCamera(md, st);
    expect(r.stream).toBe(second.stream);
    expect(r.facing).toBe("environment");
    expect(first.track.stop).toHaveBeenCalled();
    expect(md.enumerateDevices).toHaveBeenCalled();
    expect(md.getUserMedia.mock.calls[1][0]).toMatchObject({ video: { deviceId: { exact: "back0" } } });
    expect(st.getItem(BACK_CAMERA_KEY)).toBe("back0");
  });
  it("switches when facingMode is missing and picks the main lens among several back lenses", async () => {
    const first = mkStream(undefined, "uw");
    const second = mkStream(undefined, "main");
    const md = fakeMD({
      first: first.stream,
      second: second.stream,
      devices: [dev("uw", "camera2 2, facing back"), dev("main", "camera2 0, facing back"), dev("tele", "camera2 3, facing back"), dev("front", "camera2 1, facing front")],
    });
    const r = await ensureBackCamera(md, memStorage());
    expect(r.stream).toBe(second.stream);
    expect(md.getUserMedia.mock.calls[1][0]).toMatchObject({ video: { deviceId: { exact: "main" } } });
    expect(r.facing).toBe("environment");
    expect(r.deviceId).toBe("main");
  });
  it("does not reopen when the first stream is already the chosen main back lens", async () => {
    const first = mkStream(undefined, "main");
    const md = fakeMD({ first: first.stream, devices: [dev("main", "camera2 0, facing back")] });
    const r = await ensureBackCamera(md, memStorage());
    expect(r.stream).toBe(first.stream);
    expect(first.track.stop).not.toHaveBeenCalled();
    expect(md.getUserMedia).toHaveBeenCalledTimes(1);
    expect(r.facing).toBe("environment");
  });
  it("keeps the front camera and exposes facing 'user' when no back camera exists", async () => {
    const first = mkStream("user", "front");
    const md = fakeMD({ first: first.stream, devices: [dev("front", "Front Camera")] });
    const st = memStorage();
    const r = await ensureBackCamera(md, st);
    expect(r.stream).toBe(first.stream);
    expect(r.facing).toBe("user");
    expect(first.track.stop).not.toHaveBeenCalled();
    expect(md.getUserMedia).toHaveBeenCalledTimes(1);
    expect(st.getItem(BACK_CAMERA_KEY)).toBeNull();
  });
  it("exposes 'unknown' when facing is missing and labels say nothing", async () => {
    const first = mkStream(undefined, "x");
    const md = fakeMD({ first: first.stream, devices: [dev("x", "")] });
    const r = await ensureBackCamera(md, memStorage());
    expect(r.stream).toBe(first.stream);
    expect(r.facing).toBe("unknown");
  });
  it("reuses the saved deviceId on the next launch", async () => {
    const { stream } = mkStream("environment", "back0");
    const md = fakeMD({ first: stream, devices: [] });
    const st = memStorage();
    st.setItem(BACK_CAMERA_KEY, "back0");
    const r = await ensureBackCamera(md, st);
    expect(r.stream).toBe(stream);
    expect(md.getUserMedia).toHaveBeenCalledTimes(1);
    expect(md.getUserMedia.mock.calls[0][0]).toMatchObject({ video: { deviceId: { ideal: "back0" }, facingMode: { ideal: "environment" } } });
    expect(md.enumerateDevices).not.toHaveBeenCalled();
  });
  it("falls back to the first stream when enumerateDevices throws", async () => {
    const first = mkStream("user", "front");
    const md = fakeMD({ first: first.stream, devices: [], enumerateThrows: true });
    const r = await ensureBackCamera(md, memStorage());
    expect(r.stream).toBe(first.stream);
    expect(r.facing).toBe("user");
    expect(first.track.stop).not.toHaveBeenCalled();
  });
  it("falls back to the first stream when reopening the back device throws", async () => {
    const first = mkStream("user", "front");
    const md = fakeMD({ first: first.stream, devices: [dev("front", "Front"), dev("back0", "Back Camera")], secondThrows: true });
    const r = await ensureBackCamera(md, memStorage());
    expect(r.stream).toBe(first.stream);
    expect(r.facing).toBe("user");
    // The first track is stopped only after a successful reopen.
    expect(first.track.stop).not.toHaveBeenCalled();
  });
  it("survives a storage that throws", async () => {
    const { stream } = mkStream("environment", "env1");
    const md = fakeMD({ first: stream, devices: [] });
    const bad = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => {} };
    const r = await ensureBackCamera(md, bad);
    expect(r.stream).toBe(stream);
    expect(r.facing).toBe("environment");
  });
  it("uses getSettings().facingMode even when the track has no deviceId", async () => {
    const { stream } = mkStream("environment");
    const md = fakeMD({ first: stream, devices: [] });
    const r = await ensureBackCamera(md, memStorage());
    expect(r.facing).toBe("environment");
    expect(r.deviceId).toBeNull();
  });
});

describe("capture.ts integration", () => {
  it("startCamera opens the back camera and cameraFacing reports it", async () => {
    vi.resetModules();
    vi.doMock("../src/core/app", () => ({ app: { send: vi.fn(), speak: vi.fn(), earcon: vi.fn(), haptic: vi.fn(), settings: { lang: "en" } } }));
    const first = mkStream("user", "front");
    const second = mkStream("environment", "back0");
    const md = fakeMD({ first: first.stream, second: second.stream, devices: [dev("front", "camera2 1, facing front"), dev("back0", "camera2 0, facing back")] });
    Object.defineProperty(navigator, "mediaDevices", { value: md, configurable: true });
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const cap = await import("../src/camera/capture");
    expect(cap.cameraFacing()).toBe("unknown");
    const v = await cap.startCamera();
    expect(v.srcObject).toBe(second.stream);
    expect(cap.cameraFacing()).toBe("environment");
    expect(first.track.stop).toHaveBeenCalled();
    cap.stopCamera();
    expect(second.track.stop).toHaveBeenCalled();
    play.mockRestore();
  });
});
