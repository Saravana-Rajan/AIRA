// Types for firestoreRest.mjs (plain ESM so Node and Playwright load it without a build step).
import type { SeedDoc } from "./demoShop.mjs";

export declare const E2E_PROJECT: string;
export declare const E2E_DATABASE: "aira";

export type FirestoreValue =
  | { nullValue: null }
  | { booleanValue: boolean }
  | { integerValue: string }
  | { doubleValue: number }
  | { stringValue: string }
  | { timestampValue: string }
  | { arrayValue: { values: FirestoreValue[] } }
  | { mapValue: { fields: Record<string, FirestoreValue> } };

type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface EmulatorTarget { emulatorHost: string; projectId: string; databaseId: string; fetchImpl?: FetchLike }

export declare function parseEmulatorHost(value: string | undefined | null): { host: string; port: number; hostPort: string } | null;
export declare function toValue(v: unknown): FirestoreValue;
export declare function toFields(obj: Record<string, unknown>): Record<string, FirestoreValue>;
export declare function docName(projectId: string, databaseId: string, path: string): string;
export declare function commitDocs(args: EmulatorTarget & { docs: SeedDoc[] }): Promise<void>;
export declare function clearDatabase(args: EmulatorTarget): Promise<void>;
export declare function emulatorReachable(emulatorHost: string | undefined | null, fetchImpl?: FetchLike, timeoutMs?: number): Promise<boolean>;
