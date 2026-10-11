import { describe, it, expect } from "vitest";
import { parseForecast, fetchForecast } from "../src/lib/forecast";

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
    expect(await fetchForecast("https://live.example", "demo", http500)).toBeNull();
    expect(await fetchForecast("https://live.example", "demo", netErr)).toBeNull();
    expect(await fetchForecast("https://live.example", "demo", badJson)).toBeNull();
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
    expect(await fetchForecast("https://live.example", "demo", hang, 20)).toBeNull();
  });
});
