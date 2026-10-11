import { afterEach, describe, it, expect, vi } from "vitest";
import { parseForecast, fetchForecast, FORECAST_RETRY } from "../src/lib/forecast";

/** Three quick tries with no pauses, so failure tests stay fast. */
const FAST = { timeoutsMs: [50, 50, 50], backoffMs: [0, 0] };

const good = { items: [{ sku: "sakthi_curd_1l", name: "Sakthi curd 1 L", dailyDemand: 26.4, onHand: 12, suggestedOrder: 30 }] };

describe("parseForecast", () => {
  it("accepts the documented shape", () => {
    expect(parseForecast(good)).toEqual({ items: good.items, demoHistory: false });
  });
  it("carries the demo-history flag (backfilled demo sales feed the numbers)", () => {
    expect(parseForecast({ ...good, demoHistory: true })).toEqual({ items: good.items, demoHistory: true });
    expect(parseForecast({ ...good, demoHistory: "yes" })?.demoHistory).toBe(false);
  });
  it("rejects malformed payloads", () => {
    expect(parseForecast(null)).toBeNull();
    expect(parseForecast({ items: "nope" })).toBeNull();
    expect(parseForecast({ items: [{ sku: "x", name: "X", dailyDemand: "lots", onHand: 1, suggestedOrder: 2 }] })).toBeNull();
  });
});

describe("fetchForecast", () => {
  it("returns items on 200", async () => {
    const f = (async () => new Response(JSON.stringify(good), { status: 200 })) as typeof fetch;
    expect(await fetchForecast("https://live.example", "demo", f)).toEqual({ items: good.items, demoHistory: false });
  });
  it("returns null on HTTP error, network error or bad JSON", async () => {
    const http500 = (async () => new Response("boom", { status: 500 })) as typeof fetch;
    const netErr = (async () => { throw new TypeError("fetch failed"); }) as typeof fetch;
    const badJson = (async () => new Response("<html>", { status: 200 })) as typeof fetch;
    expect(await fetchForecast("https://live.example", "demo", http500, FAST)).toBeNull();
    expect(await fetchForecast("https://live.example", "demo", netErr, FAST)).toBeNull();
    expect(await fetchForecast("https://live.example", "demo", badJson, FAST)).toBeNull();
  });
  it("calls the documented URL with the shop id encoded", async () => {
    let seen = "";
    const f = (async (url: RequestInfo | URL) => { seen = String(url); return new Response(JSON.stringify(good)); }) as typeof fetch;
    await fetchForecast("https://live.example/", "my shop", f);
    expect(seen).toBe("https://live.example/api/forecast?shop=my%20shop");
  });
  it("gives up with null when the endpoint hangs past the timeout", async () => {
    const hang = ((_url: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      })) as typeof fetch;
    expect(await fetchForecast("https://live.example", "demo", hang, { timeoutsMs: [20], backoffMs: [] })).toBeNull();
  });
});

// The forecast server scales to zero (Cloud Run, min-instances 0). The first request of the day wakes it,
// which takes longer than 5 s; a second request a moment later answers in about 0.2 s.
describe("fetchForecast on a cold server", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  /** A fetch that answers after delayMs, or rejects like the browser does when the request is cancelled. */
  const answersAfter = (delayMs: number, body: unknown = good) =>
    ((_url: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((resolve, reject) => {
        const t = setTimeout(() => resolve(new Response(JSON.stringify(body), { status: 200 })), delayMs);
        init?.signal?.addEventListener("abort", () => {
          clearTimeout(t);
          reject(new DOMException("aborted", "AbortError"));
        });
      })) as typeof fetch;

  it("waits about 15 s on the first try, long enough for the server to wake", async () => {
    vi.useFakeTimers();
    const slow = vi.fn(answersAfter(12_000));
    const result = fetchForecast("https://live.example", "demo", slow);
    await vi.advanceTimersByTimeAsync(12_000);
    expect(await result).toEqual({ items: good.items, demoHistory: false });
    expect(slow).toHaveBeenCalledTimes(1);
    expect(FORECAST_RETRY.timeoutsMs[0]).toBeGreaterThanOrEqual(15_000);
  });

  it("tries again after a timeout and shows the second answer", async () => {
    vi.useFakeTimers();
    const calls: number[] = [];
    const fetchImpl = vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
      calls.push(Date.now());
      return calls.length === 1 ? answersAfter(60_000)(url, init) : answersAfter(200)(url, init);
    }) as unknown as typeof fetch;
    const result = fetchForecast("https://live.example", "demo", fetchImpl);
    await vi.advanceTimersByTimeAsync(FORECAST_RETRY.timeoutsMs[0] + FORECAST_RETRY.backoffMs[0] + 200);
    expect(await result).toEqual({ items: good.items, demoHistory: false });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    // a short pause between tries, not a hammering loop
    expect(calls[1] - calls[0]).toBe(FORECAST_RETRY.timeoutsMs[0] + FORECAST_RETRY.backoffMs[0]);
  });

  it("retries a network error or a 5xx a couple of times with a short back-off, then gives up", async () => {
    vi.useFakeTimers();
    const at: number[] = [];
    const down = vi.fn(async () => {
      at.push(Date.now());
      throw new TypeError("fetch failed");
    }) as unknown as typeof fetch;
    const result = fetchForecast("https://live.example", "demo", down);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await result).toBeNull();
    expect(down).toHaveBeenCalledTimes(FORECAST_RETRY.timeoutsMs.length);
    expect(FORECAST_RETRY.timeoutsMs.length).toBeGreaterThanOrEqual(2);
    expect(at.slice(1).map((t, i) => t - at[i])).toEqual(FORECAST_RETRY.backoffMs);
    for (const ms of FORECAST_RETRY.backoffMs) expect(ms).toBeLessThanOrEqual(3_000);

    const unavailable = vi.fn(async () => new Response("waking", { status: 503 })) as unknown as typeof fetch;
    const r503 = fetchForecast("https://live.example", "demo", unavailable);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(await r503).toBeNull();
    expect(unavailable).toHaveBeenCalledTimes(FORECAST_RETRY.timeoutsMs.length);
  });

  it("does not retry an answer that will never change (malformed JSON, 404)", async () => {
    const badJson = vi.fn(async () => new Response("<html>", { status: 200 })) as unknown as typeof fetch;
    const notFound = vi.fn(async () => new Response("no", { status: 404 })) as unknown as typeof fetch;
    expect(await fetchForecast("https://live.example", "demo", badJson)).toBeNull();
    expect(await fetchForecast("https://live.example", "demo", notFound)).toBeNull();
    expect(badJson).toHaveBeenCalledTimes(1);
    expect(notFound).toHaveBeenCalledTimes(1);
  });
});
