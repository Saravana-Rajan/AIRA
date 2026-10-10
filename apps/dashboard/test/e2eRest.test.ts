// @vitest-environment node
// The e2e test seeds the Firestore emulator over its REST API (no firebase-admin needed).
import { describe, expect, it, vi } from "vitest";
import {
  clearDatabase, commitDocs, docName, E2E_DATABASE, E2E_PROJECT, emulatorReachable, parseEmulatorHost, toFields, toValue,
} from "./e2e/firestoreRest.mjs";

describe("parseEmulatorHost", () => {
  it("reads host:port as set by firebase emulators:exec", () => {
    expect(parseEmulatorHost("127.0.0.1:8085")).toEqual({ host: "127.0.0.1", port: 8085, hostPort: "127.0.0.1:8085" });
    expect(parseEmulatorHost(" localhost:9000 ")).toEqual({ host: "localhost", port: 9000, hostPort: "localhost:9000" });
    expect(parseEmulatorHost("[::1]:8085")).toEqual({ host: "::1", port: 8085, hostPort: "[::1]:8085" });
  });

  it("returns null when it is missing or unreadable", () => {
    for (const v of [undefined, "", "localhost", "localhost:abc", "localhost:0", "localhost:70000"]) {
      expect(parseEmulatorHost(v)).toBeNull();
    }
  });
});

describe("toValue / toFields", () => {
  it("encodes scalars as Firestore REST values", () => {
    expect(toValue("curd")).toEqual({ stringValue: "curd" });
    expect(toValue(28)).toEqual({ integerValue: "28" });
    expect(toValue(0.93)).toEqual({ doubleValue: 0.93 });
    expect(toValue(true)).toEqual({ booleanValue: true });
    expect(toValue(null)).toEqual({ nullValue: null });
  });

  it("encodes a Date as a Timestamp", () => {
    expect(toValue(new Date(Date.UTC(2026, 9, 10, 6, 30)))).toEqual({ timestampValue: "2026-10-10T06:30:00.000Z" });
  });

  it("encodes arrays and nested maps", () => {
    expect(toValue([{ batchId: "b1", qty: 28 }, "x"])).toEqual({
      arrayValue: {
        values: [
          { mapValue: { fields: { batchId: { stringValue: "b1" }, qty: { integerValue: "28" } } } },
          { stringValue: "x" },
        ],
      },
    });
    expect(toValue([])).toEqual({ arrayValue: { values: [] } });
    expect(toValue({})).toEqual({ mapValue: { fields: {} } });
  });

  it("leaves out undefined fields", () => {
    expect(toFields({ a: 1, b: undefined })).toEqual({ a: { integerValue: "1" } });
  });

  it("refuses values Firestore cannot store", () => {
    expect(() => toValue(Number.NaN)).toThrow();
    expect(() => toValue(() => 1)).toThrow();
  });
});

describe("emulator REST calls", () => {
  const target = { emulatorHost: "127.0.0.1:8085", projectId: E2E_PROJECT, databaseId: E2E_DATABASE };

  it("targets the demo project and the aira database", () => {
    expect(E2E_PROJECT).toMatch(/^demo-/); // demo-* projects never reach a real Firebase project
    expect(E2E_DATABASE).toBe("aira");
    expect(docName("demo-aira", "aira", "shops/murugan_dairy")).toBe(
      "projects/demo-aira/databases/aira/documents/shops/murugan_dairy",
    );
  });

  it("refuses a path that is not a document", () => {
    expect(() => docName("demo-aira", "aira", "shops")).toThrow();
    expect(() => docName("demo-aira", "aira", "shops//x")).toThrow();
  });

  it("commits every doc in one request, as the emulator owner (rules bypassed for seeding only)", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 200 }));
    await commitDocs({
      ...target,
      docs: [
        { path: "shops/murugan_dairy", data: { name: "Murugan Dairy" } },
        { path: "shops/murugan_dairy/inventory/sakthi_curd_1l", data: { onHand: 28 } },
      ],
      fetchImpl,
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8085/v1/projects/demo-aira/databases/aira/documents:commit");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer owner");
    const body = JSON.parse(String(init.body));
    expect(body.writes).toEqual([
      { update: { name: "projects/demo-aira/databases/aira/documents/shops/murugan_dairy", fields: { name: { stringValue: "Murugan Dairy" } } } },
      {
        update: {
          name: "projects/demo-aira/databases/aira/documents/shops/murugan_dairy/inventory/sakthi_curd_1l",
          fields: { onHand: { integerValue: "28" } },
        },
      },
    ]);
  });

  it("reports a failed commit with the emulator's message", async () => {
    const fetchImpl = vi.fn(async () => new Response("bad path", { status: 400 }));
    await expect(commitDocs({ ...target, docs: [{ path: "shops/x", data: {} }], fetchImpl })).rejects.toThrow(/400.*bad path/);
  });

  it("clears only the emulator's aira database", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 200 }));
    await clearDatabase({ ...target, fetchImpl });
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8085/emulator/v1/projects/demo-aira/databases/aira/documents");
    expect(init.method).toBe("DELETE");
  });

  it("refuses to clear anything that does not look like a local emulator", async () => {
    const fetchImpl = vi.fn(async () => new Response("{}", { status: 200 }));
    await expect(clearDatabase({ ...target, emulatorHost: "firestore.googleapis.com:443", fetchImpl })).rejects.toThrow(/emulator/i);
    await expect(clearDatabase({ ...target, projectId: "my-real-project", fetchImpl })).rejects.toThrow(/demo-/);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("checks whether the emulator answers", async () => {
    expect(await emulatorReachable("127.0.0.1:8085", vi.fn(async () => new Response("Ok", { status: 200 })))).toBe(true);
    expect(await emulatorReachable("127.0.0.1:8085", vi.fn(async () => { throw new TypeError("fetch failed"); }))).toBe(false);
    expect(await emulatorReachable(undefined)).toBe(false);
  });
});
