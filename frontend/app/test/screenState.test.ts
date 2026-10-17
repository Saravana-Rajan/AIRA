import { describe, it, expect } from "vitest";
import type { ServerMsg } from "@aira/contracts";
import { initialScreen, reduceScreen, type ScreenInput, type ScreenState } from "../src/ui/screenState";

const run = (...inputs: ScreenInput[]): ScreenState => inputs.reduce(reduceScreen, initialScreen);
const ready: ServerMsg = { t: "ready", sessionId: "s1", resume: "r1", mock: true };

describe("initialScreen", () => {
  it("starts connecting, with no captions and no task", () => {
    expect(initialScreen).toEqual({
      status: { kind: "connecting", label: "Connecting" },
      userText: "",
      airaText: "",
      task: null,
    });
  });
});

describe("reduceScreen: status chip", () => {
  it("ready message shows Ready", () => {
    expect(run(ready).status).toEqual({ kind: "ready", label: "Ready" });
  });

  it("user transcript: partial is Listening, final is Thinking", () => {
    const partial = run(ready, { t: "transcript", role: "user", text: "2 litres", final: false });
    expect(partial.status).toEqual({ kind: "listening", label: "Listening" });
    expect(partial.userText).toBe("2 litres");
    const final = reduceScreen(partial, { t: "transcript", role: "user", text: "2 litres curd", final: true });
    expect(final.status).toEqual({ kind: "thinking", label: "Thinking" });
    expect(final.userText).toBe("2 litres curd");
  });

  it("an unclear user transcript (misheard language) is marked in the caption", () => {
    const s = run(ready, { t: "transcript", role: "user", text: "¿Cuántos años tienes?", final: true, lang: "en", unclear: true });
    expect(s.userText).toBe("¿Cuántos años tienes? (not clear)");
    expect(run(ready, { t: "transcript", role: "user", text: "two curd", final: true, lang: "en" }).userText).toBe("two curd");
  });

  it("AIRA transcript fills the AIRA caption and returns to Ready", () => {
    const s = run(ready, { t: "cue", cue: "thinking" }, { t: "transcript", role: "aira", text: "Sakthi curd 1 L, ₹60 each.", final: true });
    expect(s.airaText).toBe("Sakthi curd 1 L, ₹60 each.");
    expect(s.status.kind).toBe("ready");
  });

  it("maps cues to states", () => {
    expect(run(ready, { t: "cue", cue: "thinking" }).status).toEqual({ kind: "thinking", label: "Thinking" });
    expect(run(ready, { t: "cue", cue: "warn" }).status).toEqual({ kind: "warning", label: "Warning" });
    expect(run(ready, { t: "cue", cue: "mismatch" }).status).toEqual({ kind: "warning", label: "Mismatch" });
    expect(run(ready, { t: "cue", cue: "unsure" }).status).toEqual({ kind: "warning", label: "Not sure" });
    expect(run(ready, { t: "cue", cue: "offline" }).status).toEqual({ kind: "warning", label: "Offline" });
    expect(run(ready, { t: "cue", cue: "reconnect" }).status).toEqual({ kind: "connecting", label: "Reconnecting" });
    expect(run(ready, { t: "cue", cue: "thinking" }, { t: "cue", cue: "done" }).status.kind).toBe("ready");
    expect(run(ready, { t: "cue", cue: "thinking" }, { t: "cue", cue: "interrupted" }).status.kind).toBe("ready");
  });

  it("ack cue does not change the screen", () => {
    const s = run(ready, { t: "cue", cue: "thinking" });
    expect(reduceScreen(s, { t: "cue", cue: "ack" })).toBe(s);
  });

  it("local gestures: talk is Listening, stop is Ready", () => {
    expect(run(ready, { t: "local", event: "talk" }).status).toEqual({ kind: "listening", label: "Listening" });
    expect(run(ready, { t: "local", event: "talk" }, { t: "local", event: "stop" }).status).toEqual({ kind: "ready", label: "Ready" });
  });

  it("bump guard shows Stop! and stays until cleared", () => {
    const bumped = run(ready, { t: "local", event: "bump" });
    expect(bumped.status).toEqual({ kind: "stop", label: "Stop!" });
    const stillBumped = run(
      ready,
      { t: "local", event: "bump" },
      { t: "transcript", role: "user", text: "what", final: false },
      { t: "cue", cue: "thinking" },
    );
    expect(stillBumped.status.kind).toBe("stop");
    expect(reduceScreen(bumped, { t: "local", event: "bump_clear" }).status).toEqual({ kind: "ready", label: "Ready" });
  });

  it("local offline shows Offline as a warning", () => {
    expect(run(ready, { t: "local", event: "offline" }).status).toEqual({ kind: "warning", label: "Offline" });
  });

  it("errors show a warning and put the spoken text in the AIRA caption", () => {
    const net = run(ready, { t: "error", code: "network", say: "No internet. I will keep trying." });
    expect(net.status).toEqual({ kind: "warning", label: "Offline" });
    expect(net.airaText).toBe("No internet. I will keep trying.");
    expect(run(ready, { t: "error", code: "camera", say: "Camera blocked." }).status).toEqual({ kind: "warning", label: "Warning" });
  });
});

describe("reduceScreen: counting", () => {
  it("live count shows Counting n / expected and a delivery card", () => {
    const s = run(ready, { t: "count", sku: "sakthi_curd_1l", counted: 12, expected: 30, final: false, confidence: 0.9 });
    expect(s.status).toEqual({ kind: "counting", label: "Counting 12 / 30" });
    expect(s.task).toEqual({ title: "Delivery", detail: "12 / 30 counted", tone: "neutral" });
  });

  it("count without an expected number shows just the count", () => {
    const s = run(ready, { t: "count", sku: "aavin_milk_1l", counted: 7, expected: null, final: false, confidence: 0.8 });
    expect(s.status.label).toBe("Counting 7");
    expect(s.task?.detail).toBe("7 counted");
  });

  it("final count that matches is done", () => {
    const s = run(ready, { t: "count", sku: "sakthi_curd_1l", counted: 30, expected: 30, final: true, confidence: 0.95 });
    expect(s.status.kind).toBe("ready");
    expect(s.task).toEqual({ title: "Delivery", detail: "30 / 30 counted", tone: "ok" });
  });

  it("final count that is short is a warning", () => {
    const s = run(ready, { t: "count", sku: "sakthi_curd_1l", counted: 28, expected: 30, final: true, confidence: 0.95 });
    expect(s.status).toEqual({ kind: "warning", label: "2 short" });
    expect(s.task).toEqual({ title: "Delivery", detail: "28 / 30 counted", tone: "warn" });
  });

  it("final count that is over is a warning", () => {
    const s = run(ready, { t: "count", sku: "sakthi_curd_1l", counted: 31, expected: 30, final: true, confidence: 0.95 });
    expect(s.status).toEqual({ kind: "warning", label: "1 extra" });
  });

  it("transcripts do not hide a live count", () => {
    const s = run(
      ready,
      { t: "count", sku: "sakthi_curd_1l", counted: 12, expected: 30, final: false, confidence: 0.9 },
      { t: "transcript", role: "aira", text: "12", final: true },
    );
    expect(s.status.label).toBe("Counting 12 / 30");
    expect(s.airaText).toBe("12");
  });
});

describe("reduceScreen: task card", () => {
  it("state messages become the task card, titled by entity", () => {
    const sale = run(ready, { t: "state", entity: "sale", id: "s1", state: "SALE_PENDING", summary: "2 × Sakthi curd 1 L — ₹120" });
    expect(sale.task).toEqual({ title: "Sale", detail: "2 × Sakthi curd 1 L — ₹120", tone: "neutral" });
    const stock = run(ready, { t: "state", entity: "inventory", id: "i1", state: "LOW", summary: "Sakthi curd 1 L: 2 left" });
    expect(stock.task?.title).toBe("Stock");
  });

  it("tone follows the state", () => {
    const tone = (entity: "order" | "delivery" | "sale" | "payment", state: string) =>
      run(ready, { t: "state", entity, id: "x", state, summary: "s" }).task?.tone;
    expect(tone("order", "ORDER_PLACED")).toBe("ok");
    expect(tone("sale", "SALE_COMPLETED")).toBe("ok");
    expect(tone("payment", "CONFIRMED")).toBe("ok");
    expect(tone("delivery", "DELIVERY_RECONCILED")).toBe("ok");
    expect(tone("order", "DISCREPANCY")).toBe("danger");
    expect(tone("payment", "FAILED")).toBe("danger");
    expect(tone("sale", "SALE_VOID")).toBe("danger");
    expect(tone("order", "DELIVERY_PENDING")).toBe("neutral");
  });

  it("OPEN_URL hand-offs are not shown as a task", () => {
    const s = run(ready);
    expect(reduceScreen(s, { t: "state", entity: "order", id: "o1", state: "ORDER_PLACED", summary: "OPEN_URL https://wa.me/91" })).toBe(s);
  });

  it("confirm shows the prompt and waits for yes or no", () => {
    const s = run(ready, { t: "confirm", id: "c1", kind: "order", prompt: "Order 30 L Sakthi curd from Sakthi vendor?" });
    expect(s.task).toEqual({ title: "Confirm order", detail: "Order 30 L Sakthi curd from Sakthi vendor?", tone: "neutral" });
    expect(s.airaText).toBe("Order 30 L Sakthi curd from Sakthi vendor?");
    expect(s.status).toEqual({ kind: "listening", label: "Yes or no?" });
    expect(run(ready, { t: "confirm", id: "c2", kind: "generic", prompt: "Shall I?" }).task?.title).toBe("Confirm");
  });
});

describe("reduceScreen: ignored messages", () => {
  it("audio, request_frame and target leave the state untouched", () => {
    const s = run(ready);
    expect(reduceScreen(s, { t: "audio", pcm24: "AAAA" })).toBe(s);
    expect(reduceScreen(s, { t: "request_frame", purpose: "count" })).toBe(s);
    expect(reduceScreen(s, { t: "target", frameId: "f", label: "curd", point: [500, 500], confidence: 0.9 })).toBe(s);
  });
});
