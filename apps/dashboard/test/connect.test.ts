import { describe, expect, it, vi } from "vitest";
import { forecastLoader, lazyListen, liveHttpBase, publicEvidenceUrl } from "../src/lib/connect";

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("liveHttpBase", () => {
  it("prefers VITE_AIRA_LIVE_HTTP and trims trailing slashes", () => {
    expect(liveHttpBase({ VITE_AIRA_LIVE_HTTP: "https://aira-live.example.run.app/", VITE_AIRA_LIVE_WS: "wss://other/ws" })).toBe(
      "https://aira-live.example.run.app",
    );
  });
  it("derives the HTTP base from the app's WebSocket URL (deploy sets only VITE_AIRA_LIVE_WS)", () => {
    expect(liveHttpBase({ VITE_AIRA_LIVE_WS: "wss://aira-live-abc.a.run.app/ws" })).toBe("https://aira-live-abc.a.run.app");
    expect(liveHttpBase({ VITE_AIRA_LIVE_WS: "ws://localhost:8080/ws" })).toBe("http://localhost:8080");
  });
  it("returns null when nothing usable is configured", () => {
    expect(liveHttpBase({})).toBeNull();
    expect(liveHttpBase({ VITE_AIRA_LIVE_HTTP: "  ", VITE_AIRA_LIVE_WS: "not a url" })).toBeNull();
    expect(liveHttpBase({ VITE_AIRA_LIVE_WS: "ftp://x/ws" })).toBeNull();
  });
});

describe("forecastLoader", () => {
  it("fetches GET {base}/api/forecast?shop= when aira-live is configured", async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ items: [] }), { status: 200 }));
    const load = forecastLoader({ VITE_AIRA_LIVE_WS: "wss://live.example/ws" }, fetchImpl as unknown as typeof fetch);
    await expect(load("murugan_dairy")).resolves.toEqual([]);
    expect((fetchImpl.mock.calls[0] as unknown[])[0]).toBe("https://live.example/api/forecast?shop=murugan_dairy");
  });
  it("resolves null without any request when aira-live is not configured", async () => {
    const fetchImpl = vi.fn();
    await expect(forecastLoader({}, fetchImpl as unknown as typeof fetch)("murugan_dairy")).resolves.toBeNull();
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});

describe("lazyListen", () => {
  it("loads the Firestore module on demand and forwards data, status and stop", async () => {
    const innerStop = vi.fn();
    const listenShop = vi.fn((_shop: string, onChange: (d: unknown) => void, _now: () => number, onStatus?: (s: string) => void) => {
      onStatus?.("live");
      onChange({ products: [] });
      return innerStop;
    });
    const onChange = vi.fn();
    const onStatus = vi.fn();
    const stop = lazyListen(async () => ({ listenShop }) as never)("murugan_dairy", onChange, onStatus);
    await flush();
    expect(listenShop.mock.calls[0][0]).toBe("murugan_dairy");
    expect(onStatus).toHaveBeenCalledWith("live");
    expect(onChange).toHaveBeenCalledWith({ products: [] });
    stop();
    expect(innerStop).toHaveBeenCalledTimes(1);
  });

  it("never subscribes when stopped before the module has loaded", async () => {
    const listenShop = vi.fn(() => () => {});
    const stop = lazyListen(async () => ({ listenShop }) as never)("murugan_dairy", vi.fn(), vi.fn());
    stop();
    await flush();
    expect(listenShop).not.toHaveBeenCalled();
  });

  it("reports offline when Firebase cannot start (missing config, failed chunk load)", async () => {
    const onStatus = vi.fn();
    lazyListen(async () => {
      throw new Error("projectId not provided");
    })("murugan_dairy", vi.fn(), onStatus);
    await flush();
    expect(onStatus).toHaveBeenLastCalledWith("offline");

    const onStatus2 = vi.fn();
    lazyListen(async () => ({ listenShop: () => { throw new Error("bad config"); } }) as never)("murugan_dairy", vi.fn(), onStatus2);
    await flush();
    expect(onStatus2).toHaveBeenLastCalledWith("offline");
  });
});

describe("publicEvidenceUrl", () => {
  it("links only public https evidence; private bucket objects stay private (no Storage, no Auth)", async () => {
    await expect(publicEvidenceUrl("https://example.test/e.jpg")).resolves.toBe("https://example.test/e.jpg");
    await expect(publicEvidenceUrl("gs://aira-evidence/x.jpg")).resolves.toBeNull();
    await expect(publicEvidenceUrl("javascript:alert(1)")).resolves.toBeNull();
  });
});
