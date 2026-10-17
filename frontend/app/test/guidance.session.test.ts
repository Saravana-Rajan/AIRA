// frontend/app/test/guidance.session.test.ts
import { describe, it, expect, vi } from "vitest";
import { createGuidanceSession } from "../src/guidance/session";

function setup() {
  let t = 0;
  let tipCb: ((tip: any) => void) | null = null;
  const sent: any[] = [];
  const said: string[] = [];
  const near: boolean[] = [];
  const stopTrack = vi.fn();
  const deps = {
    track: vi.fn(async (cb: any) => { tipCb = cb; return stopTrack; }),
    guide: vi.fn(),
    stopTone: vi.fn(),
    speak: (s: string) => { said.push(s); },
    send: (m: any) => { sent.push(m); return true; },
    lang: () => "en" as const,
    now: () => t,
    onNear: (n: boolean) => { near.push(n); },
  };
  const s = createGuidanceSession(deps);
  return {
    s, deps, sent, said, near, stopTrack,
    tip: (x: number, y: number | null, at: number) => { t = at; tipCb!(y === null ? null : { x1000: x, y1000: y }); },
  };
}

const target = {
  t: "target" as const, frameId: "f1", label: "Sakthi curd 1 L",
  point: [412, 530] as [number, number], box: [390, 505, 435, 555] as [number, number, number, number], confidence: 0.9,
};
const flush = () => new Promise((r) => setTimeout(r, 0));

describe("guidance session", () => {
  it("a target msg starts tracking once and drives guideTo with the [y,x] point", async () => {
    const g = setup();
    g.s.onServerMsg(target);
    await flush();
    expect(g.deps.track).toHaveBeenCalledTimes(1);
    g.tip(200, 412, 0);
    expect(g.deps.guide).toHaveBeenCalledWith([412, 530], { x1000: 200, y1000: 412 });
    expect(g.said).toEqual(["Right"]);
  });

  it("sends fingertip_in_target once after 500 ms in the box, despite a short flicker", async () => {
    const g = setup();
    g.s.onServerMsg(target); await flush();
    g.tip(530, 410, 0);
    g.tip(0, null, 80);           // flicker: tip lost for one frame
    g.tip(530, 410, 140);         // back within the 150 ms grace
    g.tip(530, 410, 520);         // >= 500 ms since entering => event
    g.tip(530, 410, 900);         // no second event
    const events = g.sent.filter((m) => m.t === "event");
    expect(events).toEqual([{ t: "event", name: "fingertip_in_target", data: { frameId: "f1", label: "Sakthi curd 1 L" } }]);
    expect(g.said).toContain("Stop");
  });

  it("a new target resets dwell so the event can fire again for it", async () => {
    const g = setup();
    g.s.onServerMsg(target); await flush();
    for (const t of [0, 120, 240, 360, 520]) g.tip(530, 410, t);          // continuous samples => event f1
    g.s.onServerMsg({ ...target, frameId: "f2" }); await flush();
    for (const t of [700, 820, 940, 1060, 1220]) g.tip(530, 410, t);      // => event f2
    expect(g.sent.filter((m) => m.t === "event").map((m) => m.data.frameId)).toEqual(["f1", "f2"]);
    expect(g.deps.track).toHaveBeenCalledTimes(1);   // tracking reused
  });

  it("done / interrupted cue and the server's guide-stream stop end guidance (stops tracking and tone)", async () => {
    const endings = [
      { t: "cue", cue: "done" }, { t: "cue", cue: "interrupted" },
      { t: "request_frame", purpose: "guide", stop: true },   // guidance.py stop(): the loop is over
    ] as const;
    for (const m of endings) {
      const g = setup();
      g.s.onServerMsg(target); await flush();
      g.s.onServerMsg(m);
      expect(g.stopTrack).toHaveBeenCalledTimes(1);
      expect(g.deps.stopTone).toHaveBeenCalled();
    }
  });

  it("other cues and messages do not end guidance", async () => {
    const g = setup();
    g.s.onServerMsg(target); await flush();
    g.s.onServerMsg({ t: "cue", cue: "thinking" });
    // `match` is spoken for an intermediate pick ("Take 1 more") while the loop keeps sending targets, and for a
    // cash match: neither ends hand guidance, so dwell state and tone survive between picks
    g.s.onServerMsg({ t: "cue", cue: "match" });
    g.s.onServerMsg({ t: "request_frame", purpose: "identify", stop: true });
    g.s.onServerMsg({ t: "request_frame", purpose: "guide", continuous: true });
    g.s.onServerMsg({ t: "transcript", role: "aira", text: "left", final: true });
    expect(g.stopTrack).not.toHaveBeenCalled();
  });

  it("a second target while tracking is still starting does not start a second tracker", async () => {
    const g = setup();
    g.s.onServerMsg(target);
    g.s.onServerMsg({ ...target, frameId: "f2" });
    await flush();
    expect(g.deps.track).toHaveBeenCalledTimes(1);
    for (const t of [0, 120, 240, 360, 520]) g.tip(530, 410, t);
    expect(g.sent.filter((m) => m.t === "event").map((m) => m.data.frameId)).toEqual(["f2"]);
  });

  it("end() before tracking has started stops the tracker as soon as it starts", async () => {
    const g = setup();
    g.s.onServerMsg(target);
    g.s.end();
    await flush();
    expect(g.stopTrack).toHaveBeenCalledTimes(1);
    g.tip(530, 410, 0);
    expect(g.deps.guide).not.toHaveBeenCalled();
    expect(g.sent).toEqual([]);
  });

  it("a failing tracker does not throw and a later target can retry", async () => {
    const g = setup();
    g.deps.track.mockRejectedValueOnce(new Error("no camera"));
    g.s.onServerMsg(target); await flush();
    g.s.onServerMsg({ ...target, frameId: "f2" }); await flush();
    expect(g.deps.track).toHaveBeenCalledTimes(2);
  });

  it("tips with no active target are ignored", async () => {
    const g = setup();
    g.s.onServerMsg(target); await flush();
    g.s.end();
    g.s._onTip({ x1000: 530, y1000: 410 });
    expect(g.deps.guide).not.toHaveBeenCalled();
  });

  it("reports when the hand is near the target (mutes the bump guard) and clears it on end", async () => {
    const g = setup();
    g.s.onServerMsg(target); await flush();
    g.tip(100, 900, 0);           // far away
    g.tip(530, 410, 100);         // inside the box
    g.tip(530, 410, 200);         // still near: no duplicate report
    g.tip(100, 900, 300);         // far again
    g.tip(530, 410, 400);
    g.s.end();
    expect(g.near).toEqual([true, false, true, false]);
  });
});
